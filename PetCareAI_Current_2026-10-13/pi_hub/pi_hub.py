#!/usr/bin/env python3
"""PetCare AI Pi hub: runs the laptop GUI's job on the Raspberry Pi itself.

The unchanged sensor script connects to this hub over localhost:
    python predict_movement.py --gui-host 127.0.0.1
The hub stores history in SQLite, raises alerts, sends Telegram messages,
handles gesture OTP access and serves the phone web app on port 8080.

Standard library only. Run:  python3 pi_hub.py
Set or reset a password:     python3 pi_hub.py --set-password admin
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import logging
import math
import os
import queue
import re
import secrets
import signal
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"

MODES = {"eat": "Food bowl", "sleep": "Bed", "water": "Water bowl", "toilet": "Litter box"}
DEFAULT_MAPPING = {"1": "eat", "2": "sleep", "3": "water", "4": "toilet"}
MODE_THRESHOLDS = {"eat": 20.0, "water": 20.0, "sleep": 14.0, "toilet": 13.0}
CLASSES = {"PASS_BY", "APPROACH_STAY", "AUTH_GESTURE"}
GESTURE_STATES = {"WAITING", "RECORDING", "CLASSIFYING", "WAIT_CLEAR"}

STALE_SECONDS = 5            # zone reading older than this is treated as offline
SERVICE_OFFLINE_ALERT = 60   # sensor script disconnected this long -> alert
ZONE_SILENT_ALERT = 120      # zone without a valid echo this long -> alert
LOW_LEVEL, LEVEL_RECOVER, LOW_LEVEL_HOLD = 30.0, 40.0, 60
LEVEL_SAVE_SECONDS = 60
GESTURE_WINDOW, OTP_SECONDS, OTP_COOLDOWN, OTP_ATTEMPTS = 120, 120, 30, 5
MIN_COVERAGE_HOURS = 20      # daily checks need this much observed time
SESSION_SECONDS = 14 * 86400
PBKDF2_ITERATIONS = 600_000
MAX_BODY = 16 * 1024
PHONE_MAX_JSON = 1024 * 1024        # largest decrypted phone request/response
PHONE_MAX_PACKET = PHONE_MAX_JSON * 9
CHALLENGE_SECONDS = 60
SENSOR_PINS = {"1": [13, 15], "2": [16, 18], "3": [22, 24], "4": [29, 31], "5": [7, 11]}  # physical BOARD TRIG/ECHO
AVATARS = ("cat", "dog", "rabbit", "hamster", "guinea_pig", "bird", "fish", "turtle")
REMINDER_KINDS = ("vaccination", "vet", "grooming", "medicine", "parasite", "other")
REPEAT_DAYS = (0, 7, 14, 30, 90, 180, 365)
REMINDER_HOUR = 9           # daily reminder notifications go out after 09:00
REMINDER_SOON_DAYS = 7
WEIGHTS = {"sleep": .25, "toilet": .15}  # original weights, renormalised

DEFAULT_SETTINGS = {
    "users": {},
    "must_change_password": False,
    "pet": {"name": "Milo", "animal": "Cat", "baseline_sleep_hours": 8.0, "baseline_toilet_visits": 3.0},
    "sensors": {"mapping": DEFAULT_MAPPING, "thresholds": {k: MODE_THRESHOLDS[m] for k, m in DEFAULT_MAPPING.items()}, "revision": 1},
    "calibration": {},
    "telegram": {"enabled": False, "token": "", "chat_id": ""},
    "notifications": {"entrance": True, "access": True, "levels": True, "sensors": True, "baseline": True, "reminders": True},
    "pets": [],
    "monitored_pet": "",
    # Where each sensor physically sits. x 0-10 left->right, z 0 front -> 15 back of the tray;
    # facing in degrees (0 = toward the back, 90 = right, 180 = toward the front, -90 = left).
    "sensor_layout": {
        "1": {"x": 10.15, "z": 1.15, "facing": -90, "mount": "raised", "note": "Food bowl, lower-right corner"},
        "2": {"x": 8.5, "z": 15.12, "facing": 180, "mount": "raised", "note": "Above the bed, upper-right"},
        "3": {"x": 10.15, "z": 4.75, "facing": -90, "mount": "raised", "note": "Water bottle, right side"},
        "4": {"x": 0.0, "z": 0.2, "facing": 37, "mount": "raised", "note": "Litter box, lower-left corner"},
        "5": {"x": 1.15, "z": 14.0, "facing": 180, "mount": "floor", "note": "Entrance, upper-left lane"},
    },
    "reminders": [],
    "anomaly_threshold": 0.30,
    "mirror": {"hosts": [], "port": 5005},
}
MAX_MIRRORS = 5


def parse_targets(entries, default_port=5005):
    """'10.0.0.5', '10.0.0.6:5006' or a comma/space separated string -> [(host, port)]."""
    if isinstance(entries, str):
        entries = re.split(r"[\s,;]+", entries)
    targets = []
    for entry in entries:
        entry = str(entry).strip()
        if not entry:
            continue
        host, _, port = entry.rpartition(":") if entry.count(":") == 1 else (entry, "", "")
        port = int(port) if port.isdigit() else default_port if not port else -1
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) or not 1 <= port <= 65535:
            raise ValueError(f"'{entry}' is not a valid address. Use an IP like 10.156.144.168")
        if (host, port) not in targets:
            targets.append((host, port))
    if len(targets) > MAX_MIRRORS:
        raise ValueError(f"Up to {MAX_MIRRORS} laptops")
    return targets


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def finite(value, low, high):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and low <= value <= high


def hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return {"salt": salt, "hash": digest.hex(), "iterations": iterations}


def check_password(record, password):
    try:
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(record["salt"]), record["iterations"])
        return hmac.compare_digest(candidate.hex(), record["hash"])
    except (KeyError, TypeError, ValueError):
        return False


# ============================================================
# Settings (JSON, owner-only permissions; holds the Telegram token)
# ============================================================
class SettingsStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            loaded = {}
        self.data = json.loads(json.dumps(DEFAULT_SETTINGS))
        for key, value in loaded.items():
            if isinstance(value, dict) and isinstance(self.data.get(key), dict):
                self.data[key].update(value)
            else:
                self.data[key] = value
        mirror = self.data["mirror"]
        old_host = mirror.pop("host", "")  # single-laptop setting from the previous version
        if old_host and old_host not in mirror["hosts"]:
            mirror["hosts"].append(old_host)
        if not self.data["pets"]:
            old = self.data["pet"]
            animal = str(old.get("animal", "")).strip().lower().replace(" ", "_")
            self.data["pets"] = [{"id": secrets.token_hex(4), "name": old.get("name", "Milo"), "animal": old.get("animal", ""),
                                  "avatar": animal if animal in AVATARS else "cat", "breed": "", "birthday": "", "sex": "",
                                  "weight_kg": None, "notes": "", "baseline_sleep_hours": old.get("baseline_sleep_hours", 8.0),
                                  "baseline_toilet_visits": old.get("baseline_toilet_visits", 3.0)}]
        if self.data["monitored_pet"] not in {pet["id"] for pet in self.data["pets"]}:
            self.data["monitored_pet"] = self.data["pets"][0]["id"]
        self.sync_monitored()
        if not self.data["users"]:
            # Same demo account as the desktop app; the phone app asks for a new password.
            self.data["users"] = {"admin": hash_password("pet123")}
            self.data["must_change_password"] = True
            logging.warning("Created default account admin / pet123. Change it in the app or with --set-password.")
        self.save()

    def sync_monitored(self):
        """data["pet"] mirrors the monitored pet; the sensor rules, scores and alerts read it."""
        pet = next(p for p in self.data["pets"] if p["id"] == self.data["monitored_pet"])
        self.data["pet"] = dict(pet)

    def save(self):
        with self.lock:
            temporary = self.path.with_suffix(".tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2)
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass


# ============================================================
# SQLite history
# ============================================================
class Database:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=2)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS visits (event_id TEXT PRIMARY KEY, timestamp TEXT, day TEXT, sensor_id TEXT, mode TEXT, duration REAL);
            CREATE TABLE IF NOT EXISTS activity_daily (day TEXT, mode TEXT, seconds REAL, PRIMARY KEY(day, mode));
            CREATE TABLE IF NOT EXISTS levels (timestamp TEXT, day TEXT, sensor_id TEXT, mode TEXT, distance REAL, percent REAL);
            CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, timestamp TEXT, severity TEXT, title TEXT, message TEXT, category TEXT, read INTEGER DEFAULT 0, telegram_status TEXT);
            CREATE TABLE IF NOT EXISTS gestures (timestamp TEXT, prediction TEXT, confidence REAL);
            CREATE TABLE IF NOT EXISTS daily_summary (day TEXT PRIMARY KEY, sleep_hours REAL, toilet_visits INTEGER, coverage_hours REAL, score INTEGER, status TEXT);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        self.conn.commit()

    def query(self, sql, params=()):
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
            self.conn.commit()
            return rows

    def meta(self, key, default=None):
        rows = self.query("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0][0] if rows else default

    def set_meta(self, key, value):
        self.query("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# ============================================================
# Telegram (urllib; the token never appears in logs)
# ============================================================
class Telegram:
    def __init__(self, settings):
        self.settings = settings

    def configured(self):
        t = self.settings.data["telegram"]
        return bool(t["enabled"] and t["token"] and t["chat_id"])

    def send(self, text):
        t = self.settings.data["telegram"]
        if not t["enabled"]:
            return False, "Telegram off"
        if not t["token"] or not t["chat_id"]:
            return False, "Not configured"
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{t['token']}/sendMessage",
            data=json.dumps({"chat_id": t["chat_id"], "text": text}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                ok = bool(json.load(response).get("ok"))
            logging.info("Telegram: %s", "sent" if ok else "rejected")
            return ok, "Sent" if ok else "Failed · check token and chat ID"
        except urllib.error.HTTPError as exc:
            logging.warning("Telegram HTTP error %s", exc.code)
            return False, "Failed · check token and chat ID"
        except (urllib.error.URLError, OSError, ValueError):
            logging.warning("Telegram request failed (network)")
            return False, "Failed · network unavailable"


# ============================================================
# Protocol validation (same contract as GUI movement_receiver.py)
# ============================================================
def parse_message(line):
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object")
    kind = message.get("type")
    if kind not in {"IDLE", "TRIGGERED", "RECORDING", "MOVEMENT_RESULT", "CONFIG_ACK", "SENSOR_READING", "ZONE_EVENT", "GESTURE_READING"}:
        raise ValueError("Unknown message type")
    result = {"type": kind}
    if kind == "GESTURE_READING":
        distance = message.get("distance_cm")
        if distance is not None and not finite(distance, 1e-9, 200):
            raise ValueError("Invalid gesture distance")
        samples, total = message.get("samples", 0), message.get("total", 60)
        if message.get("state") not in GESTURE_STATES or not finite(total, 2, 10000) or not finite(samples, 0, total):
            raise ValueError("Invalid gesture telemetry")
        result.update(distance_cm=distance, state=message["state"], samples=int(samples), total=int(total))
    elif kind in {"CONFIG_ACK", "SENSOR_READING", "ZONE_EVENT"}:
        revision = message.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("Invalid revision")
        result["revision"] = revision
        if kind != "CONFIG_ACK":
            sensor_id = str(message.get("sensor_id"))
            if sensor_id not in DEFAULT_MAPPING:
                raise ValueError("Zone sensor must be 1-4")
            result["sensor_id"] = sensor_id
            if kind == "SENSOR_READING":
                distance = message.get("distance_cm")
                if distance is not None and not finite(distance, 1e-9, 400):
                    raise ValueError("Invalid distance")
                result["distance_cm"] = distance
            else:
                event_id, duration = message.get("event_id"), message.get("duration_seconds")
                if not isinstance(event_id, str) or not 1 <= len(event_id) <= 100 or not finite(duration, 0, 86400):
                    raise ValueError("Invalid zone event")
                result.update(event_id=event_id, duration_seconds=float(duration))
    elif kind == "MOVEMENT_RESULT":
        prediction, confidence = message.get("prediction"), message.get("confidence")
        if prediction not in CLASSES or not finite(confidence, 0, 1):
            raise ValueError("Invalid movement result")
        result.update(prediction=prediction, confidence=float(confidence))
    return result


# ============================================================
# Hub state and rules
# ============================================================
class Hub:
    def __init__(self, settings, db, telegram, dashboard_source=False):
        self.settings, self.db, self.telegram = settings, db, telegram
        self.dashboard_source = dashboard_source
        self._source_sensors = None
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="telegram")
        self.states = {k: {"distance": None, "presence": False, "seen": 0.0, "valid_seen": 0.0, "since": None, "low_since": None}
                       for k in DEFAULT_MAPPING}
        self.connected = False
        self.peer = None
        self.ever_connected = False
        self.disconnected_at = time.monotonic()
        self.acknowledged = None
        self.gesture = {"distance": None, "state": None, "samples": 0, "total": 60, "seen": 0.0}
        self.conditions = set()
        self.access = {"gesture_at": None, "gesture_until": 0.0, "challenge": None, "last_request": -OTP_COOLDOWN,
                       "status": "Waiting for an unlock gesture at the entrance.", "demo_code": "", "generation": 0}
        self._clock = time.monotonic()
        self._pending = {}
        self._last_flush = time.monotonic()
        self._last_level_save = {}
        self.last_day = db.meta("last_day", date.today().isoformat())
        self.mirror = None
        self._last_reminder_check = -1e9
        self.edc, self.edc_error = None, None
        self.door_commands = queue.Queue(maxsize=16)

    def door(self, action):
        """Entrance lights/buzzer on the sensor script: AUTH_OK, OTP_OK or RESET."""
        if self.dashboard_source:
            return
        try:
            self.door_commands.put_nowait(action)
        except queue.Full:
            pass

    # ---------- configuration ----------
    @property
    def sensors(self):
        return self._source_sensors if self._source_sensors is not None else self.settings.data["sensors"]

    def dashboard_configuration(self, message):
        """Runtime-only sensor configuration supplied by the desktop over its SSH tunnel."""
        if not self.dashboard_source:
            raise ValueError("Desktop source mode is disabled")
        mapping, thresholds = message.get("mapping"), message.get("thresholds_cm")
        revision, acknowledged = message.get("revision"), message.get("acknowledged")
        connected = message.get("connected")
        if (not isinstance(mapping, dict) or set(mapping) != set(DEFAULT_MAPPING)
                or any(not isinstance(mode, str) or mode not in MODES for mode in mapping.values())
                or not isinstance(thresholds, dict) or set(thresholds) != set(DEFAULT_MAPPING)
                or not all(finite(value, 1, 400) for value in thresholds.values())
                or type(revision) is not int or revision < 1 or type(connected) is not bool
                or (acknowledged is not None and (type(acknowledged) is not int or acknowledged != revision))):
            raise ValueError("Invalid desktop source configuration")
        configuration = {"mapping": dict(mapping), "thresholds": dict(thresholds), "revision": revision}
        with self.lock:
            changed = configuration != self._source_sensors
            if changed:
                self._advance(time.monotonic())
                self._source_sensors = configuration
                self._reset_states()
            if connected and not self.connected:
                self.on_connect("Desktop GUI over SSH")
            elif not connected and self.connected:
                self.on_disconnect()
            self.acknowledged = acknowledged if connected else None

    def config_bytes(self):
        with self.lock:
            s = self.sensors
            message = {"type": "SET_CONFIG", "revision": s["revision"], "mapping": dict(s["mapping"]),
                       "thresholds_cm": dict(s["thresholds"]), "gesture_sensor": 5}
        return json.dumps(message, allow_nan=False).encode() + b"\n"

    def save_sensor_config(self, mapping, thresholds):
        if self.dashboard_source:
            raise ValueError("Sensor assignments are controlled by the desktop GUI")
        if set(mapping) != set(DEFAULT_MAPPING) or any(v not in MODES for v in mapping.values()):
            raise ValueError("Assign Food, Bed, Water or Litter to sensors 1-4")
        if set(thresholds) != set(DEFAULT_MAPPING) or not all(finite(v, 1, 400) for v in thresholds.values()):
            raise ValueError("Thresholds must be 1-400 cm")
        with self.lock:
            self._advance(time.monotonic())
            self.sensors.update(mapping=dict(mapping), thresholds={k: float(v) for k, v in thresholds.items()},
                                revision=self.sensors["revision"] + 1)
            self.settings.save()
            self.acknowledged = None
            self._reset_states()

    def _reset_states(self):
        for state in self.states.values():
            state.update(distance=None, presence=False, seen=0.0, since=None, low_since=None)

    # ---------- sensor link callbacks ----------
    def on_connect(self, peer):
        with self.lock:
            self._advance(time.monotonic())
            self.connected, self.peer, self.ever_connected = True, peer, True
            self.acknowledged = None
            self._reset_states()
            self.conditions.discard("service_offline")
        logging.info("Sensor script connected from %s", peer)

    def on_disconnect(self):
        with self.lock:
            self._advance(time.monotonic())
            self.connected, self.peer, self.acknowledged = False, None, None
            self.disconnected_at = time.monotonic()
            self._reset_states()
            self.gesture.update(distance=None, state=None, seen=0.0)
        logging.info("Sensor script disconnected")

    def process(self, message):
        kind = message["type"]
        now = time.monotonic()
        with self.lock:
            if kind in {"CONFIG_ACK", "SENSOR_READING", "ZONE_EVENT"}:
                if message["revision"] != self.sensors["revision"]:
                    return
                if kind == "CONFIG_ACK":
                    self.acknowledged = message["revision"]
                    logging.info("Pi applied configuration revision %s", self.acknowledged)
                    return
                if self.acknowledged != self.sensors["revision"]:
                    return
                sensor_id = message["sensor_id"]
                mode = self.sensors["mapping"][sensor_id]
                if kind == "SENSOR_READING":
                    self._advance(now)
                    self._zone_reading(sensor_id, mode, message["distance_cm"], now)
                elif mode in {"sleep", "toilet"}:
                    self.db.query("INSERT OR IGNORE INTO visits VALUES (?,?,?,?,?,?)",
                                  (message["event_id"], now_iso(), date.today().isoformat(), sensor_id, mode, message["duration_seconds"]))
            elif kind == "GESTURE_READING":
                self.gesture.update(distance=message["distance_cm"], state=message["state"], samples=message["samples"],
                                    total=message["total"], seen=now)
            elif kind == "MOVEMENT_RESULT":
                self._movement_result(message["prediction"], message["confidence"])

    def _zone_reading(self, sensor_id, mode, distance, now):
        state = self.states[sensor_id]
        presence = distance is not None and distance < self.sensors["thresholds"][sensor_id] and mode in {"sleep", "toilet"}
        if presence and not state["presence"]:
            state["since"] = now
        elif not presence:
            state["since"] = None
        state.update(distance=distance, presence=presence, seen=now)
        if distance is not None:
            state["valid_seen"] = now
            self.conditions.discard("zone_silent:" + sensor_id)
        if mode in {"eat", "water"}:
            self._check_level(sensor_id, mode, now)

    # ---------- food / water level ----------
    def level(self, sensor_id):
        state = self.states[sensor_id]
        if self.sensors["mapping"][sensor_id] not in {"eat", "water"}:
            return None
        if state["distance"] is None or time.monotonic() - state["seen"] > STALE_SECONDS:
            return None
        calibration = self.settings.data["calibration"].get(sensor_id)
        if not calibration:
            return None
        full, empty = calibration["full_cm"], calibration["empty_cm"]
        return max(0.0, min(100.0, 100 * (empty - state["distance"]) / (empty - full)))

    def _check_level(self, sensor_id, mode, now):
        percent = self.level(sensor_id)
        if percent is None:
            return
        state = self.states[sensor_id]
        if now - self._last_level_save.get(sensor_id, -1e9) >= LEVEL_SAVE_SECONDS:
            self._last_level_save[sensor_id] = now
            self.db.query("INSERT INTO levels VALUES (?,?,?,?,?,?)",
                          (now_iso(), date.today().isoformat(), sensor_id, mode, state["distance"], round(percent, 1)))
        condition = "level:" + sensor_id
        if percent < LOW_LEVEL:
            # Require a sustained low reading: a pet's head in the bowl looks like a full bowl, not an empty one,
            # but splashing and echo noise can briefly read low.
            state["low_since"] = state["low_since"] or now
            if now - state["low_since"] >= LOW_LEVEL_HOLD:
                what = "Food" if mode == "eat" else "Water"
                self.raise_alert(condition, "WARNING", f"{what} running low",
                                 f"The {MODES[mode].lower()} is at about {percent:.0f}%. Please refill it.", "levels")
        else:
            state["low_since"] = None
            if percent >= LEVEL_RECOVER:
                self.conditions.discard(condition)

    # ---------- entrance gestures / OTP ----------
    def _movement_result(self, prediction, confidence):
        self.db.query("INSERT INTO gestures VALUES (?,?,?)", (now_iso(), prediction, round(confidence, 4)))
        self.db.query("DELETE FROM gestures WHERE rowid NOT IN (SELECT rowid FROM gestures ORDER BY rowid DESC LIMIT 500)")
        if prediction == "APPROACH_STAY":
            if self.access["gesture_until"]:
                self.door("RESET")
            self._cancel_access("Waiting for an unlock gesture at the entrance.")
            # A fresh condition per event: each approach is its own notification.
            self.raise_alert("approach:" + uuid.uuid4().hex, "WARNING", "Someone is waiting at the entrance",
                             f"An approach-and-stay movement was detected at the entrance ({confidence:.0%} confidence). "
                             "Access has not been granted.", "entrance")
        elif prediction == "AUTH_GESTURE":
            self._cancel_access("Unlock gesture detected. Enter your username to receive a code.")
            self.access.update(gesture_at=now_iso(), gesture_until=time.monotonic() + GESTURE_WINDOW)
            self.door("AUTH_OK")
            if self.settings.data["notifications"]["access"]:
                self.executor.submit(self.telegram.send, "PetCare AI: unlock gesture detected at the entrance. "
                                     "Open the app within 2 minutes to request an access code.")

    def _cancel_access(self, status):
        self.access.update(gesture_at=None, gesture_until=0.0, challenge=None, demo_code="", status=status)
        self.access["generation"] += 1

    def request_otp(self, username):
        with self.lock:
            a, now = self.access, time.monotonic()
            username = username.strip()
            if now >= a["gesture_until"]:
                a["status"] = "Perform the unlock gesture at the entrance first."
            elif now - a["last_request"] < OTP_COOLDOWN:
                a["status"] = f"Wait {OTP_COOLDOWN} seconds before requesting another code."
            elif username not in self.settings.data["users"]:
                a["status"] = "Unable to start access for this username."
            else:
                code = f"{secrets.randbelow(1_000_000):06d}"
                a["last_request"] = now
                a["generation"] += 1
                generation = a["generation"]
                a["challenge"] = {"username": username, "code": code, "expires": now + OTP_SECONDS, "attempts": 0, "ready": False}
                a["demo_code"] = ""
                if not self.telegram.configured():
                    a["challenge"]["ready"] = True
                    a["demo_code"] = code
                    a["status"] = "Demo code shown below because Telegram is not set up. Expires in 2 minutes."
                else:
                    a["status"] = "Sending code to Telegram…"
                    future = self.executor.submit(self.telegram.send,
                                                  f"PetCare AI access code: {code}\nUsername: {username}\nExpires in 2 minutes.")
                    future.add_done_callback(lambda f: self._otp_delivered(generation, f))

    def _otp_delivered(self, generation, future):
        try:
            success, status = future.result()
        except Exception:
            success, status = False, "Delivery failed"
        with self.lock:
            a = self.access
            if generation != a["generation"] or not a["challenge"]:
                return
            a["challenge"]["ready"] = success
            a["status"] = "Code sent to Telegram · expires in 2 minutes." if success else "Code delivery failed: " + status
            if not success:
                a["challenge"] = None

    def verify_otp(self, code):
        with self.lock:
            a, challenge = self.access, self.access["challenge"]
            if not challenge or time.monotonic() >= challenge["expires"]:
                self._cancel_access("Code expired or unavailable. Perform the gesture again.")
                self.door("RESET")
                return False
            if not challenge["ready"]:
                a["status"] = "Wait for the code to arrive."
                return False
            challenge["attempts"] += 1
            if hmac.compare_digest(challenge["code"], str(code).strip()):
                username = challenge["username"]
                self._cancel_access(f"Access granted to {username}.")
                self.door("AUTH_OK")
                self.door("OTP_OK")
                logging.info("Entrance OTP verified for %s", username)
                self.db.query("INSERT INTO gestures VALUES (?,?,?)", (now_iso(), "ACCESS_GRANTED", 1.0))
                return True
            if challenge["attempts"] >= OTP_ATTEMPTS:
                self._cancel_access("Too many attempts. Perform the gesture again.")
                self.door("RESET")
            else:
                a["status"] = f"Wrong code · {OTP_ATTEMPTS - challenge['attempts']} attempts left."
            return False

    # ---------- sensor layout and desktop sync ----------
    def save_layout(self, layout):
        if set(layout) != set(SENSOR_PINS):
            raise ValueError("Send the placement of all five sensors")
        clean = {}
        for sensor_id, place in layout.items():
            note = str(place.get("note") or "").strip()[:60]
            if not finite(place.get("x"), -0.5, 10.5) or not finite(place.get("z"), -0.5, 15.5):
                raise ValueError(f"Sensor {sensor_id}: position must be inside the tray (x 0-10, z 0-15)")
            if not finite(place.get("facing"), -180, 180) or place.get("mount") not in ("floor", "raised"):
                raise ValueError(f"Sensor {sensor_id}: choose a facing direction and a mount")
            clean[sensor_id] = {"x": round(float(place["x"]), 2), "z": round(float(place["z"]), 2),
                                "facing": int(place["facing"]), "mount": place["mount"], "note": note}
        with self.settings.lock:
            self.settings.data["sensor_layout"] = clean
            self.settings.save()

    def sync_message(self):
        """Everything a desktop dashboard mirrors from the hub: the monitored pet and the hardware setup."""
        with self.lock:
            data = self.settings.data
            pet = data["pet"]
            sensors = {}
            for sensor_id, pins in SENSOR_PINS.items():
                entry = {"pins": pins, "location": dict(data["sensor_layout"][sensor_id])}
                if sensor_id == "5":
                    entry["role"] = "entrance"
                else:
                    calibration = data["calibration"].get(sensor_id)
                    entry.update(role=self.sensors["mapping"][sensor_id], threshold_cm=self.sensors["thresholds"][sensor_id],
                                 calibration={"full_cm": calibration["full_cm"], "empty_cm": calibration["empty_cm"]} if calibration else None)
                sensors[sensor_id] = entry
            return {"type": "HUB_SYNC", "revision": self.sensors["revision"],
                    "pet": {k: pet.get(k) for k in ("name", "animal", "breed", "sex", "birthday", "weight_kg", "avatar",
                                                    "baseline_sleep_hours", "baseline_toilet_visits")},
                    "pets_total": len(data["pets"]), "sensors": sensors}

    # ---------- pets and care reminders ----------
    def save_pet(self, body):
        def text(key, limit):
            return str(body.get(key) or "").strip()[:limit]
        name = text("name", 40)
        if not name:
            raise ValueError("Give your pet a name")
        avatar = body.get("avatar", "cat")
        if avatar not in AVATARS:
            raise ValueError("Unknown avatar")
        birthday = text("birthday", 10)
        if birthday:
            if date.fromisoformat(birthday) > date.today():
                raise ValueError("The birthday can't be in the future")
        sex = body.get("sex") or ""
        if sex not in ("", "female", "male"):
            raise ValueError("Sex must be female, male or blank")
        weight = body.get("weight_kg")
        if weight in ("", None):
            weight = None
        elif not finite(weight, 0.01, 200):
            raise ValueError("Weight must be 0.01-200 kg")
        sleep, toilet = body.get("baseline_sleep_hours", 8), body.get("baseline_toilet_visits", 3)
        if not finite(sleep, 0.5, 24) or not finite(toilet, 0.5, 50):
            raise ValueError("Usual sleep must be 0.5-24 h and usual litter visits 0.5-50")
        pet = {"name": name, "animal": text("animal", 30), "avatar": avatar, "breed": text("breed", 40),
               "birthday": birthday, "sex": sex, "weight_kg": None if weight is None else round(float(weight), 2),
               "notes": text("notes", 300), "baseline_sleep_hours": float(sleep), "baseline_toilet_visits": float(toilet)}
        with self.settings.lock:
            pets = self.settings.data["pets"]
            existing = next((p for p in pets if p["id"] == body.get("id")), None)
            if existing:
                existing.update(pet)
            else:
                if len(pets) >= 12:
                    raise ValueError("Up to 12 pets")
                existing = {"id": secrets.token_hex(4), **pet}
                pets.append(existing)
            if body.get("monitored"):
                self.settings.data["monitored_pet"] = existing["id"]
            self.settings.sync_monitored()
            self.settings.save()
        return existing["id"]

    def delete_pet(self, pet_id):
        with self.settings.lock:
            pets = self.settings.data["pets"]
            if len(pets) == 1:
                raise ValueError("Keep at least one pet")
            if not any(p["id"] == pet_id for p in pets):
                raise ValueError("Unknown pet")
            self.settings.data["pets"] = [p for p in pets if p["id"] != pet_id]
            self.settings.data["reminders"] = [r for r in self.settings.data["reminders"] if r["pet_id"] != pet_id]
            if self.settings.data["monitored_pet"] == pet_id:
                self.settings.data["monitored_pet"] = self.settings.data["pets"][0]["id"]
            self.settings.sync_monitored()
            self.settings.save()

    def set_monitored(self, pet_id):
        with self.settings.lock:
            if not any(p["id"] == pet_id for p in self.settings.data["pets"]):
                raise ValueError("Unknown pet")
            self.settings.data["monitored_pet"] = pet_id
            self.settings.sync_monitored()
            self.settings.save()

    def save_reminder(self, body):
        title = str(body.get("title") or "").strip()[:60]
        kind, repeat = body.get("kind", "other"), body.get("repeat_days", 0)
        if not title:
            raise ValueError("Give the reminder a title")
        if kind not in REMINDER_KINDS:
            raise ValueError("Unknown reminder type")
        if repeat not in REPEAT_DAYS:
            raise ValueError("Unsupported repeat interval")
        due = date.fromisoformat(str(body.get("due", ""))).isoformat()
        reminder = {"pet_id": body.get("pet_id"), "title": title, "kind": kind, "due": due,
                    "repeat_days": repeat, "notes": str(body.get("notes") or "").strip()[:200]}
        with self.settings.lock:
            if not any(p["id"] == reminder["pet_id"] for p in self.settings.data["pets"]):
                raise ValueError("Choose which pet this is for")
            reminders = self.settings.data["reminders"]
            existing = next((r for r in reminders if r["id"] == body.get("id")), None)
            if existing:
                existing.update(reminder)
            else:
                if len(reminders) >= 100:
                    raise ValueError("Up to 100 reminders")
                reminders.append({"id": secrets.token_hex(4), **reminder})
            self.settings.save()

    def complete_reminder(self, reminder_id):
        """Repeating reminders move to their next date; one-off reminders are removed."""
        with self.settings.lock:
            reminders = self.settings.data["reminders"]
            reminder = next((r for r in reminders if r["id"] == reminder_id), None)
            if not reminder:
                raise ValueError("Unknown reminder")
            if reminder["repeat_days"]:
                due = date.fromisoformat(reminder["due"])
                while due <= date.today():
                    due += timedelta(days=reminder["repeat_days"])
                reminder["due"] = due.isoformat()
            else:
                reminders.remove(reminder)
            self.settings.save()

    def delete_reminder(self, reminder_id):
        with self.settings.lock:
            self.settings.data["reminders"] = [r for r in self.settings.data["reminders"] if r["id"] != reminder_id]
            self.settings.save()

    def reminders_due(self, days=REMINDER_SOON_DAYS):
        """Reminders overdue or due within `days`, soonest first, with the pet's name and avatar."""
        today = date.today()
        pets = {p["id"]: p for p in self.settings.data["pets"]}
        due = []
        for r in self.settings.data["reminders"]:
            days_left = (date.fromisoformat(r["due"]) - today).days
            if days_left <= days and r["pet_id"] in pets:
                pet = pets[r["pet_id"]]
                due.append({**r, "days_left": days_left, "pet_name": pet["name"], "pet_avatar": pet["avatar"]})
        return sorted(due, key=lambda r: r["days_left"])

    def _send_reminders(self, today):
        """Alert once per due/overdue reminder per day; the sent list survives restarts."""
        try:
            sent = set(json.loads(self.db.meta("reminders_sent", "{}")).get(today, []))
        except ValueError:
            sent = set()
        due = [r for r in self.reminders_due(days=0) if r["id"] not in sent]
        if not due:
            return
        self.db.set_meta("reminders_sent", json.dumps({today: sorted(sent | {r["id"] for r in due})}))
        for r in due:
            when = "today" if r["days_left"] == 0 else f"{-r['days_left']} day{'s' if r['days_left'] != -1 else ''} overdue"
            self.raise_alert(f"reminder:{r['id']}:{today}", "INFO", f"{r['pet_name']}: {r['title']} ({when})",
                             f"Care reminder for {r['pet_name']}: {r['title']} is due {r['due']}. "
                             "Mark it done in the app under Settings → Care reminders.", "reminders")

    # ---------- alerts ----------
    def raise_alert(self, condition, severity, title, message, category):
        with self.lock:
            if condition in self.conditions:
                return
            self.conditions.add(condition)
        alert_id = uuid.uuid4().hex
        wants_telegram = self.settings.data["notifications"].get(category, True)
        status = "Sending…" if wants_telegram and self.telegram.configured() else "App only"
        self.db.query("INSERT INTO alerts (id,timestamp,severity,title,message,category,telegram_status) VALUES (?,?,?,?,?,?,?)",
                      (alert_id, now_iso(), severity, title, message, category, status))
        self.db.query("DELETE FROM alerts WHERE rowid NOT IN (SELECT rowid FROM alerts ORDER BY rowid DESC LIMIT 300)")
        logging.warning("Alert: %s", title)
        if status == "Sending…":
            pet = self.settings.data["pet"]["name"]
            future = self.executor.submit(self.telegram.send, f"[{severity}] {title}\n{message}\n— PetCare AI · {pet}")
            future.add_done_callback(lambda f: self.db.query(
                "UPDATE alerts SET telegram_status=? WHERE id=?", (f.result()[1] if not f.exception() else "Failed", alert_id)))

    # ---------- periodic work ----------
    def tick(self):
        now = time.monotonic()
        with self.lock:
            self._advance(now)
            for sensor_id, state in self.states.items():
                if state["seen"] and now - state["seen"] > STALE_SECONDS:
                    state.update(distance=None, presence=False, seen=0.0, since=None)
                if self.acknowledged and state["valid_seen"] and now - state["valid_seen"] > ZONE_SILENT_ALERT:
                    name = MODES[self.sensors["mapping"][sensor_id]]
                    self.raise_alert("zone_silent:" + sensor_id, "WARNING", f"{name} sensor not responding",
                                     f"Sensor {sensor_id} ({name}) has returned no valid distance for 2 minutes. Check its wiring and aim.", "sensors")
            if self.gesture["seen"] and now - self.gesture["seen"] > STALE_SECONDS:
                self.gesture.update(distance=None, state=None, seen=0.0)
            if not self.connected and now - self.disconnected_at > SERVICE_OFFLINE_ALERT:
                self.raise_alert("service_offline", "CRITICAL", "Sensors offline",
                                 "The sensor script is not connected to the hub. Monitoring is paused until it reconnects.", "sensors")
            challenge = self.access["challenge"]
            if challenge and now >= challenge["expires"]:
                self._cancel_access("Code expired. Perform the gesture again.")
                self.door("RESET")
            elif not challenge and self.access["gesture_until"] and now >= self.access["gesture_until"]:
                self._cancel_access("Gesture window closed. Perform the gesture again.")
            if now - self._last_flush >= 15:
                self.flush()
            today = date.today().isoformat()
            if datetime.now().hour >= REMINDER_HOUR and now - self._last_reminder_check >= 60:
                self._last_reminder_check = now
                self._send_reminders(today)
            if today != self.last_day:
                self.flush()
                self._close_day(self.last_day)
                self.last_day = today
                self.db.set_meta("last_day", today)

    def _advance(self, now):
        """Accumulate time the pet spent in sleep/toilet zones and time the hub was observing."""
        elapsed, self._clock = now - self._clock, now
        if not 0 < elapsed <= 10:
            return
        day = date.today().isoformat()
        modes = []
        if self.connected and self.acknowledged:
            modes.append("observed")
        for mode in ("sleep", "toilet"):
            if any(self.sensors["mapping"][k] == mode and s["presence"] and now - s["seen"] <= STALE_SECONDS
                   for k, s in self.states.items()):
                modes.append(mode)
        for mode in modes:
            self._pending[(day, mode)] = self._pending.get((day, mode), 0.0) + elapsed

    def flush(self):
        with self.lock:
            pending, self._pending = self._pending, {}
            self._last_flush = time.monotonic()
        for (day, mode), seconds in pending.items():
            self.db.query("INSERT INTO activity_daily VALUES (?,?,?) ON CONFLICT(day,mode) DO UPDATE SET seconds=seconds+excluded.seconds",
                          (day, mode, seconds))

    def day_totals(self, day):
        seconds = {mode: s for mode, s in self.db.query("SELECT mode,seconds FROM activity_daily WHERE day=?", (day,))}
        with self.lock:
            for (d, mode), s in self._pending.items():
                if d == day:
                    seconds[mode] = seconds.get(mode, 0.0) + s
        toilet = self.db.query("SELECT COUNT(*) FROM visits WHERE day=? AND mode='toilet'", (day,))[0][0]
        return {"sleep_hours": seconds.get("sleep", 0.0) / 3600, "toilet_visits": toilet,
                "coverage_hours": seconds.get("observed", 0.0) / 3600}

    def _close_day(self, day):
        totals = self.day_totals(day)
        pet = self.settings.data["pet"]
        baseline = {"sleep": pet["baseline_sleep_hours"], "toilet": pet["baseline_toilet_visits"]}
        observed = {"sleep": totals["sleep_hours"], "toilet": totals["toilet_visits"]}
        score = status = None
        if totals["coverage_hours"] >= MIN_COVERAGE_HOURS:
            parts = {k: max(0.0, 100 - abs(observed[k] - b) / b * 100) for k, b in baseline.items() if b > 0}
            weight = sum(WEIGHTS[k] for k in parts)
            score = round(sum(v * WEIGHTS[k] for k, v in parts.items()) / weight) if weight else None
            status = None if score is None else "NORMAL" if score >= 80 else "ATTENTION" if score >= 60 else "ABNORMAL"
            threshold = self.settings.data["anomaly_threshold"]
            for key, label in (("sleep", "sleep"), ("toilet", "litter box use")):
                if baseline[key] > 0:
                    delta = (observed[key] - baseline[key]) / baseline[key]
                    if abs(delta) > threshold:
                        direction = "more" if delta > 0 else "less"
                        self.raise_alert(f"baseline:{day}:{key}", "WARNING", f"Unusual {label} yesterday",
                                         f"{pet['name']} had {abs(delta):.0%} {direction} {label} than usual on {day}. "
                                         "Behaviour monitoring only, not a diagnosis.", "baseline")
        self.db.query("INSERT OR REPLACE INTO daily_summary VALUES (?,?,?,?,?,?)",
                      (day, round(totals["sleep_hours"], 2), totals["toilet_visits"], round(totals["coverage_hours"], 2), score, status))

    # ---------- views for the API ----------
    def status(self):
        now = time.monotonic()
        today = date.today().isoformat()
        with self.lock:
            zones = []
            for sensor_id, state in self.states.items():
                mode = self.sensors["mapping"][sensor_id]
                online = state["distance"] is not None and now - state["seen"] <= STALE_SECONDS
                calibrated = sensor_id in self.settings.data["calibration"]
                zones.append({"sensor_id": sensor_id, "mode": mode, "name": MODES[mode], "online": online,
                              "distance_cm": state["distance"], "presence": state["presence"],
                              "since_seconds": round(now - state["since"]) if state["since"] else None,
                              "level_percent": None if self.level(sensor_id) is None else round(self.level(sensor_id)),
                              "calibrated": calibrated, "threshold_cm": self.sensors["thresholds"][sensor_id]})
            gesture = dict(self.gesture)
            gesture["online"] = bool(gesture.pop("seen"))
            a = self.access
            access = {"window_open": now < a["gesture_until"], "gesture_at": a["gesture_at"],
                      "seconds_left": max(0, round(a["gesture_until"] - now)),
                      "awaiting_code": bool(a["challenge"]), "status": a["status"], "demo_code": a["demo_code"]}
            link = {"connected": self.connected, "configured": self.connected and self.acknowledged == self.sensors["revision"],
                    "revision": self.sensors["revision"], "peer": self.peer}
            pet = dict(self.settings.data["pet"])
            must_change = self.settings.data["must_change_password"]
        last_gesture = self.db.query("SELECT timestamp,prediction,confidence FROM gestures ORDER BY rowid DESC LIMIT 1")
        last_visit = self.db.query("SELECT timestamp,mode,duration FROM visits ORDER BY rowid DESC LIMIT 1")
        yesterday = self.db.query("SELECT day,score,status,coverage_hours FROM daily_summary ORDER BY day DESC LIMIT 1")
        alerts = self.db.query("SELECT COUNT(*), SUM(severity='CRITICAL') FROM alerts WHERE read=0")[0]
        active = sorted(c.split(":")[0] for c in self.conditions if not c.startswith(("approach:", "baseline:")))
        return {
            "time": now_iso(), "pet": pet, "link": link, "zones": zones, "gesture": gesture, "access": access,
            "dashboard_source": self.dashboard_source,
            "today": self.day_totals(today),
            "yesterday": dict(zip(("day", "score", "status", "coverage_hours"), yesterday[0])) if yesterday else None,
            "last_gesture": dict(zip(("timestamp", "prediction", "confidence"), last_gesture[0])) if last_gesture else None,
            "last_visit": dict(zip(("timestamp", "mode", "duration"), last_visit[0])) if last_visit else None,
            "alerts": {"unread": alerts[0] or 0, "critical": alerts[1] or 0}, "active_conditions": active,
            "must_change_password": must_change, "telegram_configured": self.telegram.configured(),
            "pets": [{k: p[k] for k in ("id", "name", "avatar", "animal")} for p in self.settings.data["pets"]],
            "monitored_pet": self.settings.data["monitored_pet"],
            "layout": self.settings.data["sensor_layout"],
            "reminders_due": self.reminders_due(),
            "mirror": {"hosts": list(self.settings.data["mirror"]["hosts"]), "states": self.mirror.states() if self.mirror else [],
                       "encryption": self.edc_error or ("EDC + HMAC" if self.edc else "Off: no keys on the Pi, sent unencrypted")},
        }

    def activity(self, days):
        start = (date.today() - timedelta(days=days - 1)).isoformat()
        daily = {}
        for day, mode, seconds in self.db.query("SELECT day,mode,seconds FROM activity_daily WHERE day>=?", (start,)):
            daily.setdefault(day, {})[mode] = seconds
        visits = {day: n for day, n in self.db.query("SELECT day,COUNT(*) FROM visits WHERE mode='toilet' AND day>=? GROUP BY day", (start,))}
        rows = []
        for offset in range(days):
            day = (date.today() - timedelta(days=days - 1 - offset)).isoformat()
            totals = self.day_totals(day) if day == date.today().isoformat() else {
                "sleep_hours": daily.get(day, {}).get("sleep", 0) / 3600, "toilet_visits": visits.get(day, 0),
                "coverage_hours": daily.get(day, {}).get("observed", 0) / 3600}
            rows.append({"day": day, **{k: round(v, 2) for k, v in totals.items()}})
        recent = [dict(zip(("timestamp", "mode", "duration"), r)) for r in
                  self.db.query("SELECT timestamp,mode,duration FROM visits ORDER BY rowid DESC LIMIT 30")]
        gestures = [dict(zip(("timestamp", "prediction", "confidence"), r)) for r in
                    self.db.query("SELECT timestamp,prediction,confidence FROM gestures ORDER BY rowid DESC LIMIT 30")]
        levels = [dict(zip(("timestamp", "mode", "percent"), r)) for r in
                  self.db.query("SELECT timestamp,mode,percent FROM levels WHERE timestamp>=? ORDER BY rowid", (start,))]
        return {"days": rows, "visits": recent, "gestures": gestures, "levels": levels[-500:],
                "baseline": {"sleep_hours": self.settings.data["pet"]["baseline_sleep_hours"],
                             "toilet_visits": self.settings.data["pet"]["baseline_toilet_visits"]}}


