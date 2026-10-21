"""Four configurable zone inputs and one fixed gesture input on a single Pi."""
import math
import time
from datetime import datetime, timedelta
from PySide6.QtCore import QObject, QTimer, Signal, Slot

MODES = {"eat": "Food level", "sleep": "Sleeping", "water": "Water level", "toilet": "Toilet"}
DEFAULT_MAPPING = {"1": "eat", "2": "sleep", "3": "water", "4": "toilet"}
MODE_THRESHOLDS = {"eat": 20.0, "water": 20.0, "sleep": 14.0, "toilet": 13.0}


class HubController(QObject):
    changed = Signal()
    before_activity_change = Signal()
    settings_saved = Signal(dict)

    def __init__(self, controller, receiver):
        super().__init__(controller)
        self.c, self.receiver = controller, receiver
        saved = self.c.db.get("hub_configuration", {})
        self.mapping = saved.get("mapping", DEFAULT_MAPPING.copy())
        self.thresholds = saved.get("thresholds", {key: MODE_THRESHOLDS[mode] for key, mode in self.mapping.items()})
        self.revision = saved.get("revision", 1)
        self.mode = self.c.db.get("hub_mode", "Demo")
        self.acknowledged = None
        self.feedback = ""
        self.level_calibration = self.c.db.get("level_calibration", {})
        self._last_level_save = {}
        self.connection = "Receiver stopped"
        self.states = {key: {"distance": None, "presence": False, "seen": 0, "started": None, "updated": "—"} for key in DEFAULT_MAPPING}
        self.c.db.execute("CREATE TABLE IF NOT EXISTS hub_visits (event_id TEXT, source TEXT, timestamp TEXT, sensor_id TEXT, mode TEXT, duration REAL, revision INTEGER, PRIMARY KEY(event_id,source))")
        self.c.db.execute("CREATE TABLE IF NOT EXISTS hub_activity_daily (day TEXT, source TEXT, mode TEXT, seconds REAL, PRIMARY KEY(day,source,mode))")
        self.c.db.execute("CREATE TABLE IF NOT EXISTS hub_levels (timestamp TEXT, source TEXT, sensor_id TEXT, mode TEXT, distance REAL, percent REAL, litres REAL)")
        if not saved.get("lab1_thresholds_applied"):
            changed = False
            for key, mode in self.mapping.items():
                if mode in {"sleep", "toilet"}:
                    changed |= self.thresholds[key] != MODE_THRESHOLDS[mode]
                    self.thresholds[key] = MODE_THRESHOLDS[mode]
            self.revision += int(changed)
            self.c.db.set("hub_configuration", {"mapping": self.mapping, "thresholds": self.thresholds, "revision": self.revision, "lab1_thresholds_applied": True})
        self._activity_clock = time.monotonic()
        self._sessions = {"sleep": 0.0, "toilet": 0.0}
        self.receiver.received.connect(self.process)
        self.receiver.status.connect(self.connection_changed)
        self._demo_tick = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.publish_configuration()

    def publish_configuration(self):
        self.receiver.set_configuration({"type": "SET_CONFIG", "revision": self.revision,
            "mapping": self.mapping.copy(), "thresholds_cm": self.thresholds.copy(),
            "gesture_sensor": 5})

    def save_configuration(self, mapping, thresholds):
        if not self.c.auth.is_authenticated:
            self.feedback = "Sign in before changing sensor assignments."
            self.changed.emit()
            return False
        if set(mapping) != set(DEFAULT_MAPPING) or any(value not in MODES for value in mapping.values()):
            raise ValueError("Assign a valid pet mode to Sensors 1–4")
        if set(thresholds) != set(DEFAULT_MAPPING) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 1 <= v <= 400 for v in thresholds.values()):
            raise ValueError("Thresholds must be between 1 and 400 cm")
        saved = self._store_configuration(mapping, thresholds, "Saved locally. Waiting for the Raspberry Pi to acknowledge the configuration.")
        if saved:
            self.settings_saved.emit({"configuration": {"mapping": dict(mapping), "thresholds": dict(thresholds)}})
        return saved

    def apply_remote_configuration(self, mapping, thresholds):
        """Sensor roles and thresholds pushed by the Pi hub (the master copy); no local sign-in needed."""
        return self._store_configuration(mapping, thresholds, "Sensor setup updated from the Pi hub.")

    def apply_remote_calibration(self, values):
        self.c.db.set("level_calibration", values)
        self.level_calibration = values
        self._last_level_save.clear()
        self.changed.emit()

    def _store_configuration(self, mapping, thresholds, feedback):
        self.before_activity_change.emit()
        next_revision = self.revision + 1
        self._advance_activity()
        if not self.c.db.set("hub_configuration", {"mapping": mapping, "thresholds": thresholds, "revision": next_revision, "lab1_thresholds_applied": True}):
            self.feedback = "Configuration could not be saved. Check database storage."
            self.changed.emit()
            return False
        # Finish demo visits under the previous assignment before reconfiguring.
        self._finish_demo_visits()
        self.mapping, self.thresholds = mapping.copy(), thresholds.copy()
        self.revision = next_revision
        self.acknowledged = None
        self._reset_states()
        self.publish_configuration()
        self.feedback = feedback
        self.changed.emit()
        return True

    def set_mode(self, mode):
        self.before_activity_change.emit()
        if mode not in {"Demo", "Live Pi"}:
            raise ValueError("Unknown hub mode")
        self._advance_activity()
        self._finish_demo_visits()
        self.mode = mode
        self._reset_states()
        self.c.db.set("hub_mode", mode)
        self.changed.emit()

    def _reset_states(self):
        self._sessions = {"sleep": 0.0, "toilet": 0.0}
        self._activity_clock = time.monotonic()
        for state in self.states.values():
            state.update(distance=None, presence=False, seen=0, started=None)

    @Slot(str)
    def connection_changed(self, status):
        self.before_activity_change.emit()
        self._advance_activity()
        self.connection = status
        if "disconnected" in status or "stopped" in status or "connected" in status or "lost" in status:
            self.acknowledged = None
            if self.mode == "Live Pi":
                self._reset_states()
        self.changed.emit()

    @Slot(dict)
    def process(self, message):
        self.before_activity_change.emit()
        kind = message["type"]
        if kind not in {"CONFIG_ACK", "SENSOR_READING", "ZONE_EVENT"}:
            return
        if message["revision"] != self.revision:
            self.feedback = "Ignored data from an old or unknown configuration revision."
            self.changed.emit()
            return
        if kind == "CONFIG_ACK":
            self.acknowledged = self.revision
            self.feedback = "Configuration applied on Raspberry Pi."
        elif self.mode == "Live Pi" and self.acknowledged == self.revision:
            sensor_id = message["sensor_id"]
            if kind == "SENSOR_READING":
                self._advance_activity()
                state = self.states[sensor_id]
                distance = message["distance_cm"]
                from pet_monitoring.activity_dataset import presence_for_area
                presence = presence_for_area(self.mapping[sensor_id], distance, self.thresholds[sensor_id])
                state.update(distance=distance, presence=presence, seen=time.monotonic(), updated=datetime.now().strftime("%H:%M:%S"))
                self._record_level(sensor_id)
            else:
                if self.mapping[sensor_id] not in {"eat", "water"}:
                    self.record_visit(message["event_id"], "live", sensor_id, self.mapping[sensor_id],
                                      message["duration_seconds"], self.revision)
        self.changed.emit()

    def record_visit(self, event_id, source, sensor_id, mode, duration, revision):
        self.c.db.execute("INSERT OR IGNORE INTO hub_visits VALUES (?,?,?,?,?,?,?)",
            (event_id, source, datetime.now().isoformat(timespec="seconds"), sensor_id, mode, duration, revision))

    def history(self):
        source = "demo" if self.mode == "Demo" else "live"
        cursor = self.c.db.execute("SELECT timestamp,sensor_id,mode,duration FROM hub_visits WHERE source=? ORDER BY rowid DESC LIMIT 100", (source,))
        return cursor.fetchall() if cursor else []

    def totals(self):
        source = "demo" if self.mode == "Demo" else "live"
        cursor = self.c.db.execute("SELECT mode,COUNT(*),SUM(duration) FROM hub_visits WHERE source=? AND timestamp LIKE ? GROUP BY mode",
                                   (source, datetime.now().date().isoformat() + "%"))
        return {mode: (count, duration) for mode, count, duration in cursor.fetchall()} if cursor else {}

    def _finish_demo_visits(self):
        if self.mode != "Demo":
            return
        for sensor_id, state in self.states.items():
            if state["started"] is not None:
                import uuid
                self.record_visit(str(uuid.uuid4()), "demo", sensor_id, self.mapping[sensor_id],
                                  max(0, time.monotonic() - state["started"]), self.revision)
                state["started"] = None

    def tick(self):
        self.before_activity_change.emit()
        self._advance_activity()
        now = time.monotonic()
        if self.mode == "Demo":
            self._demo_tick += 1
            for index, (sensor_id, state) in enumerate(self.states.items()):
                near = (self._demo_tick // 8) % 4 == index
                distance = 10.0 if near else 90.0
                presence = distance < self.thresholds[sensor_id]
                if presence and not state["presence"]:
                    state["started"] = now
                elif not presence and state["presence"] and state["started"] is not None:
                    import uuid
                    self.record_visit(str(uuid.uuid4()), "demo", sensor_id, self.mapping[sensor_id], now - state["started"], self.revision)
                    state["started"] = None
                state.update(distance=distance, presence=presence, seen=now, updated=datetime.now().strftime("%H:%M:%S"))
                self._record_level(sensor_id)
        else:
            for state in self.states.values():
                if state["seen"] and now - state["seen"] > 5:
                    state.update(distance=None, presence=False, seen=0)
        self.changed.emit()

    def close(self):
        self._advance_activity()
        self.timer.stop()
        self._finish_demo_visits()

    def _advance_activity(self, now=None, wall=None):
        now = time.monotonic() if now is None else now
        wall = datetime.now() if wall is None else wall
        previous = self._activity_clock
        self._activity_clock = now
        source = "demo" if self.mode == "Demo" else "live"
        for mode in self._sessions:
            # Union of sensors in the same zone, so duplicates don't double hours.
            valid_until = max((state["seen"] + 5 for key, state in self.states.items()
                if self.mapping[key] == mode and state["presence"] and state["distance"] is not None), default=previous)
            elapsed = max(0.0, min(now, valid_until) - previous)
            if elapsed:
                self._sessions[mode] += elapsed
                end = wall - timedelta(seconds=now - min(now, valid_until))
                begin = end - timedelta(seconds=elapsed)
                while begin < end:
                    boundary = datetime.combine(begin.date() + timedelta(days=1), datetime.min.time())
                    stop = min(boundary, end)
                    seconds = (stop - begin).total_seconds()
                    self.c.db.execute("INSERT INTO hub_activity_daily VALUES (?,?,?,?) ON CONFLICT(day,source,mode) DO UPDATE SET seconds=seconds+excluded.seconds",
                        (begin.date().isoformat(), source, mode, seconds))
                    begin = stop
            else:
                self._sessions[mode] = 0.0

    def activity(self, mode):
        relevant = [state for key, state in self.states.items() if self.mapping[key] == mode]
        now = time.monotonic()
        active = any(state["presence"] and state["distance"] is not None and now - state["seen"] <= 5 for state in relevant)
        valid = any(state["distance"] is not None and now - state["seen"] <= 5 for state in relevant)
        source = "demo" if self.mode == "Demo" else "live"
        cursor = self.c.db.execute("SELECT seconds FROM hub_activity_daily WHERE day=? AND source=? AND mode=?", (datetime.now().date().isoformat(), source, mode))
        row = cursor.fetchone() if cursor else None
        return active, valid, self._sessions[mode] if active else 0.0, row[0] if row else 0.0

    def activity_history(self):
        source = "demo" if self.mode == "Demo" else "live"
        cursor = self.c.db.execute("SELECT day,mode,seconds FROM hub_activity_daily WHERE source=? ORDER BY day DESC,mode LIMIT 60", (source,))
        return cursor.fetchall() if cursor else []

    def save_level_calibration(self, values):
        if not self.c.auth.is_authenticated:
            return False, "Sign in to save calibration."
        for sensor_id, value in values.items():
            full, empty, capacity = value["full_cm"], value["empty_cm"], value["capacity_l"]
            if not all(math.isfinite(x) for x in (full, empty, capacity)) or not 0 < full < empty <= 400 or capacity < 0:
                return False, f"Sensor {sensor_id}: enter valid distances with empty > full > 0."
        previous_values = dict(self.level_calibration)
        if not self.c.db.set("level_calibration", values):
            return False, "Unable to save calibration."
        # Remember the phone snapshot against which this desktop edit was made.
        # Replayed snapshots must not undo a newer local save, including a clear.
        remote = self.c.db.get("hub_remote_calibration", {})
        overrides = self.c.db.get("level_calibration_overrides", {})
        for key in set(values) | set(self.level_calibration):
            if values.get(key) != self.level_calibration.get(key):
                overrides[key] = {"baseline_known": key in remote, "baseline": remote.get(key)}
        if not self.c.db.set("level_calibration_overrides", overrides):
            return False, "Unable to save calibration sync state."
        self.level_calibration = values
        self._last_level_save.clear()
        self.changed.emit()
        patch = {key: ({"full_cm": values[key]["full_cm"], "empty_cm": values[key]["empty_cm"]} if key in values else None)
                 for key in set(values) | set(previous_values) if values.get(key) != previous_values.get(key)}
        if patch:
            self.settings_saved.emit({"calibration": patch})
        return True, "Container calibration saved locally; phone sync pending until connected."

    def level(self, sensor_id):
        state = self.states[sensor_id]
        distance = state["distance"]
        if distance is None or not state["seen"] or time.monotonic() - state["seen"] > 5:
            return None, None, "No valid level reading"
        calibration = self.level_calibration.get(sensor_id)
        if not calibration:
            return None, None, "Calibration required · enter full and empty distances"
        full, empty = calibration["full_cm"], calibration["empty_cm"]
        percent = max(0, min(100, 100 * (empty - distance) / (empty - full)))
        litres = calibration["capacity_l"] * percent / 100 if calibration.get("linear_volume") and calibration["capacity_l"] > 0 else None
        status = "Low level · refill" if percent < 30 else "Level OK"
        if distance < full or distance > empty:
            status += " · distance outside calibration range"
        return percent, litres, status

    def _record_level(self, sensor_id):
        if self.mapping[sensor_id] not in {"eat", "water"}:
            return
        now = time.monotonic()
        if now - self._last_level_save.get(sensor_id, -100) < 5:
            return
        percent, litres, _ = self.level(sensor_id)
        if percent is None:
            return
        self._last_level_save[sensor_id] = now
        self.c.db.execute("INSERT INTO hub_levels VALUES (?,?,?,?,?,?,?)", (datetime.now().isoformat(timespec="seconds"),
            "demo" if self.mode == "Demo" else "live", sensor_id, self.mapping[sensor_id], self.states[sensor_id]["distance"], percent, litres))

    def level_history(self):
        cursor = self.c.db.execute("SELECT timestamp,sensor_id,mode,percent,litres FROM hub_levels WHERE source=? ORDER BY rowid DESC LIMIT 100", ("demo" if self.mode == "Demo" else "live",))
        return cursor.fetchall() if cursor else []
