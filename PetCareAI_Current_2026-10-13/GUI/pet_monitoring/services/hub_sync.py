"""Apply the Pi hub's pet details and hardware setup to this dashboard (one way: hub -> desktop).

The hub is the master copy: owners edit pets, sensor roles, thresholds, calibration and sensor
locations in the phone app, and every connected dashboard follows within a second.
"""
from dataclasses import replace
from datetime import date, datetime
from PySide6.QtCore import QObject, Signal, Slot


class HubSync(QObject):
    synced = Signal()

    def __init__(self, controller, hub, receiver, parent=None):
        super().__init__(parent)
        self.c, self.hub = controller, hub
        # Last synced extras the desktop models have no field for (sex, birthday, locations, pins).
        self.state = controller.db.get("hub_sync", None)
        receiver.received.connect(self.apply)

    @Slot(dict)
    def apply(self, message):
        if message.get("type") != "HUB_SYNC":
            return
        pet, sensors = message["pet"], message["sensors"]

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
        if mapping != self.hub.mapping or thresholds != {k: float(v) for k, v in self.hub.thresholds.items()}:
            self.hub.apply_remote_configuration(mapping, thresholds)

        # Full/empty distances come from the hub; laptop-only capacity and volume options are kept.
        calibration = dict(self.hub.level_calibration)
        for key, value in zones.items():
            if value["calibration"]:
                kept = calibration.get(key, {"capacity_l": 0.0, "linear_volume": False})
                calibration[key] = {**kept, **value["calibration"]}
            else:
                calibration.pop(key, None)
        if calibration != self.hub.level_calibration:
            self.hub.apply_remote_calibration(calibration)

        self.state = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "sex": pet["sex"] or "",
                      "birthday": pet["birthday"] or "", "pets_total": message["pets_total"],
                      "locations": {key: value["location"] for key, value in sensors.items()},
                      "pins": {key: value["pins"] for key, value in sensors.items()}}
        self.c.db.set("hub_sync", self.state)
        self.synced.emit()
