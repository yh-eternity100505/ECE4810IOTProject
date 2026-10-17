from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QComboBox
from .widgets import Page, Card, label, table, fill_table, button, time_text


class AlertsPage(Page):
    def __init__(self, controller):
        super().__init__("Alerts & notifications", "Meaningful changes, connection issues, and delivery status in one place.")
        self.c = controller
        self.signature = None
        controls = QHBoxLayout()
        self.count = label("0 unread", "sectionTitle")
        controls.addWidget(self.count)
        controls.addStretch()
        self.filter = QComboBox()
        self.filter.addItems(["All", "Info", "Warning", "Critical"])
        self.filter.currentTextChanged.connect(self.refresh)
        controls.addWidget(self.filter)
        self.box.addLayout(controls)
        card = Card()
        self.table = table(["Severity", "Alert", "Timestamp", "Status", "Telegram"])
        self.table.setMinimumHeight(300)
        self.table.itemSelectionChanged.connect(self.show_detail)
        card.box.addWidget(self.table)
        self.detail = label("Select an alert to view its description.", None, True)
        card.box.addWidget(self.detail)
        row = QHBoxLayout()
        self.read_button = button("Mark as read", self.mark_read)
        self.clear_button = button("Clear alert", self.clear_selected)
        row.addWidget(self.read_button)
        row.addWidget(self.clear_button)
        row.addStretch()
        row.addWidget(button("Clear all alerts", controller.clear_all_alerts))
        card.box.addLayout(row)
        self.box.addWidget(card)
        self.box.addWidget(label("Alerts describe observed behaviour and system events. Continue monitoring your pet when patterns change.", "muted", True))
        self.box.addStretch()
        controller.changed.connect(self.refresh)
        self.refresh()

    def selected_id(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def show_detail(self):
        alert = next((a for a in self.c.alerts if a.id == self.selected_id()), None)
        self.detail.setText(alert.message if alert else "No alert selected. Select a row to read its description.")
        self.read_button.setEnabled(bool(alert and not alert.read))
        self.clear_button.setEnabled(bool(alert))

    def mark_read(self):
        self.c.mark_read(self.selected_id())

    def clear_selected(self):
        self.c.clear_alert(self.selected_id())

    def refresh(self):
        selection = self.filter.currentText().upper()
        alerts = [a for a in self.c.alerts if selection == "ALL" or a.severity == selection]
        self.count.setText(f"{sum(not a.read for a in self.c.alerts)} unread  ·  {len(self.c.alerts)} total")
        signature = [(a.id, a.read, a.telegram_status) for a in alerts]
        if signature != self.signature:
            self.signature = signature
            fill_table(self.table, [(a.severity, a.title, time_text(a.timestamp), "Read" if a.read else "Unread",
                       "✓ Telegram " + a.telegram_status if a.telegram_sent else "Telegram " + a.telegram_status) for a in alerts], [a.id for a in alerts])
        self.show_detail()
