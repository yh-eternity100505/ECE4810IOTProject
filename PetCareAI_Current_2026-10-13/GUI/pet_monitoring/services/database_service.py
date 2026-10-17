import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from pet_monitoring.models import PetProfile, Alert, DailySummary, BehaviourEvent, SecurityEvent


class DatabaseService:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.available = True
        self.error = ""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(str(self.path), timeout=.1)
            self.connection.execute("PRAGMA journal_mode=WAL")
        except (sqlite3.Error, OSError):
            logging.exception("Database unavailable; using temporary in-memory storage")
            if hasattr(self, "connection"):
                self.connection.close()
            self.available = False
            self.error = "Database unavailable · changes are temporary"
            self.connection = sqlite3.connect(":memory:")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sensors (id INTEGER PRIMARY KEY, timestamp TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS behaviours (id INTEGER PRIMARY KEY, timestamp TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, timestamp TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS daily (date TEXT, source TEXT, payload TEXT, PRIMARY KEY(date, source));
            CREATE INDEX IF NOT EXISTS sensor_time ON sensors(timestamp);
            CREATE TABLE IF NOT EXISTS security_events (id INTEGER PRIMARY KEY, timestamp TEXT, payload TEXT);
        """)
        # Migrate the early date-only schema without losing stored summaries.
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(daily)")}
        if "source" not in columns:
            rows = self.connection.execute("SELECT date,payload FROM daily").fetchall()
            self.connection.execute("ALTER TABLE daily RENAME TO daily_legacy")
            self.connection.execute("CREATE TABLE daily (date TEXT, source TEXT, payload TEXT, PRIMARY KEY(date,source))")
            for day, payload in rows:
                source = json.loads(payload).get("source", "live")
                self.connection.execute("INSERT INTO daily VALUES (?,?,?)", (day, source, payload))
            self.connection.execute("DROP TABLE daily_legacy")
        self.connection.commit()

    def execute(self, sql, args=()):
        try:
            cursor = self.connection.execute(sql, args)
            if self.connection.in_transaction:
                self.connection.commit()
            return cursor
        except sqlite3.Error:
            logging.exception("Database operation failed")
            self.available = False
            self.error = "Storage error · check logs/app.log"
            return None

    def get(self, key, default=None):
        cursor = self.execute("SELECT value FROM config WHERE key=?", (key,))
        row = cursor.fetchone() if cursor else None
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        return self.execute("INSERT OR REPLACE INTO config VALUES (?,?)", (key, json.dumps(value))) is not None

    def profile(self):
        return PetProfile(**self.get("profile", asdict(PetProfile())))

    def save_profile(self, profile):
        return self.set("profile", asdict(profile))

    def save_sensor_batch(self, readings):
        payload = json.dumps([asdict(r) for r in readings])
        self.execute("INSERT INTO sensors(timestamp,payload) VALUES (?,?)", (readings[0].timestamp, payload))

    def save_behaviour(self, event):
        self.execute("INSERT INTO behaviours(timestamp,payload) VALUES (?,?)", (event.timestamp, json.dumps(asdict(event))))

    def behaviours(self, limit=100):
        cursor = self.execute("SELECT payload FROM behaviours ORDER BY id DESC LIMIT ?", (limit,))
        return [BehaviourEvent(**json.loads(r[0])) for r in cursor.fetchall()] if cursor else []

    def save_alert(self, alert):
        self.execute("INSERT OR REPLACE INTO alerts VALUES (?,?,?)", (alert.id, alert.timestamp, json.dumps(asdict(alert))))

    def alerts(self):
        cursor = self.execute("SELECT payload FROM alerts ORDER BY timestamp DESC, rowid DESC")
        return [Alert(**json.loads(r[0])) for r in cursor.fetchall()] if cursor else []

    def delete_alert(self, alert_id):
        self.execute("DELETE FROM alerts WHERE id=?", (alert_id,))

    def clear_alerts(self):
        self.execute("DELETE FROM alerts")

    def save_summary(self, summary):
        self.execute("INSERT OR REPLACE INTO daily VALUES (?,?,?)", (summary.date, summary.source, json.dumps(asdict(summary))))

    def summaries(self, days=30, source=None):
        start = (date.today() - timedelta(days=days - 1)).isoformat()
        if source:
            cursor = self.execute("SELECT payload FROM daily WHERE date>=? AND source=? ORDER BY date", (start, source))
        else:
            cursor = self.execute("SELECT payload FROM daily WHERE date>=? ORDER BY date, source", (start,))
        return [DailySummary(**json.loads(r[0])) for r in cursor.fetchall()] if cursor else []

    def seed_demo(self):
        """Seed only missing dates; keep user's existing history and alerts intact."""
        import math
        for offset in range(30):
            day = (date.today() - timedelta(days=offset)).isoformat()
            cursor = self.execute("SELECT 1 FROM daily WHERE date=? AND source='demo'", (day,))
            if cursor and not cursor.fetchone():
                self.save_summary(DailySummary(day, round(7.8 + .5 * math.sin(offset), 2),
                    6 + (offset % 3 - 1), 8 + (offset % 3 - 1), 3, round(4.8 + .4 * math.sin(offset), 2), "demo"))

    def save_security_event(self, event):
        self.execute("INSERT INTO security_events(timestamp,payload) VALUES (?,?)",
                     (event.timestamp, json.dumps(asdict(event))))

    def security_events(self, limit=200):
        cursor = self.execute("SELECT payload FROM security_events ORDER BY id DESC LIMIT ?", (limit,))
        return [SecurityEvent(**json.loads(row[0])) for row in cursor.fetchall()] if cursor else []

    def close(self):
        self.connection.close()
