"""Durable, concise phone-setting changes and accepted Pi activity events."""
import json
from datetime import datetime, timedelta
from PySide6.QtCore import QObject, Signal


class ChangeLog(QObject):
    changed = Signal()

    def __init__(self, database, parent=None, hub=None):
        super().__init__(parent)
        self.db = database
        self.hub = hub
        self.snapshot = database.get("phone_change_snapshot")
        database.execute("""CREATE TABLE IF NOT EXISTS phone_change_log (
            id INTEGER PRIMARY KEY, observed TEXT NOT NULL, category TEXT NOT NULL,
            summary TEXT NOT NULL, event_key TEXT UNIQUE)""")

    def add(self, category, summary, event_key=None):
        cursor = self.db.execute("INSERT OR IGNORE INTO phone_change_log(observed,category,summary,event_key) VALUES (?,?,?,?)",
                                 (datetime.now().isoformat(timespec="seconds"), category, summary, event_key))
        if cursor is not None and cursor.rowcount:
            self.changed.emit()

    def settings(self, message):
        # Explicit whitelist: never persist Telegram tokens, OTPs, passwords,
        # account objects or arbitrary settings included by a future sender.
        pet = message["pet"]
        values = {"Pet " + key.replace("_", " "): pet.get(key) for key in
                  ("name", "animal", "breed", "sex", "birthday", "weight_kg", "avatar", "baseline_sleep_hours", "baseline_toilet_visits")}
        values["Number of pets"] = message["pets_total"]
        for sensor, entry in message["sensors"].items():
            for key in ("role", "threshold_cm", "calibration", "location", "pins"):
                if key in entry:
                    values[f"Sensor {sensor} {key.replace('_', ' ')}"] = entry[key]
        if values == self.snapshot:
            return
        if self.snapshot is None:
            self.add("Phone settings", f"Initial phone settings received for {pet['name']}.")
        else:
            for key, value in values.items():
                if self.snapshot.get(key) != value:
                    before = json.dumps(self.snapshot.get(key), ensure_ascii=False, sort_keys=True)
                    after = json.dumps(value, ensure_ascii=False, sort_keys=True)
                    self.add("Phone settings", f"{key}: {before} -> {after}")
        self.snapshot = values
        self.db.set("phone_change_snapshot", values)

    def event(self, message):
        kind = message.get("type")
        if kind == "ZONE_EVENT":
            if self.hub is not None:
                if (self.hub.mode != "Live Pi" or message["revision"] != self.hub.revision
                        or self.hub.acknowledged != self.hub.revision):
                    return
                from pet_monitoring.hub_controller import MODES
                zone = MODES[self.hub.mapping[message["sensor_id"]]]
            else:
                zone = "Zone"
            key = f"zone:{message['sensor_id']}:{message['event_id']}"
            self.add("Activity", f"{zone}, Sensor {message['sensor_id']}: completed visit ({message['duration_seconds']:.1f} seconds).", key)
        elif kind == "MOVEMENT_RESULT":
            names = {"PASS_BY": "Pass-by detected", "APPROACH_STAY": "Waiting at the entrance detected", "AUTH_GESTURE": "Unlock gesture detected"}
            self.add("Entrance", f"{names.get(message['prediction'], 'Movement detected')} ({message['confidence']:.0%} confidence).")

    def rows(self, days=7, category="All"):
        start = (datetime.now().date() - timedelta(days=days - 1)).isoformat()
        sql = "SELECT observed,category,summary FROM phone_change_log WHERE observed>=?"
        args = [start]
        if category != "All":
            sql += " AND category=?"
            args.append(category)
        cursor = self.db.execute(sql + " ORDER BY id DESC LIMIT 500", tuple(args))
        return cursor.fetchall() if cursor else []
