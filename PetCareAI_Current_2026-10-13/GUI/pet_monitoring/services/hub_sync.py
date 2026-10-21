"""Mirror phone details and synchronise sensor saves through the trusted Pi hub.

Sensor saves from either UI are ordered by the hub. Desktop changes remain pending
until acknowledged; stale refresh snapshots cannot roll them back.
"""
import uuid
from dataclasses import replace
from datetime import date, datetime
from PySide6.QtCore import QObject, Signal, Slot


class HubSync(QObject):
    synced = Signal()
    outgoing_changed = Signal()

    def __init__(self, controller, hub, receiver, parent=None, change_log=None):
        super().__init__(parent)
        self.c, self.hub = controller, hub
        self.change_log = change_log
        # Last synced extras the desktop models have no field for (sex, birthday, locations, pins).
        self.state = controller.db.get("hub_sync", None)
        self.pending = controller.db.get("settings_outbox", [])
        self.version = controller.db.get("settings_sync_version", -1)
        hub.settings_saved.connect(self.queue_save)
        receiver.received.connect(self.apply)

    def queue_save(self, patch):
        operation = {"type": "DASHBOARD_SETTINGS", "operation_id": uuid.uuid4().hex, **patch}
        self.pending.append(operation)
        self.c.db.set("settings_outbox", self.pending)
        self.hub.feedback = "Saved locally; waiting for phone/Pi settings confirmation."
        self.outgoing_changed.emit()

    def acknowledge(self, operation_id, snapshot):
        if not any(p["operation_id"] == operation_id for p in self.pending):
            return
        self.pending = [p for p in self.pending if p["operation_id"] != operation_id]
        self.c.db.set("settings_outbox", self.pending)
        self.apply(snapshot)
        if not self.pending:
            self.hub.feedback = "Sensor settings synchronised with the phone app."
            self.hub.changed.emit()

    @Slot(dict)
    def apply(self, message):
        if message.get("type") != "HUB_SYNC":
            return
        version = message.get("settings_version")
        if self.version >= 0 and version is None:
            return  # Unversioned legacy data cannot overwrite ordered settings.
        if version is not None and version < self.version:
            return  # An older stdout snapshot must not overwrite an acknowledged save.
        if version is not None:
            self.version = version
            self.c.db.set("settings_sync_version", version)
        pet, sensors = message["pet"], message["sensors"]
        if self.change_log is not None:
            self.change_log.settings(message)

        profile = self.c.profile
        updated = replace(profile, name=pet["name"], animal_type=pet["animal"] or profile.animal_type,
                          breed=pet["breed"] or profile.breed, baseline_sleep=pet["baseline_sleep_hours"],
                          baseline_litter_visits=pet["baseline_toilet_visits"])
        if pet["birthday"]:
            updated = replace(updated, age=round(max(.1, (date.today() - date.fromisoformat(pet["birthday"])).days / 365.25), 1))
        if pet["weight_kg"]:
            updated = replace(updated, weight=pet["weight_kg"])
        if updated != profile:
            self.c.save_profile(updated)

        zones = {key: value for key, value in sensors.items() if key != "5"}
        mapping = {key: value["role"] for key, value in zones.items()}
        thresholds = {key: float(value["threshold_cm"]) for key, value in zones.items()}
        config_pending = any("configuration" in p for p in self.pending)
        if not config_pending and (mapping != self.hub.mapping or thresholds != {k: float(v) for k, v in self.hub.thresholds.items()}):
            self.hub.apply_remote_configuration(mapping, thresholds)

        # Do not let a repeated/stale phone snapshot undo a desktop calibration.
        # A genuinely changed phone value can supersede the saved local value.
        previous = self.c.db.get("hub_remote_calibration", {})
        overrides = self.c.db.get("level_calibration_overrides", {})
        incoming = {key: value["calibration"] for key, value in zones.items()}
        calibration = dict(self.hub.level_calibration)
        pending_calibration = {key for p in self.pending for key in p.get("calibration", {})}
        for key, remote_value in incoming.items():
            if key in pending_calibration:
                continue
            override = overrides.get(key)
            if override is not None:
                if not override["baseline_known"]:
                    override.update(baseline_known=True, baseline=remote_value)
                    continue
                if remote_value == override["baseline"]:
                    continue
                del overrides[key]
            elif key in previous and previous[key] == remote_value:
                continue
            elif key not in previous and remote_value is None and key in calibration:
                # Preserve existing desktop calibration during first-contact migration.
                overrides[key] = {"baseline_known": True, "baseline": None}
                continue
            if remote_value:
                kept = calibration.get(key, {"capacity_l": 0.0, "linear_volume": False})
                calibration[key] = {**kept, **remote_value}
            else:
                calibration.pop(key, None)
        self.c.db.set("hub_remote_calibration", incoming)
        self.c.db.set("level_calibration_overrides", overrides)
        if calibration != self.hub.level_calibration:
            self.hub.apply_remote_calibration(calibration)

        self.state = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "sex": pet["sex"] or "",
                      "birthday": pet["birthday"] or "", "pets_total": message["pets_total"],
                      "locations": {key: value["location"] for key, value in sensors.items()},
                      "pins": {key: value["pins"] for key, value in sensors.items()}}
        self.c.db.set("hub_sync", self.state)
        self.synced.emit()
