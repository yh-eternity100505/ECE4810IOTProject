"""Movement telemetry, alerts and explicit gesture-plus-Telegram sign-in."""
import json
import threading
import time
from datetime import datetime
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from pet_monitoring.models import Alert
from pet_monitoring.services.movement_receiver import MovementReceiver, parse_message


class MovementController(QObject):
    changed = Signal()
    authentication_requested = Signal()
    authenticated = Signal()

    def __init__(self, controller):
        super().__init__(controller)
        self.c = controller
        self.receiver = MovementReceiver(self)
        self.receiver.received.connect(self.process)
        self.receiver.status.connect(self.set_connection)
        self.connection = "Receiver stopped"
        self.state = "Idle · waiting for movement"
        self.prediction = "—"
        self.confidence = None
        self.approach_alert_id = None
        self.gesture_distance = None
        self.gesture_seen = 0
        self.gesture_updated = "—"
        self.gesture_status = "No live readings yet · update the Pi script"
        self.history = controller.db.get("movement_history", [])
        self.auth_status = "Perform the authentication gesture, then enter your username to request a Telegram entrance code."
        self._access_lock = threading.RLock()
        self._gesture_until = 0
        self._access_generation = 0
        self._last_otp_request = -float("inf")
        self._access_attempts = 0
        # Inert compatibility attributes for older callers. No codes are issued.
        self.demo_code = ""
        self._challenge = None
        self.expiry_timer = QTimer(self)
        self.expiry_timer.timeout.connect(self.expire_challenge)
        self.expiry_timer.start(1000)

    def expire_challenge(self):
        if self._gesture_until and time.monotonic() >= self._gesture_until:
            self.cancel_authentication()
            self.auth_status = "Gesture access expired. Perform the gesture again."
            self.changed.emit()
        if self.gesture_seen and time.monotonic() - self.gesture_seen > 5:
            self.gesture_seen = 0
            self.gesture_distance = None
            self.gesture_status = "Offline · no readings for 5 seconds"
            self.changed.emit()

    @Slot(str)
    def set_connection(self, text):
        self.connection = text
        if text.startswith("Raspberry Pi connected"):
            self.gesture_seen = 0
            self.gesture_distance = None
            self.gesture_status = "Pi connected · waiting for Sensor 5 live readings (updated Pi script required)"
        if any(word in text.lower() for word in ("disconnected", "stopped", "lost", "invalid", "cannot")):
            self.cancel_authentication()
            self.gesture_seen = 0
            self.gesture_distance = None
            self.gesture_status = "Offline · receiver disconnected or stopped"
        self.changed.emit()

    def cancel_authentication(self):
        with self._access_lock:
            self._access_generation += 1
            self._gesture_until = 0
            self._challenge = None
            self.demo_code = ""
            self.auth_status = "Perform the authentication gesture before requesting a Telegram entrance code."
        self.changed.emit()

    def close(self):
        self.expiry_timer.stop()
        self.cancel_authentication()
        self.receiver.stop()

    @Slot(dict)
    def process(self, message):
        if message.get("type") in {"CONFIG_ACK", "SENSOR_READING", "ZONE_EVENT"}:
            return
        message = parse_message(json.dumps(message))
        kind = message["type"]
        if kind == "GESTURE_READING":
            self.gesture_distance = message["distance_cm"]
            self.gesture_seen = time.monotonic()
            self.gesture_updated = datetime.now().strftime("%H:%M:%S")
            states = {"WAITING": "Waiting · bring an object below 20 cm", "RECORDING": f"Recording · {message['samples']}/{message['total']} samples",
                      "CLASSIFYING": "Classifying movement", "WAIT_CLEAR": "Waiting for clear area · move beyond 20 cm"}
            self.gesture_status = states[message["state"]] if self.gesture_distance is not None else "No valid echo · check Sensor 5 wiring or target"
            self.state = states[message["state"]]
            self.changed.emit()
            return
        if kind != "MOVEMENT_RESULT":
            self.state = "Recording gesture · approximately 6 seconds" if kind in {"TRIGGERED", "RECORDING"} else "Idle · waiting for movement"
            self.changed.emit()
            return
        self.prediction, self.confidence = message["prediction"], message["confidence"]
        if not self.gesture_seen:
            self.gesture_status = "Gesture result received · live distance not received; check the Pi script version"
        self.state = "Result received"
        self.history.insert(0, {**message, "timestamp": datetime.now().isoformat(timespec="seconds")})
        self.history = self.history[:100]
        self.c.db.set("movement_history", self.history)
        if self.prediction == "APPROACH_STAY":
            self.cancel_authentication()
            self.state = "Suspicious approach · warning raised"
            alert = Alert("Warning", "Suspicious approach detected",
                          "ENTRANCE ALERT\n"
                          "Sensor 5 detected an approach-and-stay movement near the entrance.\n"
                          f"Classification: APPROACH_STAY\nConfidence: {self.confidence:.1%}\n"
                          "Please check the entrance. Access has not been granted.", category="approach")
            self.approach_alert_id = alert.id
            self.c._raise_once("movement:" + alert.id, alert)
            self.c.changed.emit()
        elif self.prediction == "AUTH_GESTURE":
            self.cancel_authentication()
            with self._access_lock:
                self._gesture_until = time.monotonic() + 120
                self._access_attempts = 0
                self.auth_status = "Gesture recognised. Enter your username and request a Telegram entrance OTP."
            self.receiver.send_door_command("AUTH_OK")
            self.authentication_requested.emit()
        else:
            self.cancel_authentication()
            self.state = "Pass-by recorded · no access action"
        self.changed.emit()

    @property
    def gesture_access_ready(self):
        return time.monotonic() < self._gesture_until

    def request_otp(self, username):
        """Request a delivered OTP after a fresh authentication gesture.

        Called by OtpRequest's worker; native storage and network only, no SQLite.
        """
        from pet_monitoring.services.authentication_service import STORAGE_ERRORS
        auth = self.c.auth
        with self._access_lock:
            if not self.gesture_access_ready:
                self.auth_status = "Perform the authentication gesture first."
                return False
            if time.monotonic() - self._last_otp_request < 30:
                self.auth_status = "Wait 30 seconds before requesting another entrance code."
                return False
            generation = self._access_generation
            try:
                with auth._exclusive():
                    record = auth._load()
                    if (record is None or username.strip() != record['username'] or record['v'] != 2
                            or time.time() < record['locked_until']):
                        self.auth_status = "Unable to send an entrance code for this username."
                        return False
                    token, chat_id = record['telegram_token'], record['telegram_chat_id']
                    version = auth._credential_version(record)
            except STORAGE_ERRORS:
                self.auth_status = "Saved Telegram credentials are unavailable. Administrator setup remains unchanged."
                return False
            code, challenge = auth._new_otp(min(self._gesture_until, time.monotonic()+120))
            try:
                with auth._exclusive():
                    record = auth._load()
                    if record is None or auth._credential_version(record) != version:
                        self.auth_status = "Credentials changed. Perform the gesture again."
                        return False
                    record['gesture_otp_id'] = challenge.identifier
                    auth._save(record)
            except STORAGE_ERRORS:
                self.auth_status = "Could not register the gesture sign-in code. Try again."
                return False
            self._challenge = (challenge, version)
            self._last_otp_request = time.monotonic()
        try:
            success, status = auth._telegram_sender(token, chat_id,
                f"PetCare AI GESTURE LOGIN code: {code}\nUsername: admin\nExpires in 2 minutes. Do not share this code.")
        except Exception:
            success, status = False, ""
        with self._access_lock:
            if generation != self._access_generation or self._challenge is None or self._challenge[0] is not challenge:
                return False
            challenge.delivered = success is True and self.gesture_access_ready and time.monotonic() < challenge.expires_at
            if not challenge.delivered:
                self._challenge = None
                self.auth_status = auth._delivery_error(status)
                return False
            self.auth_status = "Entrance OTP sent to your registered Telegram. Enter it below."
        self.changed.emit()
        return True

    def verify_otp(self, code):
        from pet_monitoring.services.authentication_service import STORAGE_ERRORS
        auth = self.c.auth
        with self._access_lock:
            if not self.gesture_access_ready or self._challenge is None:
                self.auth_status = "No valid entrance code. Perform the gesture and request a code."
                self.changed.emit()
                return False
            challenge, version = self._challenge
            valid_record = False
            try:
                with auth._exclusive():
                    record = auth._load()
                    valid_record = (record is not None and auth._credential_version(record) == version
                                    and time.time() >= record['locked_until'])
            except STORAGE_ERRORS:
                pass
            self._access_attempts += 1
            if not valid_record or not auth.complete_gesture_login(challenge, code, version):
                self.auth_status = "Entrance verification failed. Check the code and try again."
                if self._access_attempts >= 5 or not valid_record:
                    self.cancel_authentication()
                    self.auth_status = "Entrance verification cancelled. Perform the gesture again."
                self.changed.emit()
                return False
            self._challenge = None
            self._gesture_until = 0
            self._access_generation += 1
            self.auth_status = "Gesture and Telegram OTP verified. Signing in to the dashboard."
        self.receiver.send_door_command("AUTH_OK")
        self.receiver.send_door_command("OTP_OK")
        self.history.insert(0, {"type": "MOVEMENT_RESULT", "prediction": "ACCESS_GRANTED", "confidence": 1.0,
                               "timestamp": datetime.now().isoformat(timespec="seconds")})
        self.history = self.history[:100]
        self.c.db.set("movement_history", self.history)
        self.changed.emit()
        self.authenticated.emit()
        return True
