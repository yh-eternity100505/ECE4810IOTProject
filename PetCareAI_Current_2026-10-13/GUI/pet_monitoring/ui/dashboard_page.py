from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QGridLayout
from .widgets import Page, Card, Metric, WellnessRing, label, STATUS_COLORS, time_text
from pet_monitoring.logic.wellness_score import DISCLAIMER
from pet_monitoring.logic.anomaly_detection import compare
from .security_panel import SecurityStatusCard


class DashboardPage(Page):
    def __init__(self, controller):
        super().__init__("Milo's overview", "A clear picture of daily routines and behavioural wellbeing.")
        self.c = controller
        top = QHBoxLayout()
        top.setSpacing(20)
        wellness = Card("Behavioural Wellness Score")
        wellness.setToolTip(DISCLAIMER)
        self.ring = WellnessRing()
        self.status = label("● NORMAL", "sectionTitle")
        wellness.box.addWidget(self.ring)
        wellness.box.addWidget(self.status)
        wellness.box.addWidget(label("Based on observed daily routines", "muted"))
        top.addWidget(wellness, 2)
        overview = Card("Right now")
        self.mode = label("DEMO DATA", "eyebrow")
        overview.box.addWidget(self.mode)
        self.location = label("Sleeping Area", "metric")
        self.behaviour = label("Sleeping", "sectionTitle")
        overview.box.addWidget(self.location)
        overview.box.addWidget(self.behaviour)
        overview.box.addStretch()
        self.assessment = label("", None, True)
        overview.box.addWidget(self.assessment)
        overview.box.addWidget(label("Behavioural insights only. No medical diagnosis.", "muted", True))
        top.addWidget(overview, 3)
        self.box.addLayout(top)
        summary = QHBoxLayout()
        summary.setSpacing(12)
        self.metrics = {}
        for key, name in [("sleep", "Sleep"), ("food", "Food"), ("water", "Water"), ("litter", "Litter"), ("activity", "Activity")]:
            card = Metric(name)
            card.box.setContentsMargins(16, 18, 16, 18)
            self.metrics[key] = card
            summary.addWidget(card)
        self.box.addLayout(summary)
        self.security_card = SecurityStatusCard(controller)
        self.box.addWidget(self.security_card)
        bottom = QHBoxLayout()
        bottom.setSpacing(20)
        today = Card("Today's summary")
        grid = QGridLayout()
        self.summaries = {}
        for row, (key, name) in enumerate([(k, k.title()) for k in self.metrics]):
            grid.addWidget(label(name, "muted"), row, 0)
            value = label("Normal")
            grid.addWidget(value, row, 1)
            self.summaries[key] = value
        today.box.addLayout(grid)
        bottom.addWidget(today, 2)
        latest = Card("Latest alert")
        self.alert_title = label("All quiet", "sectionTitle")
        self.alert_message = label("No unusual behaviour detected.", None, True)
        self.alert_time = label("", "muted", True)
        latest.box.addWidget(self.alert_title)
        latest.box.addWidget(self.alert_message)
        latest.box.addStretch()
        latest.box.addWidget(self.alert_time)
        bottom.addWidget(latest, 3)
        self.box.addLayout(bottom)
        self.box.addStretch()
        controller.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        c = self.c
        self.heading.setText(f"{c.profile.name}'s overview")
        self.ring.set_result(c.wellness)
        self.status.setText("● " + c.wellness.status)
        self.status.setStyleSheet(f"color: {STATUS_COLORS[c.wellness.status]};")
        self.location.setText(c.location)
        self.behaviour.setText(c.behaviour)
        self.mode.setText("DEMO DATA · SIMULATED DAILY TOTALS" if c.settings["mock_mode"] else "LIVE DATA · TODAY SO FAR")
        if c.latest_event.classification == "Abnormal Movement":
            assessment = "Unusual movement detected. Behaviour differs from the normal baseline. Continue monitoring your pet."
        elif c.connected_count < 5:
            assessment = "Some sensors are offline. Observations and the score may be incomplete."
        elif c.wellness.status == "NORMAL":
            assessment = "Behaviour is generally normal and follows the daily baseline."
        else:
            assessment = "Daily routines differ from the normal baseline. Consider monitoring your pet."
        self.assessment.setText(assessment)
        values = c.summary.values()
        deltas = compare(values, c.profile.baselines())
        for key, metric in self.metrics.items():
            value = values[key]
            if key == "sleep":
                total = round(value * 60)
                metric.value.setText(f"{total // 60} h {total % 60:02} m")
            elif key == "activity":
                metric.value.setText(f"{value:.1f} h")
            else:
                metric.value.setText(f"{value:g} visits")
            delta = deltas[key]
            state = "Normal" if abs(delta) <= c.settings["anomaly_threshold"] else "Below baseline" if delta < 0 else "Above baseline"
            if not c.settings["mock_mode"]:
                state = "Today so far"
            metric.caption.setText(state if key != "activity" else f"{c.wellness.categories.get(key, 0):.0f}% alignment")
            self.summaries[key].setText(state)
            self.summaries[key].setStyleSheet("color: #249873;" if state == "Normal" else "color: #c18724;")
        if c.alerts:
            alert = c.alerts[0]
            self.alert_title.setText(alert.title)
            self.alert_title.setStyleSheet(f"color: {STATUS_COLORS.get(alert.severity, '#8795a6')};")
            self.alert_message.setText(alert.message)
            self.alert_time.setText(time_text(alert.timestamp) + "  ·  " + alert.telegram_status)
        else:
            self.alert_title.setText("All quiet")
            self.alert_title.setStyleSheet("color: #249873;")
            self.alert_message.setText("No unusual behaviour detected. Alerts will appear here when routines change.")
            self.alert_time.setText("")
