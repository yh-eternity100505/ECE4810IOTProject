"""Application orchestration. Widgets consume state and send commands only."""
import logging
import time
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from pet_monitoring.models import Alert, DailySummary, BehaviourEvent, SensorReading, SecurityEvent
from pet_monitoring.models.records import AREAS
from pet_monitoring.logic import anomaly_detection
from pet_monitoring.logic.behaviour_interpreter import validate_reading, current_behaviour
from pet_monitoring.logic.wellness_score import calculate, DEFAULT_WEIGHTS
from pet_monitoring.services.sensor_service import MockSensorService, RealSensorService
from pet_monitoring.services.ml_service import MockMLService, UnavailableMLService
from pet_monitoring.services.telegram_service import TelegramService
from pet_monitoring.services.credential_service import CredentialService
from pet_monitoring.services.authentication_service import AuthenticationService
from pet_monitoring.services.security_service import SecurityService

DEFAULT_SETTINGS = {
    "mock_mode": True, "refresh_seconds": 1.0, "theme": "Dark",
    "thresholds": {key: 25.0 for key in AREAS}, "anomaly_threshold": .30,
    "wellness_weights": DEFAULT_WEIGHTS.copy(),
    "telegram_mode": "Mock", "telegram_chat_id": "", "telegram_enabled": True,
    "notifications": {key: True for key in ("abnormal", "food", "water", "sleep", "inactivity", "litter", "sensor", "network", "ml", "security", "approach")}}


