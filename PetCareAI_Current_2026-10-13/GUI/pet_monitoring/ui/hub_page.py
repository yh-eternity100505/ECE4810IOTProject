from PySide6.QtWidgets import QHBoxLayout, QGridLayout, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView
from .widgets import Page, Card, label, button
from .pet_profile_page import number
from pet_monitoring.hub_controller import MODES, MODE_THRESHOLDS
from .gesture_sensor_card import GestureSensorCard
from .activity_analysis import ActivityAnalysis
from .container_levels import LevelCalibration


class HubPage(Page):
    def __init__(self, hub, movement_callback, movement):
        super().__init__("Smart Pet Sensor Hub", "One Raspberry Pi · four configurable pet zones · Sensor 5 dedicated to gesture KNN")
        self.hub = hub
        self.combos, self.thresholds = {}, {}
        controls = QHBoxLayout()
        controls.addWidget(label("Data source", "muted"))
        self.source = QComboBox()
        self.source.addItems(["Demo", "Live Pi"])
        self.source.setCurrentText(hub.mode)
        self.source.currentTextChanged.connect(hub.set_mode)
        controls.addWidget(self.source)
        controls.addStretch()
        controls.addWidget(button("Connect Pi / Gesture access", movement_callback))
        self.box.addLayout(controls)
        self.status = label("", "muted", True)
        self.box.addWidget(self.status)
        assignments = Card("Sensor assignments")
        assignments.box.addWidget(label("Food/water sensors face down at the contents: farther means less remaining. Sleep/toilet sensors detect zone presence. Reposition and recalibrate hardware when changing its function.", "muted", True))
        grid = QGridLayout()
        self.sync_note = label("", "muted", True)
        assignments.box.addWidget(self.sync_note)
        self.locations = {}
        for column, title in enumerate(["Hardware input", "Function", "Sleep / toilet threshold", "Location (from Pi hub)"]):
            grid.addWidget(label(title, "eyebrow"), 0, column)
        for row, sensor_id in enumerate(hub.mapping, 1):
            grid.addWidget(label(f"Sensor {sensor_id}"), row, 0)
            combo = QComboBox()
            for mode, title in MODES.items():
                combo.addItem(title, mode)
            combo.setCurrentIndex(combo.findData(hub.mapping[sensor_id]))
            grid.addWidget(combo, row, 1)
            self.combos[sensor_id] = combo
            threshold = number(hub.thresholds[sensor_id], 1, 400, " cm")
            grid.addWidget(threshold, row, 2)
            self.thresholds[sensor_id] = threshold
            threshold.setEnabled(combo.currentData() not in {"eat", "water"})
            combo.currentIndexChanged.connect(lambda index, field=threshold, selector=combo: (field.setValue(MODE_THRESHOLDS[selector.currentData()]), field.setEnabled(selector.currentData() not in {"eat", "water"})))
        grid.addWidget(label("Sensor 5"), 5, 0)
        grid.addWidget(label("Gesture KNN · fixed", "sectionTitle"), 5, 1)
        grid.addWidget(label("Trigger <20 cm · 60 samples / 6 seconds", "muted", True), 5, 2)
        for sensor_id in ("1", "2", "3", "4", "5"):
            self.locations[sensor_id] = label("—", "muted", True)
            grid.addWidget(self.locations[sensor_id], int(sensor_id), 3)
        assignments.box.addLayout(grid)
        row = QHBoxLayout()
        self.feedback = label("", "muted", True)
        row.addWidget(self.feedback, 1)
        row.addWidget(button("Save assignments & send to Pi", self.save, True))
        assignments.box.addLayout(row)
        self.box.addWidget(assignments)
        self.calibration = LevelCalibration(hub)
        self.box.addWidget(self.calibration)
        self.metrics = {}
        row = QHBoxLayout()
        for mode, title in MODES.items():
            card = Card("Food remaining" if mode == "eat" else "Water remaining" if mode == "water" else title + " zone")
            value = label("0 visits", "sectionTitle")
            duration = label("0 s near zone", "muted", True)
            card.box.addWidget(value)
            card.box.addWidget(duration)
            self.metrics[mode] = value, duration
            row.addWidget(card)
        self.box.addLayout(row)
        self.activity_analysis = ActivityAnalysis(hub)
        self.box.addWidget(self.activity_analysis)
        live = Card("Live zone status")
        live.box.addWidget(label("All four pet sensors monitor continuously when you change tabs. Live data uses the Pi's proximity rules and confirmed assignment revision; inputs turn offline after 5 seconds without readings.", "muted", True))
        self.live = QTableWidget(4, 4)
        self.live.setHorizontalHeaderLabels(["Sensor", "Assignment", "Distance", "Activity / remaining level"])
        self.live.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.live.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.live.setFixedHeight(205)
        live.box.addWidget(self.live)
        self.box.addWidget(live)
        self.gesture_card = GestureSensorCard(movement)
        self.box.addWidget(self.gesture_card)
        history = Card("Zone activity history")
        history.box.addWidget(label("Today's totals count completed visits. Durations measure time near a zone. Demo and Pi records are stored separately; historical records retain their original zone assignment.", "muted", True))
        self.history = QTableWidget(0, 4)
        self.history.setHorizontalHeaderLabels(["Received", "Sensor", "Zone", "Duration"])
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history.setMinimumHeight(230)
        history.box.addWidget(self.history)
        self.box.addWidget(history)
        hub.changed.connect(self.refresh)
        self.refresh()

    def sync_from_hub(self, state):
        """Show the hub's sensor setup: roles, thresholds, calibration and physical locations."""
        if not state:
            self.sync_note.setText("Not synced with the Pi hub yet. When connected, the hub's sensor setup replaces local changes.")
            return
        facing = {0: "faces back", 45: "faces back-right", 90: "faces right", 135: "faces front-right", 180: "faces front",
                  -180: "faces front", -135: "faces front-left", -90: "faces left", -45: "faces back-left"}
        for sensor_id, place in state["locations"].items():
            direction = facing.get(int(round(place["facing"] / 45) * 45), f"faces {place['facing']}°")
            pins = state["pins"][sensor_id]
            self.locations[sensor_id].setText(f"{place['note'] or 'No note'}\n{place['mount']} · x {place['x']:g}, z {place['z']:g} · {direction} · TRIG {pins[0]} / ECHO {pins[1]}")
        for sensor_id, combo in self.combos.items():
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(self.hub.mapping[sensor_id]))
            combo.blockSignals(False)
            self.thresholds[sensor_id].setValue(self.hub.thresholds[sensor_id])
            self.thresholds[sensor_id].setEnabled(self.hub.mapping[sensor_id] not in {"eat", "water"})
        self.calibration.reload()
        self.sync_note.setText(f"Synced from the Pi hub at {state['time']}. Change sensors, calibration and locations in the phone app; "
                               "the hub's setup replaces local edits while connected.")

    def save(self):
        self.hub.save_configuration({key: combo.currentData() for key, combo in self.combos.items()},
                                    {key: field.value() for key, field in self.thresholds.items()})

    def refresh(self):
        hub = self.hub
        applied = f"Pi applied revision {hub.revision}" if hub.acknowledged == hub.revision else f"Saved revision {hub.revision} · Pi confirmation pending"
        self.status.setText(f"{'DEMO · simulated zone readings' if hub.mode == 'Demo' else 'LIVE PI · real zone messages'}    /    {applied}\n{hub.connection}")
        self.feedback.setText(hub.feedback)
        for row, (sensor_id, state) in enumerate(hub.states.items()):
            distance = ("No valid echo" if state["seen"] else "Offline / no recent data") if state["distance"] is None else f"{state['distance']:.1f} cm"
            presence = "Unknown" if state["distance"] is None else "Near zone" if state["presence"] else "Away"
            if hub.mapping[sensor_id] in {"eat", "water"}:
                percent, litres, text = hub.level(sensor_id)
                presence = text if percent is None else f"{percent:.1f}% remaining" + (f" · {litres:.3f} L" if litres is not None else "")
            if state["presence"] and state["distance"] is not None and hub.mapping[sensor_id] in {"sleep", "toilet"}:
                presence = "Sleeping (estimated)" if hub.mapping[sensor_id] == "sleep" else "Using toilet (estimated)"
            for column, value in enumerate((f"Sensor {sensor_id}", MODES[hub.mapping[sensor_id]], distance, presence)):
                self.live.setItem(row, column, QTableWidgetItem(value))
        totals = hub.totals()
        for mode, (value, duration) in self.metrics.items():
            count, seconds = totals.get(mode, (0, 0))
            value.setText(f"{count} completed visits")
            duration.setText(f"{seconds:.1f} s near zone today")
            if mode in {"eat", "water"}:
                sensors = [key for key, assigned in hub.mapping.items() if assigned == mode]
                readings = [hub.level(key) for key in sensors]
                value.setText(" / ".join("— %" if p is None else f"{p:.1f}%" for p, _, _ in readings) if sensors else "Not assigned")
                duration.setText(" / ".join(f"{litres:.3f} L estimated" if litres is not None else status for _, litres, status in readings))
            if mode in {"sleep", "toilet"}:
                active, valid, session, total = hub.activity(mode)
                value.setText(("Sleeping" if mode == "sleep" else "Using toilet") if active else "Not detected" if valid else "Unknown")
                duration.setText(f"Today: {total / 3600:.3f} h · Current session: {session / 3600:.3f} h")
        events = [event for event in hub.history() if event[2] not in {"eat", "water"}]
        self.history.setRowCount(len(events))
        for row, (timestamp, sensor_id, mode, duration) in enumerate(events):
            for column, value in enumerate((timestamp, f"Sensor {sensor_id}", MODES[mode], f"{duration:.1f} s")):
                self.history.setItem(row, column, QTableWidgetItem(value))
