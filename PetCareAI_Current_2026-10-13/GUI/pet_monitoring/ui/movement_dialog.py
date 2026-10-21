from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
                              QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea, QWidget)
from .widgets import label, button
from .gesture_sensor_card import GestureSensorCard
from .otp_request import OtpRequest


class MovementDialog(QDialog):
    sign_in_requested = Signal()
    def __init__(self, movement, parent=None, *, embedded=False):
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
            self.setObjectName("page")
        self.m = movement
        self.request = OtpRequest(self)
        self.request.finished.connect(self.otp_delivered)
        self.setWindowTitle("Raspberry Pi Hub · Connection & Gesture Access")
        if not embedded:
            self.resize(850, 730)
        outer = QVBoxLayout(self)
        if embedded:
            layout = outer
            layout.setContentsMargins(30, 26, 30, 24)
            layout.setSpacing(16)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            body = QWidget()
            layout = QVBoxLayout(body)
            scroll.setWidget(body)
            outer.addWidget(scroll)
        layout.addWidget(label("Door Authentication" if embedded else "Movement & Access", "title"))
        layout.addWidget(label("One Raspberry Pi runs four pet zones and Sensor 5 gesture KNN. This connection receives zone records and gesture results, and sends saved sensor assignments.", "muted", True))
        config = self.m.c.db.get("movement_receiver", {"host": "0.0.0.0", "port": 5005})
        row = QHBoxLayout()
        self.host = QLineEdit(config["host"])
        self.host.setPlaceholderText("Laptop listen address")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(config["port"])
        row.addWidget(label("Listen address"))
        row.addWidget(self.host)
        row.addWidget(label("Port"))
        row.addWidget(self.port)
        row.addWidget(button("Start receiver", self.start_receiver, True))
        row.addWidget(button("Stop", self.stop_receiver))
        layout.addLayout(row)
        self.connection = label("", "muted", True)
        layout.addWidget(self.connection)
        self.gesture_card = GestureSensorCard(movement)
        layout.addWidget(self.gesture_card)
        if not embedded:
            self.resize(900, 880)
        layout.addWidget(label("Saved EDC keys enable automatic verification and decryption on port 5005. EDC is experimental. Plaintext mode is insecure.", "muted", True))
        self.result = label("", "sectionTitle", True)
        layout.addWidget(self.result)
        self.state = label("", "muted", True)
        layout.addWidget(self.state)
        self.approach_status = label("", "muted", True)
        layout.addWidget(self.approach_status)
        layout.addWidget(label("Door gesture and Telegram OTP" if embedded else "Gesture authentication and dashboard sign-in", "sectionTitle"))
        layout.addWidget(label("Recognise your gesture, enter admin, request a Telegram OTP, then verify door access." if embedded else "Recognise your gesture, enter admin, request a Telegram OTP, then verify it to sign in to the dashboard.", "muted", True))
        self.username = QLineEdit("admin")
        self.username.setPlaceholderText("Registered username")
        self.username.setMaxLength(80)
        layout.addWidget(self.username)
        self.request_otp_button = button("Send gesture login OTP to Telegram", self.request_entrance_otp, True)
        layout.addWidget(self.request_otp_button)
        self.otp = QLineEdit()
        self.otp.setPlaceholderText("6-digit gesture login OTP from Telegram")
        self.otp.setEchoMode(QLineEdit.EchoMode.Password)
        self.otp.setMaxLength(6)
        layout.addWidget(self.otp)
        self.verify_otp_button = button("Verify door authentication" if embedded else "Verify OTP and open dashboard", self.verify_entrance_otp, True)
        layout.addWidget(self.verify_otp_button)
        self.otp.returnPressed.connect(self.verify_entrance_otp)
        self.continue_sign_in = button("Continue to administrator sign-in", self.sign_in_requested.emit)
        layout.addWidget(self.continue_sign_in)
        self.continue_sign_in.setVisible(not embedded)
        self.auth_status = label("", "muted", True)
        layout.addWidget(self.auth_status)
        layout.addWidget(label("Movement event history", "sectionTitle"))
        self.table = QTableWidget(0, 3)
        self.table.setMinimumHeight(180)
        self.table.setHorizontalHeaderLabels(["Received", "Prediction", "Confidence"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.m.changed.connect(self.refresh)
        self.m.c.changed.connect(self.refresh)
        self.refresh()

    def request_entrance_otp(self):
        if self.request.busy or not self.m.gesture_access_ready:
            return
        username = self.username.text().strip()
        self.auth_status.setText("Sending entrance OTP to Telegram...")
        def operation():
            success = self.m.request_otp(username)
            return success, self.m.auth_status
        self.request.start(operation)
        self.refresh()

    def otp_delivered(self, generation, success, message):
        if generation != self.request.generation:
            return
        self.request.busy = False
        self.refresh()
        self.auth_status.setText(message)
        if success:
            self.otp.setFocus()

    def verify_entrance_otp(self):
        if self.request.busy:
            return
        self.m.verify_otp(self.otp.text())
        self.otp.clear()
        self.refresh()

    def start_receiver(self):
        if self.m.receiver.start(self.host.text().strip(), self.port.value()):
            self.m.c.db.set("movement_receiver", {"host": self.host.text().strip(), "port": self.port.value()})

    def stop_receiver(self):
        self.m.cancel_authentication()
        self.m.receiver.stop()

    def refresh(self):
        self.connection.setText(self.m.connection)
        confidence = "—" if self.m.confidence is None else f"{self.m.confidence:.1%}"
        self.result.setText(f"{self.m.prediction}    /    Confidence: {confidence}")
        self.state.setText(self.m.state)
        approach = next((alert for alert in self.m.c.alerts if alert.id == self.m.approach_alert_id), None)
        self.approach_status.setText("Entrance alert Telegram: " + approach.telegram_status if approach else "")
        self.auth_status.setText(self.m.auth_status)
        self.continue_sign_in.setEnabled(self.m.prediction == "AUTH_GESTURE")
        self.request_otp_button.setEnabled(self.m.gesture_access_ready and not self.request.busy)
        self.verify_otp_button.setEnabled(self.m._challenge is not None and self.m._challenge[0].delivered and not self.request.busy)
        self.username.setEnabled(not self.request.busy)
        self.table.setRowCount(len(self.m.history))
        for row, event in enumerate(self.m.history):
            for column, value in enumerate((event["timestamp"], event["prediction"], f"{event['confidence']:.1%}")):
                self.table.setItem(row, column, QTableWidgetItem(value))
