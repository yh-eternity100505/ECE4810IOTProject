from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
                              QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea, QWidget)
from .widgets import label, button
from .gesture_sensor_card import GestureSensorCard


class MovementDialog(QDialog):
    def __init__(self, movement, parent=None):
        super().__init__(parent)
        self.m = movement
        self.setWindowTitle("Raspberry Pi Hub · Connection & Gesture Access")
        self.resize(850, 730)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        layout.addWidget(label("Movement & Access", "title"))
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
        self.resize(900, 880)
        layout.addWidget(label("Saved EDC keys enable automatic verification and decryption on port 5005. EDC is experimental. Plaintext mode is insecure.", "muted", True))
        self.result = label("", "sectionTitle", True)
        layout.addWidget(self.result)
        self.state = label("", "muted", True)
        layout.addWidget(self.state)
        self.approach_status = label("", "muted", True)
        layout.addWidget(self.approach_status)
        layout.addWidget(label("Administrator access", "sectionTitle"))
        layout.addWidget(label("Movement detection does not grant administrator access. Sign in on the login page with your password and authenticator.", "muted", True))
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
        self.table.setRowCount(len(self.m.history))
        for row, event in enumerate(self.m.history):
            for column, value in enumerate((event["timestamp"], event["prediction"], f"{event['confidence']:.1%}")):
                self.table.setItem(row, column, QTableWidgetItem(value))