class MonitoringController(QObject):
    changed = Signal()
    settings_changed = Signal()
    message = Signal(str)
    telegram_result = Signal(str, bool, str)
    session_required = Signal()

    def __init__(self, database):
        super().__init__()
        self.db = database
        self.profile = database.profile()
        saved = database.get("settings", {})
        self.settings = {**DEFAULT_SETTINGS, **saved}
        for key in ("thresholds", "notifications", "wellness_weights"):
            self.settings[key] = {**DEFAULT_SETTINGS[key], **saved.get(key, {})}
        self.credentials = CredentialService(database)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="petcare-network")
        self.telegram_result.connect(self._telegram_done)
        self.telegram_status = "Mock" if self.settings["telegram_mode"] == "Mock" else "Not configured"
        self.network_online = True
        self.scenario = "normal"
        self.sensor = MockSensorService() if self.settings["mock_mode"] else RealSensorService()
        self.ml = MockMLService() if self.settings["mock_mode"] else UnavailableMLService()
        self.readings = RealSensorService().read()
        self.latest_event = BehaviourEvent("Waiting for samples", 0, datetime.now().isoformat(timespec="seconds"), "Unavailable", 0)
        self.location, self.behaviour = "Unknown", "Resting"
        if self.settings["mock_mode"]:
            self.db.seed_demo()
        self.summary = self._load_summary()
        self.alerts = self.db.alerts()
        self.events = self.db.behaviours()
        self.wellness = calculate(self.summary.values(), self.profile.baselines(), self.settings["wellness_weights"])
        self._conditions = set()
        self.security_events = self.db.security_events()
        self.security_warning = ""
        self.security_inspector = None
        self._capture_secure_demo = False
        self.security = SecurityService(event_sink=self.record_security_event)
        self.auth = AuthenticationService(database, self._auth_event)
        self.pet_id = database.get("pet_id", "PET001")
        self._previous_presence = set()
        self._previous_connections = {}
        self._last_tick = time.monotonic()
        self._last_event = 0.0
        self._last_sensor_save = 0.0
        self._last_summary_save = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.tick()

    def _load_summary(self):
        today = date.today().isoformat()
        desired = "demo" if self.settings["mock_mode"] else "live"
        summary = next((s for s in self.db.summaries(1, desired) if s.date == today), None)
        if summary and summary.source == desired:
            return summary
        if desired == "demo":
            return DailySummary(today, **self.profile.baselines(), source="demo")
        return DailySummary(today)

    def start(self):
        if not self.auth.is_authenticated:
            self.message.emit("Authentication Required")
            return
        self._last_tick = time.monotonic()
        self.tick()
        self.timer.start(round(self.settings["refresh_seconds"] * 1000))

    def pause(self):
        self.timer.stop()
        self.db.save_summary(self.summary)

    def close(self):
        self.pause()
        self.executor.shutdown(wait=False, cancel_futures=True)

    @Slot()
    def tick(self):
        if not self.auth.is_authenticated:
            if self.auth.session is not None:
                self.auth.expire_session()
                self.pause()
                self.session_required.emit()
            self.changed.emit()
            return
        now = time.monotonic()
        elapsed = min(now - self._last_tick, self.settings["refresh_seconds"] * 2)
        self._last_tick = now
        if self.summary.date != date.today().isoformat():
            self._evaluate_baseline()  # Only completed real days are comparable to full-day baselines.
            self.db.save_summary(self.summary)
            self.summary = DailySummary(date.today().isoformat(), source="demo" if self.settings["mock_mode"] else "live")
        try:
            samples = self.sensor.read()
            by_id = {r.sensor_id: r for r in samples}
            timestamp = datetime.now().isoformat(timespec="seconds")
            outgoing = [validate_reading(by_id.get(key, SensorReading(key, name, None, False, timestamp, "Offline")),
                                        self.settings["thresholds"][key]) for key, name in AREAS.items()]
        except Exception:
            logging.error("Sensor acquisition failed")
            outgoing = RealSensorService().read()
        accepted = []
        for reading in outgoing:
            try:
                payload = self.sensor_payload(reading)
                packet = self.security.create_secure_packet(payload, "SENSOR_" + reading.sensor_id.upper())
                result = self.receive_secure_packet(packet.to_wire())
            except Exception:
                self._security_offline()
                self.changed.emit()
                return
            if not result.accepted:
                self.changed.emit()
                return
            accepted.append(validate_reading(SensorReading(**result.data["reading"]), self.settings["thresholds"][reading.sensor_id]))
            if self._capture_secure_demo and reading.sensor_id == "food":
                self._capture_inspector(payload, packet, result)
        self.readings = accepted
        self._capture_secure_demo = False
        present = {r.sensor_id for r in self.readings if r.detected}
        for reading in self.readings:
            previous = self._previous_connections.get(reading.sensor_id)
            if previous != reading.connection_status:
                logging.info("Sensor %s: %s", reading.sensor_id, reading.connection_status)
            self._previous_connections[reading.sensor_id] = reading.connection_status
            condition = "sensor:" + reading.sensor_id
            if reading.connection_status != "Connected":
                self._raise_once(condition, Alert("CRITICAL", "Sensor disconnected", f"{reading.sensor_name}: {reading.connection_status}. Behaviour observations may be incomplete.", category="sensor"))
            else:
                self._conditions.discard(condition)
        for key in ("food", "water", "litter"):
            if key in present - self._previous_presence:
                setattr(self.summary, key, getattr(self.summary, key) + 1)
        if "sleep" in present:
            self.summary.sleep += elapsed / 3600
        elif present:
            self.summary.activity += elapsed / 3600
        self._previous_presence = present
        try:
            self.latest_event = self.ml.predict({"readings": self.readings, "scenario": self.scenario,
                                          "daily": self.summary.values()})
        except Exception:
            logging.exception("ML inference failed")
            self.latest_event = UnavailableMLService().predict({})
        ml_available = self.latest_event.model_name != "Unavailable"
        if not ml_available and self.settings["mock_mode"]:
            self._raise_once("ml", Alert("WARNING", "ML model unavailable", "Classification is unavailable. Check the model adapter.", category="ml"))
        if self.latest_event.classification == "Abnormal Movement":
            self._raise_once("movement", Alert("WARNING", "Abnormal movement", "Unusual movement detected in sleeping area. Continue monitoring your pet.", category="abnormal"))
        else:
            self._conditions.discard("movement")
        self.location, self.behaviour = current_behaviour(self.readings, self.latest_event.classification)
        self.wellness = calculate(self.summary.values(), self.profile.baselines(), self.settings["wellness_weights"],
                                 self.latest_event.classification == "Abnormal Movement", self.connected_count < 5 or not self.network_online)
        if not self.events or self.events[0].classification != self.latest_event.classification or now - self._last_event >= 10:
            self.db.save_behaviour(self.latest_event)
            self.events.insert(0, self.latest_event)
            self.events = self.events[:100]
            self._last_event = now
            logging.info("Behaviour classification: %s (%.0f%%)", self.latest_event.classification, self.latest_event.confidence * 100)
        if now - self._last_sensor_save >= 5:
            self.db.save_sensor_batch(self.readings)
            self._last_sensor_save = now
        if now - self._last_summary_save >= 5:
            self.db.save_summary(self.summary)
            self._last_summary_save = now
        self.changed.emit()

    @property
    def connected_count(self):
        return sum(r.connection_status == "Connected" for r in self.readings)

    @property
    def security_status(self):
        if not self.auth.is_authenticated:
            return "Authentication Required"
        if not self.security.available:
            return "Security Module Offline"
        return "Verification Failed" if self.security_warning else "Secure" if self.security.metrics.accepted_packets else "Waiting for packets"

    def sensor_payload(self, reading):
        return {"schema": 1, "pet_id": self.pet_id, "pet_name": self.profile.name, "reading": asdict(reading)}

    def record_security_event(self, event):
        self.security_events.insert(0, event)
        self.security_events = self.security_events[:200]
        self.db.save_security_event(event)
        logging.log(logging.WARNING if event.verification_status == "REJECTED" else logging.INFO,
                    "Security %s packet=%s source=%s result=%s latency_ms=%.3f",
                    event.event_type, event.packet_id, event.source_id, event.verification_status, event.total_ms)

    def _auth_event(self, kind):
        self.record_security_event(SecurityEvent(kind, datetime.now().isoformat(timespec="seconds")))
        if kind == "REPEATED_AUTHENTICATION_FAILURE":
            self._raise_once("auth_failures:" + str(time.monotonic()), Alert("WARNING", "Repeated authentication failure",
                "Several login attempts were rejected. Check account access; passwords are never recorded.", category="security"))

    def _security_offline(self):
        self.security.available = False
        self.security_warning = "Security Module Offline. Sensor updates paused."
        self.record_security_event(SecurityEvent("SECURITY_MODULE_OFFLINE", datetime.now().isoformat(timespec="seconds"), verification_status="REJECTED"))
        self._raise_once("security_offline", Alert("CRITICAL", "Security module offline",
            "Sensor packets could not be secured. No plaintext fallback is used; previous readings are retained.", category="security"))

    def receive_secure_packet(self, wire):
        """Receiver entry point. Only accepted results can be applied by tick()."""
        if not self.auth.is_authenticated:
            self.record_security_event(SecurityEvent("PACKET_REJECTED_SESSION", datetime.now().isoformat(timespec="seconds"), verification_status="REJECTED"))
            from pet_monitoring.models.secure_packet import PacketResult
            return PacketResult(False, "Authentication Required")
        result = self.security.process_secure_packet(wire)
        if not result.accepted:
            integrity = result.reason == "PACKET_REJECTED_HMAC_FAILURE"
            self.security_warning = ("Tampered IoT packet detected. Integrity verification failed. Packet rejected."
                                     if integrity else "Invalid secure packet rejected. Previous sensor readings retained.")
            title = "IoT Packet Integrity Failure" if integrity else "Malformed encrypted packet" if result.reason == "PACKET_REJECTED_MALFORMED" else "Security verification failed"
            source = result.packet.source_id if result.packet else "unknown source"
            self._raise_once("security:" + str(time.monotonic()), Alert("CRITICAL", title,
                f"{self.security_warning} Source: {source}.", category="security"))
        return result

    def clear_security_warning(self):
        if self.security.available and self.security.last_reason == "PASSED":
            self.security_warning = ""
            self.changed.emit()

    def _capture_inspector(self, original, packet, result):
        self.security_inspector = {
            "original": json.dumps(original, ensure_ascii=False, indent=2),
            "ciphertext": packet.ciphertext, "tag": packet.authentication_tag,
            "verification": "PASSED" if result.accepted else "FAILED · " + result.reason,
            "decrypted": json.dumps(result.data, ensure_ascii=False, indent=2) if result.accepted else "Not decrypted · packet rejected",
            "encryption_ms": self.security.last_encryption_ms, "verification_ms": result.verification_ms,
            "decryption_ms": result.decryption_ms, "total_ms": self.security.last_encryption_ms + result.total_ms}

    def simulate_secure_packet(self, tamper=False):
        if not self.auth.is_authenticated or not self.settings["mock_mode"]:
            self.message.emit("An authenticated session and Mock Mode are required.")
            return
        if tamper:
            reading = SensorReading("food", AREAS["food"], 14.3, True, datetime.now().isoformat(timespec="seconds"))
            original = self.sensor_payload(reading)
            try:
                packet = self.security.create_secure_packet(original, "SENSOR_FOOD")
                cipher = bytearray(base64.b64decode(packet.ciphertext))
                cipher[0] ^= 1
                packet.ciphertext = base64.b64encode(cipher).decode()
                result = self.receive_secure_packet(packet.to_wire())
                self._capture_inspector(original, packet, result)
                self.message.emit(self.security_warning)
            except Exception:
                self._security_offline()
        else:
            self.sensor.scenario = "secure_food"
            self.scenario = "secure_food"
            self._capture_secure_demo = True
            self.tick()
            self.clear_security_warning()
            if self.security_inspector and self.security_inspector["verification"] == "PASSED" and not self.security_warning:
                self.db.save_sensor_batch(self.readings)
                self.db.save_summary(self.summary)
                self.db.save_behaviour(self.latest_event)
                self.message.emit("Secure sensor packet verified and decrypted. Food station updated.")
            else:
                self.message.emit("Secure packet demo failed. Previous readings retained; check Security status.")
        self.changed.emit()

    def _raise_once(self, condition, alert):
        if condition in self._conditions:
            return
        self._conditions.add(condition)
        self.alerts.insert(0, alert)
        self.db.save_alert(alert)
        logging.warning("Alert: %s", alert.title)
        if self.settings["notifications"].get(alert.category, True):
            alert.telegram_status = "Sending…"
            self.db.save_alert(alert)
            service = self._telegram_service()
            self._submit(alert.id, lambda: service.send_alert(alert))
        else:
            alert.telegram_status = "Disabled · category"
            self.db.save_alert(alert)

    def _evaluate_baseline(self):
        unusual = anomaly_detection.detect(self.summary.values(), self.profile.baselines(), self.profile.name,
                                            self.settings["anomaly_threshold"])
        active = {"baseline:" + a.category for a in unusual}
        self._conditions = {c for c in self._conditions if not c.startswith("baseline:") or c in active}
        for alert in unusual:
            self._raise_once("baseline:" + alert.category, alert)

    def _telegram_service(self):
        service = TelegramService(self.settings["telegram_mode"], self.credentials.load(),
                                  self.settings["telegram_chat_id"], self.settings["telegram_enabled"])
        service.network_available = self.network_online
        return service

    def _submit(self, identifier, function):
        future = self.executor.submit(function)
        def done(result):
            try:
                success, status = result.result()
            except Exception:
                logging.warning("Notification task failed")
                success, status = False, "Failed"
            self.telegram_result.emit(identifier, success, status)
        future.add_done_callback(done)

    @Slot(str, bool, str)
    def _telegram_done(self, identifier, success, status):
        self.telegram_status = "Mock" if success and self.settings["telegram_mode"] == "Mock" else "Connected" if success else status
        alert = next((a for a in self.alerts if a.id == identifier), None)
        if alert:
            alert.telegram_sent, alert.telegram_status = success, status
            self.db.save_alert(alert)
            if alert.category == "security":
                self.record_security_event(SecurityEvent("TELEGRAM_ALERT_SENT" if success else "TELEGRAM_ALERT_FAILED",
                    datetime.now().isoformat(timespec="seconds"), verification_status="SENT" if success else "FAILED"))
        if identifier == "test":
            self.message.emit("Telegram: " + status)
        self.changed.emit()

    def test_telegram(self):
        self.message.emit("Testing Telegram connection…")
        service = self._telegram_service()
        self._submit("test", service.test_connection)

    def save_profile(self, profile):
        self.profile = profile
        success = self.db.save_profile(profile)
        if self.settings["mock_mode"]:
            self._evaluate_baseline()
        self.tick()
        self.message.emit("Profile and baseline saved." if success else self.db.error)

    def save_settings(self, settings, new_token="", clear_token=False):
        old_mode = self.settings["mock_mode"]
        credential_notice = ""
        if new_token:
            try:
                stored = self.credentials.save(new_token)
                if not stored:
                    credential_notice = " Token kept for this session only; secure storage is unavailable."
            except OSError:
                credential_notice = " Token kept for this session only; credential encryption failed."
        elif clear_token:
            self.credentials.clear()
        self.settings = settings
        success = self.db.set("settings", settings)
        if settings["mock_mode"] != old_mode:
            self.db.save_summary(self.summary)
            self.sensor = MockSensorService() if settings["mock_mode"] else RealSensorService()
            self.ml = MockMLService() if settings["mock_mode"] else UnavailableMLService()
            self.scenario = "normal"
            self.network_online = True
            self._conditions.clear()
            self._previous_presence.clear()
            if settings["mock_mode"]:
                self.db.seed_demo()
            self.summary = self._load_summary()
        self.telegram_status = "Mock" if settings["telegram_mode"] == "Mock" else "Configured" if self.credentials.load() else "Not configured"
        if self.timer.isActive():
            self.start()
        self.settings_changed.emit()
        if settings["mock_mode"]:
            self._evaluate_baseline()
        self.tick()
        self.message.emit(("Settings saved." if success else self.db.error) + credential_notice)

    def simulate(self, scenario):
        if not self.auth.is_authenticated:
            self.message.emit("Authentication Required")
            return
        if not self.settings["mock_mode"]:
            self.message.emit("Enable Mock Mode before using demonstration scenarios.")
            return
        self.scenario = scenario
        self._capture_secure_demo = False
        if scenario == "normal":
            self.security_warning = ""
        self.sensor.reset()
        self.sensor.scenario = scenario
        self._conditions.clear()
        self._previous_presence.clear()
        self.network_online = scenario != "network_failure"
        self.summary = DailySummary(date.today().isoformat(), **self.profile.baselines(), source="demo")
        if scenario == "low_food":
            self.summary.food = self.profile.baseline_food_visits * .5
        elif scenario == "low_water":
            self.summary.water = self.profile.baseline_water_visits * .375
        elif scenario == "restless":
            self.summary.sleep = self.profile.baseline_sleep * .5
        elif scenario == "network_failure":
            self._raise_once("network", Alert("CRITICAL", "Network disconnected", "Network unavailable. Local monitoring continues; Telegram delivery failed.", category="network"))
        self._evaluate_baseline()
        self.tick()
        self.db.save_summary(self.summary)
        self.db.save_sensor_batch(self.readings)
        self.db.save_behaviour(self.latest_event)
        self.message.emit("Demo scenario: " + scenario.replace("_", " ") + ".")

    def mark_read(self, alert_id):
        alert = next((a for a in self.alerts if a.id == alert_id), None)
        if alert:
            alert.read = True
            self.db.save_alert(alert)
            self.changed.emit()

    def clear_alert(self, alert_id):
        self.alerts = [a for a in self.alerts if a.id != alert_id]
        self.db.delete_alert(alert_id)
        self.changed.emit()

    def clear_all_alerts(self):
        self.alerts.clear()
        self.db.clear_alerts()
        self.changed.emit()