# ============================================================
# TCP link to the unchanged predict_movement script
# ============================================================
class SensorLink(threading.Thread):
    def __init__(self, hub, host, port):
        super().__init__(daemon=True, name="sensor-link")
        self.hub, self.host, self.port = hub, host, port
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.startup_error = None

    def run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                server.bind((self.host, self.port))
                server.listen(1)
            except OSError as exc:
                self.startup_error = str(exc)
                self.ready.set()
                return
            self.port = server.getsockname()[1]
            self.ready.set()
            server.settimeout(.5)
            logging.info("Waiting for the sensor script on %s:%s", self.host, self.port)
            while not self.stop.is_set():
                try:
                    client, address = server.accept()
                except socket.timeout:
                    continue
                if not self.hub.dashboard_source:
                    self.hub.on_connect(address[0])
                try:
                    self._serve(client)
                except (OSError, ValueError) as exc:
                    logging.warning("Sensor link closed: %s", exc)
                finally:
                    client.close()
                    self.hub.on_disconnect()

    def _serve(self, client):
        client.settimeout(.2)
        buffer, sent = b"", None
        while not self.hub.door_commands.empty():
            self.hub.door_commands.get_nowait()  # stale commands from a previous connection
        while not self.stop.is_set():
            configuration = self.hub.config_bytes()
            if not self.hub.dashboard_source and configuration != sent:
                client.sendall(configuration)
                sent = configuration
            while not self.hub.door_commands.empty():
                action = self.hub.door_commands.get_nowait()
                client.sendall(json.dumps({"type": "DOOR_COMMAND", "action": action}).encode() + b"\n")
                logging.info("Entrance: %s", action)
            try:
                data = client.recv(4096)
            except socket.timeout:
                continue
            if not data:
                return
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if len(line) > MAX_BODY:
                    raise ValueError("Message too large")
                if not line.strip():
                    continue
                try:
                    text = line.decode("utf-8")
                    if self.hub.dashboard_source:
                        source = json.loads(text)
                        if isinstance(source, dict) and source.get("type") == "DASHBOARD_SOURCE":
                            self.hub.dashboard_configuration(source)
                            continue
                        if not self.hub.connected or self.hub._source_sensors is None:
                            continue
                    self.hub.process(parse_message(text))
                    if self.hub.mirror:
                        self.hub.mirror.forward(json.loads(text))
                except (ValueError, UnicodeError) as exc:
                    logging.warning("Ignored invalid sensor message: %s", exc)
            if len(buffer) > MAX_BODY:
                raise ValueError("Message too large")


