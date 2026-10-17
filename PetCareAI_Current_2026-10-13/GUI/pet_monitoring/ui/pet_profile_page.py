from PySide6.QtWidgets import QFormLayout, QLineEdit, QDoubleSpinBox, QHBoxLayout
from .widgets import Page, Card, label, button
from pet_monitoring.models import PetProfile


def number(value, minimum, maximum, suffix="", decimals=1):
    widget = QDoubleSpinBox()
    widget.setDecimals(decimals)
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    widget.setSuffix(suffix)
    return widget


class PetProfilePage(Page):
    def __init__(self, controller):
        super().__init__("Pet profile", "Personal details and the daily routines that make your pet unique.")
        self.c = controller
        self.fields = {}
        row = QHBoxLayout()
        details = Card("About your pet")
        avatar = label("M", "metric")
        self.avatar = avatar
        avatar.setFixedSize(70, 70)
        self.refresh_avatar()
        controller.settings_changed.connect(self.refresh_avatar)
        details.box.addWidget(avatar)
        details.box.addWidget(label("Pet photo placeholder", "muted"))
        form = QFormLayout()
        form.setSpacing(14)
        profile = controller.profile
        for key, title in [("name", "Pet name"), ("animal_type", "Animal type"), ("breed", "Breed")]:
            field = QLineEdit(getattr(profile, key))
            field.setMaxLength(80)
            form.addRow(title, field)
            self.fields[key] = field
        for key, title, maximum, suffix in [("age", "Age", 100, " years"), ("weight", "Weight", 1000, " kg")]:
            field = number(getattr(profile, key), .1, maximum, suffix)
            form.addRow(title, field)
            self.fields[key] = field
        details.box.addLayout(form)
        details.box.addStretch()
        row.addWidget(details)
        baseline = Card("Normal behaviour baseline")
        baseline.box.addWidget(label("Daily averages used for comparison and the behavioural wellness score.", "muted", True))
        form2 = QFormLayout()
        form2.setSpacing(14)
        for key, title, maximum, suffix in [
            ("baseline_sleep", "Average sleep", 24, " h / day"),
            ("baseline_food_visits", "Food visits", 100, " / day"),
            ("baseline_water_visits", "Water visits", 100, " / day"),
            ("baseline_litter_visits", "Litter visits", 100, " / day"),
            ("baseline_activity", "Active time", 24, " h / day")]:
            field = number(getattr(profile, key), .1, maximum, suffix)
            form2.addRow(title, field)
            self.fields[key] = field
        baseline.box.addLayout(form2)
        baseline.box.addStretch()
        baseline.box.addWidget(label("Use a representative multi-day observation period when setting these values.", "muted", True))
        row.addWidget(baseline)
        self.box.addLayout(row)
        self.feedback = label("", "muted", True)
        self.box.addWidget(self.feedback)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("Save profile", self.save, True))
        self.box.addLayout(actions)
        self.box.addStretch()

    def refresh_avatar(self):
        # Settings already contains the selected theme before the shell applies it.
        from .theme import DARK, LIGHT
        colors = DARK if self.c.settings["theme"] == "Dark" else LIGHT
        self.avatar.setStyleSheet(f"background: {colors['accent_soft']}; color: {colors['accent']}; border-radius: 35px; qproperty-alignment: AlignCenter;")

    def save(self):
        values = {key: field.text().strip() if isinstance(field, QLineEdit) else field.value()
                  for key, field in self.fields.items()}
        if not values["name"] or not values["animal_type"]:
            self.feedback.setText("Pet name and animal type are required.")
            return
        if values["baseline_sleep"] + values["baseline_activity"] > 24:
            self.feedback.setText("Average sleep and active time must total no more than 24 hours.")
            return
        self.c.save_profile(PetProfile(**values))
        self.feedback.setText("Profile and baseline saved locally." if self.c.db.available else self.c.db.error)
