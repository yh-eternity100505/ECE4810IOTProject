import copy
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
                              QLineEdit, QComboBox, QCheckBox, QTabWidget)
from .widgets import Page, Card, label, button
from .pet_profile_page import number
from pet_monitoring.models.records import AREAS
from .security_panel import SecurityPanel


class SettingsPage(Page):
    def __init__(self, controller):
        super().__init__("Settings", "Connections, sensing thresholds, notifications, and presentation controls.")
        self.c = controller
        self.tabs = QTabWidget()
        self.box.addWidget(self.tabs)
        self.notifications = {}
        self.thresholds = {}
        self.weights = {}
        s = controller.settings
        integrations = self.tab("Connections")
        telegram = Card("Telegram notifications")
        telegram.box.addWidget(label("Mock mode logs notifications locally. Real mode sends messages using your bot credentials.", "muted", True))
        form = QFormLayout()
        self.telegram_mode = QComboBox()
        self.telegram_mode.addItems(["Mock", "Real"])
        self.telegram_mode.setCurrentText(s["telegram_mode"])
        form.addRow("Telegram mode", self.telegram_mode)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Token hidden · leave blank to keep" if controller.credentials.load() else "Enter bot token")
        form.addRow("Bot token", self.token)
        self.chat = QLineEdit(s["telegram_chat_id"])
        form.addRow("Chat ID", self.chat)
        self.telegram_enabled = QCheckBox("Enable Telegram notifications")
        self.telegram_enabled.setChecked(s["telegram_enabled"])
        form.addRow("", self.telegram_enabled)
        self.clear_token = QCheckBox("Remove saved bot token on save")
        form.addRow("", self.clear_token)
        telegram.box.addLayout(form)
        telegram.box.addWidget(button("Test saved Telegram connection", controller.test_telegram))
        self.telegram_status = label("", "muted", True)
        telegram.box.addWidget(self.telegram_status)
        telegram.box.addWidget(label("Save changes before testing. On Windows, tokens are protected with your user account's DPAPI encryption. On other platforms they stay in memory for this session.", "muted", True))
        integrations.addWidget(telegram)
        integrations.addStretch()
        sensing = self.tab("Sensors & score")
        sensor_card = Card("Presence thresholds")
        sensor_card.box.addWidget(label("Pet presence is detected when a valid distance is below the station threshold.", "muted", True))
        sensor_form = QFormLayout()
        for key, name in AREAS.items():
            field = number(s["thresholds"][key], 1, 400, " cm")
            sensor_form.addRow(name, field)
            self.thresholds[key] = field
        sensor_card.box.addLayout(sensor_form)
        sensing.addWidget(sensor_card)
        score_card = Card("Baseline comparison & wellness weights")
        score_form = QFormLayout()
        self.anomaly = number(s["anomaly_threshold"] * 100, 5, 95, "%", 0)
        score_form.addRow("Alert deviation threshold", self.anomaly)
        for key, value in s["wellness_weights"].items():
            field = number(value * 100, 0, 100, "%", 0)
            score_form.addRow(key.title() + " weight", field)
            self.weights[key] = field
        score_card.box.addLayout(score_form)
        score_card.box.addWidget(label("Weights are normalised automatically. At least one must be positive. Baseline alerts compare complete daily totals; demo scenarios evaluate immediately.", "muted", True))
        sensing.addWidget(score_card)
        sensing.addStretch()
        notifications = self.tab("Notifications")
        notify_card = Card("Notify me about")
        for key, name in [("abnormal", "Abnormal behaviour"), ("food", "Low food activity"), ("water", "Low water activity"),
                          ("sleep", "Unusual sleep"), ("inactivity", "Excessive activity / inactivity"),
                          ("litter", "Litter abnormality"), ("sensor", "Sensor disconnected"),
                          ("network", "Network disconnected"), ("ml", "ML anomaly / unavailable"),
                          ("security", "Security / tampered sensor packets"),
                          ("approach", "Entrance approach-and-stay alerts (Sensor 5)")]:
            field = QCheckBox(name)
            field.setChecked(s["notifications"][key])
            self.notifications[key] = field
            notify_card.box.addWidget(field)
        notifications.addWidget(notify_card)
        notifications.addWidget(label("These switches control Telegram delivery. Alerts are always recorded locally.", "muted", True))
        notifications.addStretch()
        system = self.tab("System")
        system_card = Card("Application preferences")
        system_form = QFormLayout()
        self.mock = QCheckBox("Use simulated sensors and demo ML")
        self.mock.setChecked(s["mock_mode"])
        system_form.addRow("Mock Mode", self.mock)
        self.refresh_rate = number(s["refresh_seconds"], .2, 60, " seconds")
        system_form.addRow("Refresh rate", self.refresh_rate)
        self.theme = QComboBox()
        self.theme.addItems(["Light", "Dark"])
        self.theme.setCurrentText(s["theme"])
        system_form.addRow("Theme", self.theme)
        system_card.box.addLayout(system_form)
        system_card.box.addWidget(label("With Mock Mode off, sensors remain offline until a hardware adapter is connected. The application continues to work without a model or network.", "muted", True))
        system.addWidget(system_card)
        system.addStretch()
        demo = self.tab("Demo Mode")
        demo_card = Card("University presentation controls")
        demo_card.box.addWidget(label("DEMO MODE · requires Mock Mode", "eyebrow"))
        demo_card.box.addWidget(label("Scenarios change sensor state, classification, daily totals, wellness, alerts, and notification delivery. History is retained when resetting.", "muted", True))
        grid = QGridLayout()
        self.demo_buttons = {}
        scenarios = [("normal", "Simulate Normal Day"), ("abnormal", "Simulate Abnormal Movement"),
                     ("low_food", "Simulate Low Food"), ("low_water", "Simulate Low Water"),
                     ("restless", "Simulate Restless Sleep"), ("sensor_failure", "Simulate Sensor Failure"),
                     ("network_failure", "Simulate Network Failure"), ("reset", "Reset Demo"),
                     ("secure_packet", "Simulate Secure Packet"), ("tampered_packet", "Simulate Tampered Packet")]
        for i, (scenario, title) in enumerate(scenarios):
            btn = button(title, lambda checked=False, name=scenario: self.run_demo(name))
            self.demo_buttons[scenario] = btn
            grid.addWidget(btn, i // 2, i % 2)
        demo_card.box.addLayout(grid)
        self.demo_status = label("Current scenario: normal", "muted")
        demo_card.box.addWidget(self.demo_status)
        demo.addWidget(demo_card)
        demo.addStretch()
        security = self.tab("Security")
        self.security_panel = SecurityPanel(controller)
        security.addWidget(self.security_panel)
        security.addStretch()
        actions = QHBoxLayout()
        self.feedback = label("", "muted", True)
        actions.addWidget(self.feedback, 1)
        actions.addWidget(button("Save settings", self.save, True))
        self.box.addLayout(actions)
        self.box.addStretch()
        controller.changed.connect(self.refresh)
        controller.settings_changed.connect(self.sync_mock)
        controller.message.connect(self.feedback.setText)
        self.refresh()

    def tab(self, title):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(18)
        self.tabs.addTab(widget, title)
        return layout

    def run_demo(self, scenario):
        if scenario in ("secure_packet", "tampered_packet"):
            self.c.simulate_secure_packet(tamper=scenario == "tampered_packet")
        else:
            self.c.simulate("normal" if scenario == "reset" else scenario)

    def sync_mock(self):
        self.mock.setChecked(self.c.settings["mock_mode"])

    def save(self):
        s = copy.deepcopy(self.c.settings)
        if sum(field.value() for field in self.weights.values()) == 0:
            self.feedback.setText("At least one wellness weight must be positive.")
            return
        s.update(mock_mode=self.mock.isChecked(), refresh_seconds=self.refresh_rate.value(), theme=self.theme.currentText(),
                 telegram_mode=self.telegram_mode.currentText(), telegram_chat_id=self.chat.text().strip(),
                 telegram_enabled=self.telegram_enabled.isChecked(), anomaly_threshold=self.anomaly.value() / 100)
        s["thresholds"] = {key: field.value() for key, field in self.thresholds.items()}
        s["notifications"] = {key: field.isChecked() for key, field in self.notifications.items()}
        s["wellness_weights"] = {key: field.value() / 100 for key, field in self.weights.items()}
        self.c.save_settings(s, self.token.text().strip(), self.clear_token.isChecked())
        self.token.clear()
        self.clear_token.setChecked(False)
        self.token.setPlaceholderText("Token hidden · leave blank to keep" if self.c.credentials.load() else "Enter bot token")

    def refresh(self):
        self.telegram_status.setText("Telegram status: " + self.c.telegram_status)
        self.demo_status.setText("Current scenario: " + self.c.scenario.replace("_", " "))
        for btn in self.demo_buttons.values():
            btn.setEnabled(self.c.settings["mock_mode"])
