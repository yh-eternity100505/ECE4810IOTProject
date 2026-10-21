from PySide6.QtWidgets import QFormLayout, QLineEdit, QDoubleSpinBox, QHBoxLayout
from .widgets import Page, Card, label, button, DogMascot
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
        avatar = DogMascot(150, 170)
        self.avatar = avatar
        self.refresh_avatar()
        controller.settings_changed.connect(self.refresh_avatar)
        details.box.addWidget(avatar)
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
        self.sync_note = label("", "muted", True)
        self.box.addWidget(self.sync_note)
        self.feedback = label("", "muted", True)
        self.box.addWidget(self.feedback)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("Save profile", self.save, True))
        self.box.addLayout(actions)
        self.box.addStretch()

    def reload(self, state):
        """Show the pet details synced from the Pi hub."""
        profile = self.c.profile
        for key, field in self.fields.items():
            value = getattr(profile, key)
            field.setText(value) if isinstance(field, QLineEdit) else field.setValue(value)
        self.refresh_avatar()
        if state:
            sex = {"female": "Girl", "male": "Boy"}.get(state["sex"], "not set")
            birthday = state["birthday"] or "not set"
            self.sync_note.setText(f"Synced from the Pi hub at {state['time']} · Sex: {sex} · Birthday: {birthday} · "
                                   f"{state['pets_total']} pet(s) on the hub; this is the monitored one. Edit pets in the phone app.")

    def refresh_avatar(self):
        self.avatar.setToolTip(f"{self.c.profile.name} - profile picture")

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
