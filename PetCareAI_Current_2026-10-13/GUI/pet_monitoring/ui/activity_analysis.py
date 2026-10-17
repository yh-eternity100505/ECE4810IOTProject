from PySide6.QtWidgets import QHBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView
from .widgets import Card, label
from .container_levels import ContainerLevels
from .pet_animation import PetAnimation, compact_animation


def duration_text(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds / 3600:.3f} h ({seconds // 3600}h {(seconds % 3600) // 60}m {seconds % 60}s)"


class ActivityAnalysis(Card):
    def __init__(self, hub):
        super().__init__("Sleep & toilet activity · live estimates")
        self.hub = hub
        self.source = label("", "muted")
        self.box.addWidget(self.source)
        self.levels = ContainerLevels(hub)
        self.box.addWidget(self.levels)
        self.fields = {}
        self.animations = {}
        row = QHBoxLayout()
        for mode, title in (("sleep", "Sleeping"), ("toilet", "Using toilet")):
            card = Card(title)
            card.setProperty("petTone", mode)
            animation = PetAnimation(mode)
            self.animations[mode] = animation
            compact_animation(card, animation)
            state = label("", "sectionTitle", True)
            session = label("", None, True)
            today = label("", None, True)
            for field in (state, session, today):
                card.box.addWidget(field)
            self.fields[mode] = state, session, today
            row.addWidget(card)
        self.box.addLayout(row)
        self.box.addWidget(label("Estimated from time below the assigned zone threshold. Ultrasonic presence does not confirm sleep or toileting. Missing echoes and stale readings stop accumulation.", "muted", True))
        self.history = QTableWidget(0, 3)
        self.history.setHorizontalHeaderLabels(["Day", "Activity", "Estimated hours"])
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history.setMinimumHeight(160)
        self.box.addWidget(self.history)
        hub.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.source.setText(f"{self.hub.mode} · thresholds follow each sensor's saved assignment")
        for mode, (state, session, today) in self.fields.items():
            active, valid, running, total = self.hub.activity(mode)
            self.animations[mode].set_active(active)
            state.setText(("Pet is sleeping (estimated)" if mode == "sleep" else "Pet is using toilet (estimated)") if active else "Not detected" if valid else "Unknown · no valid zone reading")
            session.setText("Current session: " + duration_text(running))
            today.setText("Today's total: " + duration_text(total))
        records = self.hub.activity_history()
        self.history.setRowCount(len(records))
        for row, (day, mode, seconds) in enumerate(records):
            for column, value in enumerate((day, "Sleeping" if mode == "sleep" else "Toilet", duration_text(seconds))):
                self.history.setItem(row, column, QTableWidgetItem(value))


def bind_analysis_page(page, hub):
    original = [page.box.itemAt(i) for i in range(1, page.box.count())]
    panel = ActivityAnalysis(hub)
    page.box.insertWidget(1, panel)
    def show_item(item, visible):
        if item.widget():
            item.widget().setVisible(visible)
        elif item.layout():
            for i in range(item.layout().count()):
                show_item(item.layout().itemAt(i), visible)
    def refresh():
        live = hub.mode == "Live Pi"
        if live:
            page.heading.setText(getattr(page, "live_heading", "Live activity analysis"))
        panel.setVisible(live)
        for item in original:
            show_item(item, not live)
    hub.changed.connect(refresh)
    page.c.changed.connect(refresh)
    refresh()