# ============================================================
# EDC + HMAC for the laptop dashboards (wire-compatible with the GUI receiver)
# ============================================================
EDC_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
EDC_ROWS = ("ECE", "FIT", "TRC", "MEC", "CHE", "CIV")
EDC_AUTH_CONTEXT = b"EDC-HMAC-v1\x00"
EDC_MAX_JSON, EDC_MAX_WIRE, EDC_MAX_SKEW, EDC_REPLAY_SECONDS = 4096, 131072, 120, 300
DEFAULT_EDC_KEYS = Path.home() / ".config" / "petcare" / "edc.env"


def _edc_key(key):
    key = str(key).upper().replace(" ", "")
    if not key or any(c not in EDC_ALPHABET for c in key):
        raise ValueError("EDC key must contain only A-Z and 0-9")
    return key


def _edc_matrix(key):
    alphabet = "".join(dict.fromkeys(key + EDC_ALPHABET))
    return alphabet, {c: divmod(i, 6) for i, c in enumerate(alphabet)}


def _edc_permutation(key, block, length):
    scores = [(EDC_ALPHABET.index(key[(block * 4 + p) % len(key)]) + 11 * p + 7 * block) % 97 for p in range(length)]
    return sorted(range(length), key=lambda p: (scores[p], p))


def edc_encrypt(message, key):
    """Keyed 6x6 matrix, coordinate shifts with ciphertext feedback, four-token block permutation."""
    _, coordinates = _edc_matrix(key)
    row_prev, col_prev = divmod(sum(EDC_ALPHABET.index(c) for c in key) % 36, 6)
    tokens = []
    for i, character in enumerate(message):
        row, col = coordinates[character]
        key_row, key_col = divmod(EDC_ALPHABET.index(key[i % len(key)]), 6)
        row_prev = (row + key_row + i + row_prev) % 6
        col_prev = (col + key_col + 2 * i + col_prev) % 6
        tokens.append(f"{EDC_ROWS[row_prev]}{col_prev + 1}")
    output = []
    for start in range(0, len(tokens), 4):
        block = tokens[start:start + 4]
        output.extend(block[p] for p in _edc_permutation(key, start // 4, len(block)))
    return "-".join(output)


def edc_decrypt(ciphertext, key):
    transmitted = ciphertext.upper().split("-")
    tokens = []
    for start in range(0, len(transmitted), 4):
        block = transmitted[start:start + 4]
        restored = [None] * len(block)
        for position, original in enumerate(_edc_permutation(key, start // 4, len(block))):
            restored[original] = block[position]
        tokens.extend(restored)
    alphabet, _ = _edc_matrix(key)
    row_prev, col_prev = divmod(sum(EDC_ALPHABET.index(c) for c in key) % 36, 6)
    output = []
    for i, token in enumerate(tokens):
        if len(token) != 4 or token[:3] not in EDC_ROWS or token[3] not in "123456":
            raise ValueError("Invalid EDC token")
        row, col = EDC_ROWS.index(token[:3]), int(token[3]) - 1
        key_row, key_col = divmod(EDC_ALPHABET.index(key[i % len(key)]), 6)
        output.append(alphabet[((row - key_row - i - row_prev) % 6) * 6 + (col - key_col - 2 * i - col_prev) % 6])
        row_prev, col_prev = row, col
    return "".join(output)


class EdcCodec:
    """Encrypt-then-HMAC packets; verify the HMAC before decrypting; reject stale or replayed packets."""
    def __init__(self, edc_key, hmac_key, max_json=EDC_MAX_JSON):
        self.edc_key = _edc_key(edc_key)
        if not isinstance(hmac_key, bytes) or len(hmac_key) != 32:
            raise ValueError("HMAC key must be 32 bytes (64 hex characters)")
        self.hmac_key = hmac_key
        self.max_json = max_json
        self.max_wire = max(EDC_MAX_WIRE, max_json * 9)  # Base32 (x1.6) then 5 characters per EDC token
        self._seen = {}
        self._lock = threading.Lock()

    def fresh(self):
        """Same keys, separate replay memory (one per dashboard connection)."""
        return EdcCodec(self.edc_key, self.hmac_key, self.max_json)

    def _tag(self, ciphertext):
        return hmac.new(self.hmac_key, EDC_AUTH_CONTEXT + ciphertext.encode(), "sha256").hexdigest()

    def encode(self, message):
        # Nonce first: EDC has no IV, but its ciphertext feedback carries this random start through every later token.
        inner = {"nonce": secrets.token_hex(16).upper(), "v": 1, "sent_at": int(time.time()), "message": message}
        raw = json.dumps(inner, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > self.max_json:
            raise ValueError("Message too large for EDC")
        ciphertext = edc_encrypt(base64.b32encode(raw).decode("ascii").rstrip("="), self.edc_key)
        return {"type": "EDC_PACKET", "v": 1, "ciphertext": ciphertext, "tag": self._tag(ciphertext)}

    def decode(self, packet):
        if (not isinstance(packet, dict) or set(packet) != {"type", "v", "ciphertext", "tag"}
                or packet["type"] != "EDC_PACKET" or type(packet["v"]) is not int or packet["v"] != 1):
            raise ValueError("Expected an EDC_PACKET")
        ciphertext, tag = packet["ciphertext"], packet["tag"]
        if not isinstance(ciphertext, str) or not 0 < len(ciphertext) <= self.max_wire:
            raise ValueError("Invalid ciphertext size")
        if not isinstance(tag, str) or not re.fullmatch(r"[0-9a-f]{64}", tag) or not hmac.compare_digest(self._tag(ciphertext), tag):
            raise ValueError("HMAC verification failed")
        plaintext = edc_decrypt(ciphertext, self.edc_key)
        if not re.fullmatch(r"[A-Z2-7]+", plaintext):
            raise ValueError("Invalid decrypted Base32")
        raw = base64.b32decode(plaintext + "=" * (-len(plaintext) % 8))
        if len(raw) > self.max_json:
            raise ValueError("Decrypted JSON too large")
        inner = json.loads(raw)
        if not isinstance(inner, dict) or set(inner) != {"v", "sent_at", "nonce", "message"} or inner["v"] != 1:
            raise ValueError("Invalid authenticated fields")
        now = time.time()
        if type(inner["sent_at"]) is not int or abs(int(now) - inner["sent_at"]) > EDC_MAX_SKEW:
            raise ValueError("Expired or future packet; check both clocks")
        nonce, message = inner["nonce"], inner["message"]
        if not isinstance(nonce, str) or not re.fullmatch(r"[0-9A-F]{32}", nonce):
            raise ValueError("Invalid nonce")
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise ValueError("Invalid message")
        with self._lock:
            self._seen = {n: t for n, t in self._seen.items() if now - t <= EDC_REPLAY_SECONDS}
            if nonce in self._seen:
                raise ValueError("Replay rejected")
            self._seen[nonce] = now
        return message


def load_edc_codec(path):
    """Keys from EDC_KEY / EDC_HMAC_KEY_HEX in the environment, else from the Pi's edc.env file.

    Returns (codec or None, error or None). A file that exists but cannot be used is an error,
    so dashboards are refused rather than silently sent plaintext.
    """
    values = {k: os.environ[k] for k in ("EDC_KEY", "EDC_HMAC_KEY_HEX") if os.environ.get(k)}
    if len(values) < 2 and path and Path(path).is_file():
        try:
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.removeprefix("export ").split("=", 1)
                values[name.strip()] = value.strip().strip("'\"")
        except OSError as exc:
            return None, f"Cannot read EDC key file: {exc.strerror}"
    if "EDC_KEY" not in values and "EDC_HMAC_KEY_HEX" not in values:
        return None, None
    try:
        return EdcCodec(values.get("EDC_KEY", ""), bytes.fromhex(values.get("EDC_HMAC_KEY_HEX", ""))), None
    except ValueError as exc:
        return None, f"Invalid EDC keys: {exc}"


# ============================================================
# Mirror: live copy for the laptop developer dashboard
# ============================================================
class MirrorManager(threading.Thread):
    """Keeps one Mirror per laptop saved in settings; forwards each message to all of them."""
    def __init__(self, hub):
        super().__init__(daemon=True, name="mirrors")
        self.hub = hub
        self.mirrors = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def targets(self):
        mirror = self.hub.settings.data["mirror"]
        try:
            return parse_targets(mirror["hosts"], int(mirror["port"]))
        except ValueError:
            logging.warning("Ignoring invalid dashboard addresses in settings")
            return []

    def run(self):
        while not self.stop.is_set():
            wanted = self.targets()
            with self.lock:
                for target in [t for t in self.mirrors if t not in wanted]:
                    self.mirrors.pop(target).stop.set()
                    logging.info("Dashboard mirror: %s:%s removed", *target)
                for target in wanted:
                    if target not in self.mirrors:
                        self.mirrors[target] = Mirror(self.hub, target)
                        self.mirrors[target].start()
            self.stop.wait(1)
        with self.lock:
            for mirror in self.mirrors.values():
                mirror.stop.set()

    def forward(self, message):
        with self.lock:
            mirrors = list(self.mirrors.values())
        for mirror in mirrors:
            mirror.forward(message)

    def states(self):
        with self.lock:
            return [{"host": f"{t[0]}:{t[1]}", "state": m.state, "live": m.live} for t, m in self.mirrors.items()]


class Mirror(threading.Thread):
    """Connects to one desktop GUI's receiver as if it were the Pi and forwards live traffic.

    The hub stays in charge of the sensor configuration. The dashboard's SET_CONFIG is
    acknowledged only when its sensor assignments match the hub's, and zone messages are
    renumbered to the dashboard's revision so its existing checks accept them.
    """
    def __init__(self, hub, target):
        super().__init__(daemon=True, name=f"mirror-{target[0]}")
        self.hub, self.target = hub, target
        self.queue = queue.Queue(maxsize=256)
        self.stop = threading.Event()
        self.connected = threading.Event()
        self.state = "Starting"
        self.live = False
        self.codec = None

    def _set_state(self, text, live=False):
        self.live = live
        if text != self.state:
            logging.info("Dashboard mirror %s:%s: %s", *self.target, text)
            self.state = text

    def forward(self, message):
        if not self.connected.is_set():
            return
        try:
            self.queue.put_nowait((time.monotonic(), message))
        except queue.Full:
            pass

    def _drain(self):
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return

    def run(self):
        target = self.target
        while not self.stop.is_set():
            try:
                connection = socket.create_connection(target, timeout=2)
            except OSError:
                self._set_state("Waiting for the dashboard (start its receiver on this port)")
                self.stop.wait(3)
                continue
            try:
                with connection:
                    self._session(connection, target)
            except (OSError, ValueError) as exc:
                if not self.state.startswith("Refused"):
                    self._set_state("Disconnected · retrying")
                logging.debug("Mirror connection: %s", exc)
            finally:
                self.connected.clear()
                self._drain()
            # A refused dashboard needs a person to fix its keys; don't hammer it every few seconds.
            self.stop.wait(30 if self.state.startswith("Refused") else 3)

    def _session(self, connection, target):
        connection.settimeout(.1)
        self._drain()
        self.connected.set()
        self._set_state("Connected · waiting for its sensor setup")
        # With hub keys every byte to and from this dashboard is EDC + HMAC; without keys, plaintext.
        self.codec = self.hub.edc.fresh() if self.hub.edc else None
        if self.hub.edc_error:
            self._set_state(self.hub.edc_error + " · not sending to dashboards")
            self.stop.wait(10)
            return
        buffer, dashboard, acknowledged = b"", None, None
        synced, next_sync_check = None, 0.0
        limit = EDC_MAX_WIRE if self.codec else 4096
        while not self.stop.is_set():
            try:
                data = connection.recv(4096)
                if not data:
                    raise ConnectionError("dashboard closed the connection")
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    message = self._receive(json.loads(line))
                    if isinstance(message, dict) and message.get("type") == "SET_CONFIG" and isinstance(message.get("revision"), int):
                        dashboard, acknowledged = {"revision": message["revision"], "mapping": message.get("mapping")}, None
                        if dashboard["mapping"] != self.hub.sensors["mapping"]:
                            synced = None  # the dashboard drifted (or just connected): resend the hub's setup
                if len(buffer) > limit:
                    raise ValueError("Dashboard message too large")
            except socket.timeout:
                pass
            if dashboard is None:
                continue  # send nothing until the dashboard has identified itself
            if time.monotonic() >= next_sync_check:
                # The dashboard adopts the hub's pet details and sensor setup; resend whenever they change.
                next_sync_check = time.monotonic() + 1
                sync = self.hub.sync_message()
                if sync != synced:
                    self._send(connection, {**sync, "timestamp": datetime.now().isoformat()})
                    synced = sync
            with self.hub.lock:
                hub_mapping, hub_revision = dict(self.hub.sensors["mapping"]), self.hub.sensors["revision"]
            matches = dashboard is not None and dashboard["mapping"] == hub_mapping
            if dashboard and not matches:
                self._set_state("Connected · sensor assignments differ from the hub; set Sensors 1-4 the same in both")
            elif matches and acknowledged != dashboard["revision"]:
                acknowledged = dashboard["revision"]
                self._send(connection, {"type": "CONFIG_ACK", "revision": acknowledged, "timestamp": datetime.now().isoformat()})
            if matches:
                self._set_state("Connected · dashboard receiving live data" + (" (EDC + HMAC)" if self.codec else " (unencrypted)"), live=True)
            for _ in range(64):
                try:
                    queued, message = self.queue.get_nowait()
                except queue.Empty:
                    break
                if time.monotonic() - queued > 3 or message.get("type") == "CONFIG_ACK":
                    continue
                if message.get("type") in {"SENSOR_READING", "ZONE_EVENT"}:
                    if not matches or acknowledged is None or message.get("revision") != hub_revision:
                        continue
                    message = {**message, "revision": acknowledged}
                self._send(connection, message)

    def _receive(self, packet):
        encrypted = isinstance(packet, dict) and packet.get("type") == "EDC_PACKET"
        if self.codec and not encrypted:
            self._set_state("Refused · this dashboard is not encrypted. On that laptop run main.py --setup-edc with the same keys")
            raise ConnectionError("plaintext dashboard refused")
        if encrypted and not self.codec:
            self._set_state("Refused · this dashboard requires EDC but the hub has no keys (~/.config/petcare/edc.env)")
            raise ConnectionError("hub has no EDC keys")
        if encrypted:
            try:
                return self.codec.decode(packet)
            except ValueError as exc:
                self._set_state(f"Refused · packet from the dashboard failed verification ({exc})")
                raise ConnectionError("dashboard packet rejected") from exc
        return packet

    def _send(self, connection, message):
        packet = self.codec.encode(message) if self.codec else message
        connection.settimeout(2)
        connection.sendall((json.dumps(packet, allow_nan=False) + "\n").encode())
        connection.settimeout(.1)


# ============================================================
# HTTP API + phone web app
# ============================================================
class Sessions:
    """Signed-in phones. A session made by the encrypted login carries its own EDC + HMAC codec."""
    def __init__(self):
        self.lock = threading.Lock()
        self.tokens = {}
        self.failures = {}
        self.challenges = {}
        self.secret = secrets.token_bytes(32)   # fake salts for unknown usernames
        self.channels = {}                      # token -> {"codec", "stats"}

    def create(self, username, codec=None):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.tokens[token] = (username, time.time() + SESSION_SECONDS)
            if codec:
                self.channels[token] = {"codec": codec, "stats": {"started": now_iso(), "requests": 0, "rejected": 0,
                                                                 "bytes_in": 0, "bytes_out": 0, "last": None}}
        return token

    def channel(self, token):
        with self.lock:
            return self.channels.get(token or "")

    def challenge(self, username):
        nonce = secrets.token_hex(16)
        with self.lock:
            now = time.time()
            self.challenges = {n: c for n, c in self.challenges.items() if c[1] > now}
            if len(self.challenges) > 200:
                raise ApiError(429, "Too many sign-in attempts. Try again in a minute.")
            self.challenges[nonce] = (username, now + CHALLENGE_SECONDS)
        return nonce

    def take_challenge(self, nonce, username):
        with self.lock:
            entry = self.challenges.pop(str(nonce), None)
        return bool(entry and entry[0] == username and entry[1] > time.time())

    def user(self, token):
        with self.lock:
            entry = self.tokens.get(token or "")
            if entry and entry[1] > time.time():
                return entry[0]
            self.tokens.pop(token or "", None)
            self.channels.pop(token or "", None)
        return None

    def end(self, token):
        with self.lock:
            self.tokens.pop(token or "", None)
            self.channels.pop(token or "", None)

    def end_user(self, username, keep=None):
        with self.lock:
            self.tokens = {t: e for t, e in self.tokens.items() if e[0] != username or t == keep}
            self.channels = {t: c for t, c in self.channels.items() if t in self.tokens}

    def locked(self, ip):
        with self.lock:
            count, until = self.failures.get(ip, (0, 0))
            return time.time() < until

    def record(self, ip, success):
        with self.lock:
            if success:
                self.failures.pop(ip, None)
                return
            count, _ = self.failures.get(ip, (0, 0))
            count += 1
            self.failures[ip] = (0, time.time() + 60) if count >= 5 else (count, 0)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def derive_session_keys(password_hash, nonce, client_nonce):
    """Both sides compute these from the stored PBKDF2 hash and the two login nonces; no key is ever sent."""
    master = hmac.new(password_hash, f"session|{nonce}|{client_nonce}".encode(), "sha256").digest()
    edc_key = hmac.new(master, b"edc", "sha256").hexdigest().upper()[:32]   # 0-9 A-F fits the EDC alphabet
    mac_key = hmac.new(master, b"mac", "sha256").digest()
    return EdcCodec(edc_key, mac_key, PHONE_MAX_JSON)


class Handler(BaseHTTPRequestHandler):
    server_version = "PetCareHub/1.0"
    hub: Hub = None
    sessions: Sessions = None
    _capture = None        # set while answering a request that arrived inside an EDC packet
    _secure_body = None

    def log_message(self, fmt, *args):
        logging.debug("HTTP %s " + fmt, self.client_address[0], *args)

    # ----- helpers -----
    def _headers(self, status, content_type, length, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        if not any(k.lower() == "cache-control" for k in (extra or {})):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _json(self, payload, status=200, extra=None):
        if self._capture is not None:
            self._capture.append((status, payload, extra or {}))
            return
        body = json.dumps(payload).encode()
        self._headers(status, "application/json", len(body), extra)
        self.wfile.write(body)

    _gzip_cache = {}

    def _file(self, path, content_type, max_age=0):
        try:
            body = path.read_bytes()
        except OSError:
            return self._json({"error": "Not found"}, 404)
        extra = {"Cache-Control": f"public, max-age={max_age}"} if max_age else {}
        if len(body) > 32_768 and "gzip" in self.headers.get("Accept-Encoding", ""):
            # The 3D library is 1.3 MB; compress once and reuse (about a quarter of the size).
            key = (str(path), path.stat().st_mtime)
            if key not in self._gzip_cache:
                import gzip
                self._gzip_cache[key] = gzip.compress(body, 6)
            body = self._gzip_cache[key]
            extra.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        self._headers(200, content_type, len(body), extra)
        self.wfile.write(body)

    def _token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["petcare_session"].value if "petcare_session" in cookie else None

    def _user(self):
        token = self._token()
        user = self.sessions.user(token)
        if not user:
            raise ApiError(401, "Please sign in")
        if self._capture is None and self.sessions.channel(token):
            raise ApiError(403, "This session only accepts EDC-encrypted requests")
        return user

    def _ip(self):
        """Client address; behind `tailscale serve` the real one is in X-Forwarded-For."""
        ip = self.client_address[0]
        if ip in ("127.0.0.1", "::1") and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].split(",")[0].strip()[:64]
        return ip

    def _body(self):
        if self._capture is not None:
            if not isinstance(self._secure_body, dict):
                raise ApiError(400, "Expected a JSON object")
            return self._secure_body
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "Request too large")
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise ApiError(400, "Invalid JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "Expected a JSON object")
        return data

    def _dispatch(self, routes):
        path = urlparse(self.path).path
        handler = routes.get(path)
        try:
            if handler is None:
                raise ApiError(404, "Not found")
            handler()
        except ApiError as exc:
            self._json({"error": exc.message}, exc.status)
        except (KeyError, TypeError, ValueError) as exc:
            self._json({"error": str(exc) or "Invalid request"}, 400)
        except Exception:
            logging.exception("API error on %s", path)
            self._json({"error": "Server error"}, 500)

    # ----- routing -----
    def do_GET(self):
        self._dispatch(self._get_routes())

    def _get_routes(self):
        return {
            "/": lambda: self._file(WEB_DIR / "index.html", "text/html; charset=utf-8"),
            "/index.html": lambda: self._file(WEB_DIR / "index.html", "text/html; charset=utf-8"),
            "/manifest.webmanifest": lambda: self._file(WEB_DIR / "manifest.webmanifest", "application/manifest+json"),
            "/icon.svg": lambda: self._file(WEB_DIR / "icon.svg", "image/svg+xml"),
            "/edc.js": lambda: self._file(WEB_DIR / "edc.js", "text/javascript; charset=utf-8"),
            "/twin.js": lambda: self._file(WEB_DIR / "twin.js", "text/javascript; charset=utf-8"),
            "/vendor/three.module.js": lambda: self._file(WEB_DIR / "vendor" / "three.module.js", "text/javascript; charset=utf-8", 604800),
            "/vendor/OrbitControls.js": lambda: self._file(WEB_DIR / "vendor" / "OrbitControls.js", "text/javascript; charset=utf-8", 604800),
            "/api/session": self.get_session,
            "/api/status": lambda: (self._user(), self._json(self.hub.status())),
            "/api/activity": self.get_activity,
            "/api/alerts": self.get_alerts,
            "/api/settings": self.get_settings,
            "/api/login/challenge": self.get_challenge,
            "/api/security": self.get_security,
        }

    def do_POST(self):
        # Custom header blocks cross-site form posts (CSRF) on top of SameSite cookies.
        if self.headers.get("X-PetCare") != "1":
            return self._json({"error": "Missing request header"}, 403)
        self._dispatch(self._post_routes())

    def _post_routes(self):
        return {
            "/api/secure": self.post_secure,
            "/api/login": self.post_login,
            "/api/logout": self.post_logout,
            "/api/alerts/read": self.post_alerts_read,
            "/api/alerts/clear": self.post_alerts_clear,
            "/api/access/request": lambda: (self._user(), self.hub.request_otp(self._body().get("username", "")), self._json({"ok": True})),
            "/api/access/verify": lambda: (self._user(), self._json({"ok": self.hub.verify_otp(self._body().get("code", ""))})),
            "/api/settings/pet": self.post_pet,
            "/api/pets/save": lambda: (self._user(), self._json({"ok": True, "id": self.hub.save_pet(self._body())})),
            "/api/pets/delete": lambda: (self._user(), self.hub.delete_pet(self._body().get("id")), self._json({"ok": True})),
            "/api/pets/monitor": lambda: (self._user(), self.hub.set_monitored(self._body().get("id")), self._json({"ok": True})),
            "/api/reminders/save": lambda: (self._user(), self.hub.save_reminder(self._body()), self._json({"ok": True})),
            "/api/reminders/done": lambda: (self._user(), self.hub.complete_reminder(self._body().get("id")), self._json({"ok": True})),
            "/api/reminders/delete": lambda: (self._user(), self.hub.delete_reminder(self._body().get("id")), self._json({"ok": True})),
            "/api/settings/sensors": self.post_sensors,
            "/api/settings/calibration": self.post_calibration,
            "/api/settings/telegram": self.post_telegram,
            "/api/settings/telegram/test": self.post_telegram_test,
            "/api/settings/notifications": self.post_notifications,
            "/api/account/password": self.post_password,
            "/api/settings/mirror": self.post_mirror,
            "/api/settings/layout": lambda: (self._user(), self.hub.save_layout(self._body().get("layout") or {}), self._json({"ok": True})),
        }

    # ----- auth: challenge-response, the password never leaves the phone -----
    def get_session(self):
        token = self._token()
        self._json({"user": self.sessions.user(token), "encrypted": bool(self.sessions.channel(token))})

    def get_challenge(self):
        """Step 1: the phone gets the PBKDF2 salt/iterations for its username and a one-time nonce."""
        if self.sessions.locked(self._ip()):
            raise ApiError(429, "Too many attempts. Try again in a minute.")
        username = parse_qs(urlparse(self.path).query).get("username", [""])[0].strip()[:40]
        record = self.hub.settings.data["users"].get(username)
        # Unknown usernames get a stable fake salt, so the reply does not reveal which accounts exist.
        salt = record["salt"] if record else hmac.new(self.sessions.secret, username.encode(), "sha256").hexdigest()[:32]
        iterations = record["iterations"] if record else PBKDF2_ITERATIONS
        self._json({"salt": salt, "iterations": iterations, "nonce": self.sessions.challenge(username)})

    def post_login(self):
        """Step 2: the phone proves it knows the password with HMAC(PBKDF2(password), nonces)."""
        ip = self._ip()
        if self.sessions.locked(ip):
            raise ApiError(429, "Too many attempts. Try again in a minute.")
        body = self._body()
        username, nonce = str(body.get("username", "")).strip(), str(body.get("nonce", ""))
        client_nonce, proof = str(body.get("client_nonce", "")), str(body.get("proof", ""))
        if not re.fullmatch(r"[0-9a-f]{32}", client_nonce) or not re.fullmatch(r"[0-9a-f]{64}", proof):
            raise ApiError(400, "Sign in with the current app version (refresh the page)")
        users = self.hub.settings.data["users"]
        record = users.get(username) or next(iter(users.values()))  # constant work for unknown users
        password_hash = bytes.fromhex(record["hash"])
        expected = hmac.new(password_hash, f"login|{nonce}|{client_nonce}".encode(), "sha256").hexdigest()
        valid = self.sessions.take_challenge(nonce, username) and username in users and hmac.compare_digest(expected, proof)
        self.sessions.record(ip, valid)
        logging.info("Login %s for %s", "succeeded" if valid else "failed", username or "(blank)")
        if not valid:
            raise ApiError(401, "Wrong username or password")
        token = self.sessions.create(username, derive_session_keys(password_hash, nonce, client_nonce))
        server_proof = hmac.new(password_hash, f"server|{nonce}|{client_nonce}".encode(), "sha256").hexdigest()
        self._json({"user": username, "server_proof": server_proof}, extra={"Set-Cookie":
                   f"petcare_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}"})

    # ----- EDC channel: every request and response after login -----
    def post_secure(self):
        token = self._token()
        channel = self.sessions.channel(token)
        if not self.sessions.user(token) or not channel:
            raise ApiError(401, "Please sign in")
        length = int(self.headers.get("Content-Length") or 0)
        if length > PHONE_MAX_PACKET:
            raise ApiError(413, "Request too large")
        codec, stats = channel["codec"], channel["stats"]
        try:
            packet = json.loads(self.rfile.read(length))
            request = codec.decode(packet)
            if request.get("type") != "API_REQUEST" or request.get("method") not in ("GET", "POST") \
                    or not str(request.get("path", "")).startswith("/api/") or request["path"].startswith("/api/secure"):
                raise ValueError("Invalid API request")
        except (ValueError, TypeError, KeyError) as exc:
            stats["rejected"] += 1
            logging.warning("Rejected phone packet: %s", exc)
            raise ApiError(400, "Packet rejected: " + str(exc)[:80])
        stats["requests"] += 1
        stats["bytes_in"] += length
        # Run the decrypted request through the normal routes, capturing the answer instead of sending it.
        self._capture, self._secure_body, original = [], request.get("body"), self.path
        self.path = request["path"]
        try:
            routes = self._get_routes() if request["method"] == "GET" else self._post_routes()
            self._dispatch(routes)
        finally:
            self.path = original
            captured, self._capture, self._secure_body = self._capture, None, None
        status, payload, extra = captured[0] if captured else (500, {"error": "No response"}, {})
        reply = codec.encode({"type": "API_RESPONSE", "path": request["path"], "status": status, "data": payload})
        stats["last"] = {"time": now_iso(), "path": request["path"].split("?")[0],
                         "request": {"ciphertext": packet["ciphertext"][:160], "tag": packet["tag"], "length": len(packet["ciphertext"])},
                         "response": {"ciphertext": reply["ciphertext"][:160], "tag": reply["tag"], "length": len(reply["ciphertext"])}}
        body = json.dumps(reply).encode()
        stats["bytes_out"] += len(body)
        headers = {k: v for k, v in extra.items() if k.lower() == "set-cookie"}
        if len(body) > 4096 and "gzip" in self.headers.get("Accept-Encoding", ""):
            import gzip
            body = gzip.compress(body, 5)   # EDC tokens repeat a lot; this makes them ~6x smaller on the wire
            headers.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        self._headers(200, "application/json", len(body), headers)
        self.wfile.write(body)

    def get_security(self):
        self._user()
        channel = self.sessions.channel(self._token())
        self._json({"cipher": "EDC (keyed 6x6 matrix, coordinate feedback, 4-token permutation) + HMAC-SHA256",
                    "key_exchange": "Challenge-response on the PBKDF2-SHA256 password hash; per-session keys via HMAC",
                    "replay": f"Timestamp within {EDC_MAX_SKEW} s and one-time nonce per packet",
                    "server": dict(channel["stats"]) if channel else None})

    def post_logout(self):
        self.sessions.end(self._token())
        self._json({"ok": True}, extra={"Set-Cookie": "petcare_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})

    def post_password(self):
        user = self._user()
        body = self._body()
        users = self.hub.settings.data["users"]
        if not check_password(users[user], str(body.get("current", ""))):
            raise ApiError(400, "Current password is wrong")
        new = str(body.get("new", ""))
        if len(new) < 8:
            raise ApiError(400, "Use at least 8 characters")
        with self.hub.settings.lock:
            users[user] = hash_password(new)
            self.hub.settings.data["must_change_password"] = False
            self.hub.settings.save()
        self.sessions.end_user(user, keep=self._token())
        self._json({"ok": True})

    # ----- data -----
    def get_activity(self):
        self._user()
        days = int(parse_qs(urlparse(self.path).query).get("days", ["7"])[0])
        self._json(self.hub.activity(max(1, min(days, 60))))

    def get_alerts(self):
        self._user()
        rows = self.hub.db.query("SELECT id,timestamp,severity,title,message,category,read,telegram_status FROM alerts ORDER BY rowid DESC LIMIT 100")
        keys = ("id", "timestamp", "severity", "title", "message", "category", "read", "telegram_status")
        self._json({"alerts": [dict(zip(keys, r)) for r in rows]})

    def post_alerts_read(self):
        self._user()
        alert_id = self._body().get("id")
        if alert_id:
            self.hub.db.query("UPDATE alerts SET read=1 WHERE id=?", (str(alert_id),))
        else:
            self.hub.db.query("UPDATE alerts SET read=1")
        self._json({"ok": True})

    def post_alerts_clear(self):
        self._user()
        self.hub.db.query("DELETE FROM alerts")
        self._json({"ok": True})

    # ----- settings -----
    def get_settings(self):
        self._user()
        s = self.hub.settings.data
        t = s["telegram"]
        self._json({"pet": s["pet"], "pets": s["pets"], "monitored_pet": s["monitored_pet"], "reminders": s["reminders"],
                    "layout": s["sensor_layout"], "pins": SENSOR_PINS, "avatars": AVATARS, "reminder_kinds": REMINDER_KINDS, "repeat_days": REPEAT_DAYS, "sensors": self.hub.sensors, "calibration": s["calibration"],
                    "dashboard_source": self.hub.dashboard_source,
                    "notifications": s["notifications"], "modes": MODES, "default_thresholds": MODE_THRESHOLDS, "mirror": s["mirror"],
                    "telegram": {"enabled": t["enabled"], "chat_id": t["chat_id"], "has_token": bool(t["token"])}})

    def post_pet(self):
        self._user()
        body = self._body()
        # Older clients: update the monitored pet's basics, keeping its other profile fields.
        current = dict(self.hub.settings.data["pet"])
        current.update({k: body[k] for k in ("name", "animal", "baseline_sleep_hours", "baseline_toilet_visits") if k in body})
        self.hub.save_pet(current)
        self._json({"ok": True})

    def post_sensors(self):
        self._user()
        if self.hub.dashboard_source:
            raise ApiError(409, "Change sensor assignments in the desktop GUI")
        body = self._body()
        self.hub.save_sensor_config({str(k): v for k, v in body["mapping"].items()},
                                    {str(k): v for k, v in body["thresholds"].items()})
        self._json({"ok": True})

    def post_calibration(self):
        self._user()
        body = self._body()
        sensor_id = str(body.get("sensor_id"))
        if sensor_id not in DEFAULT_MAPPING:
            raise ApiError(400, "Unknown sensor")
        with self.hub.settings.lock:
            calibration = self.hub.settings.data["calibration"]
            if body.get("remove"):
                calibration.pop(sensor_id, None)
            else:
                full, empty = body.get("full_cm"), body.get("empty_cm")
                if not finite(full, 0.1, 400) or not finite(empty, 0.1, 400) or not full < empty:
                    raise ApiError(400, "The empty distance must be larger than the full distance")
                calibration[sensor_id] = {"full_cm": float(full), "empty_cm": float(empty)}
            self.hub.settings.save()
        self._json({"ok": True})

    def post_telegram(self):
        self._user()
        body = self._body()
        with self.hub.settings.lock:
            t = self.hub.settings.data["telegram"]
            t["enabled"] = bool(body.get("enabled"))
            t["chat_id"] = str(body.get("chat_id", t["chat_id"])).strip()[:64]
            if body.get("remove_token"):
                t["token"] = ""
            elif str(body.get("token", "")).strip():
                t["token"] = str(body["token"]).strip()[:128]
            self.hub.settings.save()
        self._json({"ok": True})

    def post_telegram_test(self):
        self._user()
        ok, status = self.hub.telegram.send("PetCare AI test message. Notifications from your Raspberry Pi hub are working.")
        self._json({"ok": ok, "status": status})

    def post_mirror(self):
        self._user()
        if self.hub.dashboard_source:
            raise ApiError(409, "The desktop GUI is already the live data source")
        body = self._body()
        port = body.get("port", 5005)
        if not finite(port, 1, 65535) or int(port) != port:
            raise ApiError(400, "Port must be 1-65535")
        targets = parse_targets(body.get("hosts", ""), int(port))
        hosts = [host if p == port else f"{host}:{p}" for host, p in targets]
        with self.hub.settings.lock:
            self.hub.settings.data["mirror"] = {"hosts": hosts, "port": int(port)}
            self.hub.settings.save()
        self._json({"ok": True})

    def post_notifications(self):
        self._user()
        body = self._body()
        with self.hub.settings.lock:
            notifications = self.hub.settings.data["notifications"]
            for key in notifications:
                if key in body:
                    notifications[key] = bool(body[key])
            self.hub.settings.save()
        self._json({"ok": True})


# ============================================================
# Entry point
# ============================================================
def main(argv=None):
    parser = argparse.ArgumentParser(description="PetCare AI Raspberry Pi hub")
    parser.add_argument("--http-host", default="0.0.0.0", help="Phone app address to listen on")
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--sensor-host", default="127.0.0.1", help="Where predict_movement.py connects (keep 127.0.0.1)")
    parser.add_argument("--sensor-port", type=int, default=5005)
    parser.add_argument("--edc-keys", type=Path, default=DEFAULT_EDC_KEYS,
                        help="EDC_KEY / EDC_HMAC_KEY_HEX file for encrypting the laptop dashboard copy")
    parser.add_argument("--dashboard-source", action="store_true", help="Receive accepted readings from the desktop via a localhost SSH tunnel")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--set-password", metavar="USERNAME", help="Create or reset an account, then exit")
    parser.add_argument("--mirror", metavar="IPS", help='Save laptop dashboard addresses, e.g. "10.0.0.5,10.0.0.6" ("" turns it off), then exit')
    args = parser.parse_args(argv)
    if args.dashboard_source and args.sensor_host != "127.0.0.1":
        parser.error("Desktop source must listen on 127.0.0.1 inside the SSH tunnel")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(), RotatingFileHandler(args.data_dir / "hub.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(message)s")
    settings = SettingsStore(args.data_dir / "hub_settings.json")

    if args.set_password:
        username = args.set_password.strip()
        password = getpass.getpass(f"New password for {username}: ")
        if len(password) < 8 or password != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords must match and be at least 8 characters.")
        with settings.lock:
            settings.data["users"][username] = hash_password(password)
            settings.data["must_change_password"] = False
            settings.save()
        print(f"Password saved for {username}.")
        return 0

    if args.mirror is not None:
        try:
            targets = parse_targets(args.mirror)
        except ValueError as exc:
            raise SystemExit(str(exc))
        with settings.lock:
            settings.data["mirror"] = {"hosts": [f"{h}:{p}" if p != 5005 else h for h, p in targets], "port": 5005}
            settings.save()
        print("Dashboard laptops saved:", ", ".join(settings.data["mirror"]["hosts"]) or "none (off)")
        print("Restart the hub to apply: sudo systemctl restart petcare-hub")
        return 0

    db = Database(args.data_dir / "petcare_hub.db")
    hub = Hub(settings, db, Telegram(settings), dashboard_source=args.dashboard_source)
    hub.edc, hub.edc_error = load_edc_codec(args.edc_keys)
    if hub.edc_error:
        logging.error("%s; laptop dashboards will not receive data until this is fixed", hub.edc_error)
    else:
        logging.info("Laptop dashboard copy: %s", "EDC + HMAC encrypted" if hub.edc else
                     f"UNENCRYPTED (no keys in {args.edc_keys})")
    link = SensorLink(hub, args.sensor_host, args.sensor_port)
    link.start()
    if not link.ready.wait(3) or link.startup_error:
        raise SystemExit("Cannot start sensor link: " + (link.startup_error or "startup timed out"))
    if not args.dashboard_source:
        hub.mirror = MirrorManager(hub)
        hub.mirror.start()
    Handler.hub, Handler.sessions = hub, Sessions()
    httpd = ThreadingHTTPServer((args.http_host, args.http_port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
    logging.info("Phone app: http://<this Pi's IP>:%s", args.http_port)
    if args.dashboard_source:
        logging.info("[PETCARE_HUB_READY]")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        while not stop.wait(1):
            hub.tick()
    except KeyboardInterrupt:
        pass
    finally:
        logging.info("Stopping hub")
        link.stop.set()
        if hub.mirror:
            hub.mirror.stop.set()
        httpd.shutdown()
        hub.flush()
        hub.executor.shutdown(wait=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
