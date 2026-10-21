import copy
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QFormLayout, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView
from .widgets import Card, label, button
from .pet_profile_page import number
from pet_monitoring.hub_controller import MODES
from .pet_animation import PetAnimation, compact_animation


class ContainerLevels(Card):
    def __init__(self, hub):
        super().__init__("Food & water remaining")
        self.hub = hub
        self.cards = {}
        self.animations = {}
        row = QHBoxLayout()
        for sensor_id in hub.states:
            card = Card()
            animation = PetAnimation("eat")
            self.animations[sensor_id] = animation
            title = label("", "sectionTitle")
            value = label("— %", "metric")
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setTextVisible(False)
            volume = label("", "muted", True)
            status = label("", None, True)
            compact_animation(card, animation)
            for widget in (title, value, progress, volume, status):
                card.box.addWidget(widget)
            row.addWidget(card)
            self.cards[sensor_id] = card, title, value, progress, volume, status
        self.box.addLayout(row)
        self.note = label("", "muted", True)
        self.box.addWidget(self.note)
        self.box.addWidget(label("Farther surface = less remaining. Percentage estimates filled height. Litres require usable capacity and the uniform-container assumption; irregular bottles and uneven food surfaces reduce accuracy.", "muted", True))
        self.history = QTableWidget(0, 5)
        self.history.setHorizontalHeaderLabels(["Time", "Sensor", "Container", "Remaining", "Estimated litres"])
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history.setMinimumHeight(160)
        self.box.addWidget(self.history)
        hub.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        count = 0
        for sensor_id, (card, title, value, progress, volume, status) in self.cards.items():
            mode = self.hub.mapping[sensor_id]
            visible = mode in {"eat", "water"}
            card.setVisible(visible)
            if not visible:
                continue
            count += 1
            self.animations[sensor_id].mode = mode
            if card.property("petTone") != mode:
                card.setProperty("petTone", mode)
                card.style().unpolish(card)
                card.style().polish(card)
            title.setText(f"Sensor {sensor_id} · {MODES[mode]}")
            percent, litres, text = self.hub.level(sensor_id)
            value.setText("— %" if percent is None else f"{percent:.1f}% remaining")
            progress.setEnabled(percent is not None)
            progress.setValue(round(percent) if percent is not None else 0)
            volume.setText(f"Estimated volume: {litres:.3f} L" if litres is not None else "Litres unavailable · capacity/shape calibration needed")
            status.setText(text)
        self.note.setText("No sensor is assigned to food or water level." if not count else f"{self.hub.mode} · container level sensors are not pet-visit counters")
        records = self.hub.level_history()
        self.history.setRowCount(len(records))
        for row, (timestamp, sensor_id, mode, percent, litres) in enumerate(records):
            for column, value in enumerate((timestamp, f"Sensor {sensor_id}", MODES[mode], f"{percent:.1f}%", "—" if litres is None else f"{litres:.3f} L")):
                self.history.setItem(row, column, QTableWidgetItem(value))


class LevelCalibration(Card):
    def __init__(self, hub):
        super().__init__("Container calibration · food and water")
        self.hub = hub
        self.box.addWidget(label("Measure sensor-to-surface distance at full and empty. Leave litres at 0 if unknown. Calibration belongs to the physical sensor/container; recalibrate if you move it.", "muted", True))
        self.fields = {}
        self._displayed_calibration = copy.deepcopy(hub.level_calibration)
        for sensor_id in hub.states:
            card = Card(f"Sensor {sensor_id}")
            saved = hub.level_calibration.get(sensor_id, {})
            form = QFormLayout()
            full = number(saved.get("full_cm", 0), 0, 400, " cm")
            empty = number(saved.get("empty_cm", 0), 0, 400, " cm")
            capacity = number(saved.get("capacity_l", 0), 0, 1000, " L", 3)
            linear = QCheckBox("Use a linear volume estimate (uniform cross-section)")
            linear.setChecked(saved.get("linear_volume", False))
            form.addRow("Full distance", full)
            form.addRow("Empty distance", empty)
            form.addRow("Usable capacity", capacity)
            form.addRow(linear)
            card.box.addLayout(form)
            self.box.addWidget(card)
            self.fields[sensor_id] = card, full, empty, capacity, linear
        self.feedback = label("", "muted", True)
        self.box.addWidget(self.feedback)
        self.box.addWidget(button("Save container calibration", self.save, True))
        hub.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        for sensor_id, (card, *_fields) in self.fields.items():
            card.setVisible(self.hub.mapping[sensor_id] in {"eat", "water"})

    def reload(self):
        """Show calibration values after the Pi hub updated them."""
        for sensor_id, (card, full, empty, capacity, linear) in self.fields.items():
            saved = self.hub.level_calibration.get(sensor_id, {})
            if saved == self._displayed_calibration.get(sensor_id, {}):
                continue  # Keep unsaved edits intact during repeated phone refreshes.
            self._displayed_calibration[sensor_id] = copy.deepcopy(saved)
            full.setValue(saved.get("full_cm", 0))
            empty.setValue(saved.get("empty_cm", 0))
            capacity.setValue(saved.get("capacity_l", 0))
            linear.setChecked(saved.get("linear_volume", False))
        self.refresh()

    def save(self):
        values = dict(self.hub.level_calibration)
        for sensor_id, (card, full, empty, capacity, linear) in self.fields.items():
            if self.hub.mapping[sensor_id] not in {"eat", "water"}:
                continue
            if full.value() == empty.value() == capacity.value() == 0:
                values.pop(sensor_id, None)
                continue
            values[sensor_id] = {"full_cm": full.value(), "empty_cm": empty.value(), "capacity_l": capacity.value(), "linear_volume": linear.isChecked()}
        success, text = self.hub.save_level_calibration(values)
        if success:
            self._displayed_calibration = copy.deepcopy(values)
        self.feedback.setText(text)
