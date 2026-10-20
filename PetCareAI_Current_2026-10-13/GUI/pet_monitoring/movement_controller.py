"""Movement telemetry and alerts; never an administrator authentication source."""
import json
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
        self.auth_status = "Administrator sign-in requires a password and OTP on the login page."
        # Inert compatibility attributes for older callers. No codes are issued.
        self.demo_code = ""
        self._challenge = None
        self.expiry_timer = QTimer(self)
        self.expiry_timer.timeout.connect(self.expire_challenge)
        self.expiry_timer.start(1000)

    def expire_challenge(self):
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
            self.gesture_seen = 0
            self.gesture_distance = None
            self.gesture_status = "Offline · receiver disconnected or stopped"
        self.changed.emit()

    def cancel_authentication(self):
        self._challenge = None
        self.demo_code = ""
        self.auth_status = "Administrator sign-in requires a password and OTP on the login page."
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
            self.auth_status = "Gesture detected. Sign in with the administrator password and OTP on the login page."
            self.authentication_requested.emit()
        else:
            self.state = "Pass-by recorded · no access action"
        self.changed.emit()

    def request_otp(self, username):
        """Legacy entry point: movement/Telegram must never grant admin access."""
        self.cancel_authentication()
        self.auth_status = "Use the administrator login page: password, then OTP."
        self.changed.emit()
        return False

    def verify_otp(self, code):
        self.cancel_authentication()
        self.auth_status = "Movement codes cannot sign in to the administrator account."
        self.changed.emit()
        return False
