from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QCheckBox
from .widgets import Page, Card, label
from pet_monitoring.models.records import AREAS
from pet_monitoring.logic.behaviour_interpreter import interpret
from .live_sensor_panel import sensor_rows
from .pet_animation import PetAnimation, compact_animation
import copy


class LiveMonitoringPage(Page):
    def __init__(self, controller):
        super().__init__("Live monitoring", "Five discreet sensing zones. Raw readings translated into useful observations.")
        self.c = controller
        self.hub = self.movement = None
        bar = QHBoxLayout()
        self.status = label("● LIVE", "sectionTitle")
        bar.addWidget(self.status)
        bar.addStretch()
        self.mock = QCheckBox("Mock Live Data")
        self.mock.setChecked(controller.settings["mock_mode"])
        self.mock.toggled.connect(self.toggle_mock)
        bar.addWidget(self.mock)
        self.box.addLayout(bar)
        grid = QGridLayout()
        grid.setSpacing(18)
        self.cards = {}
        self.card_titles = {}
        self.animations = {}
        self.card_frames = {}
        for i, (key, name) in enumerate(AREAS.items()):
            card = Card(name)
            self.card_titles[key] = card.box.itemAt(0).widget()
            animation = PetAnimation({"sleep":"sleep", "food":"eat", "water":"water", "litter":"toilet", "entrance":"gesture"}[key])
            compact_animation(card, animation)
            self.animations[key] = animation
            self.card_frames[key] = card
            card.setProperty("petTone", animation.mode)
            connection = label("Connected", "muted")
            distance = label("— cm", "metric")
            status = label("Waiting for readings", "sectionTitle")
            update = label("Last update: —", "muted")
            for widget in (connection, distance, status, update):
                card.box.addWidget(widget)
            self.cards[key] = (connection, distance, status, update)
            grid.addWidget(card, i // 2, i % 2)
        self.box.addLayout(grid)
        self.box.addStretch()
        controller.changed.connect(self.refresh)
        self.refresh()

    def toggle_mock(self, enabled):
        settings = copy.deepcopy(self.c.settings)
        settings["mock_mode"] = enabled
        self.c.save_settings(settings)

    def refresh(self):
        if self.hub is not None and self.hub.mode == "Live Pi":
            self.mock.hide()
            rows = sensor_rows(self.hub, self.movement)
            count = sum(state['distance'] is not None for state in self.hub.states.values()) + int(self.movement.gesture_distance is not None)
            self.status.setText(f"LIVE PI · {count} / 5 valid distances · acquisition continues on every tab")
            self.status.setStyleSheet("")
            for index, (key, row) in enumerate(zip(self.cards, rows), 1):
                hardware, mode, value, state, timestamp = row
                self.card_titles[key].setText(f"{hardware} · {mode}")
                tone = self.hub.mapping[str(index)] if index <= 4 else "gesture"
                animation = self.animations[key]
                animation.mode = tone
                animation.set_active(index <= 4 and self.hub.states[str(index)]["presence"])
                card = self.card_frames[key]
                if card.property("petTone") != tone:
                    card.setProperty("petTone", tone)
                    card.style().unpolish(card)
                    card.style().polish(card)
                connection, distance, status, update = self.cards[key]
                connection.setText("● Live Pi input")
                distance.setText(value)
                status.setText(state)
                status.setStyleSheet("color: #c18724;" if value == "— cm" else "color: #249873;")
                update.setText("Last reading received: " + timestamp)
            return
        self.mock.show()
        for key, title in self.card_titles.items():
            title.setText(AREAS[key])
        self.mock.blockSignals(True)
        self.mock.setChecked(self.c.settings["mock_mode"])
        self.mock.blockSignals(False)
        count = self.c.connected_count
        self.status.setText(f"● {'LIVE' if count else 'OFFLINE'}   ·   {count} / 5 sensors connected")
        self.status.setStyleSheet("color: #249873;" if count == 5 else "color: #c18724;" if count else "color: #8795a6;")
        for reading in self.c.readings:
            connection, distance, status, update = self.cards[reading.sensor_id]
            connection.setText("● Sensor: " + reading.connection_status)
            distance.setText(f"{reading.distance_cm:.1f} cm" if reading.distance_cm is not None else "— cm")
            status.setText(interpret(reading))
            color = "#d15c67" if reading.connection_status != "Connected" else "#c18724" if reading.level_low else "#249873" if reading.detected else "#8795a6"
            status.setStyleSheet(f"color: {color};")
            update.setText("Last update: " + reading.timestamp.replace("T", " "))

    def bind_live_inputs(self, hub, movement):
        self.hub, self.movement = hub, movement
        hub.changed.connect(self.refresh)
        movement.changed.connect(self.refresh)
        self.refresh()
