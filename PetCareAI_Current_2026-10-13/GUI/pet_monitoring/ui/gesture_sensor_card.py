from .widgets import Card, label
from .pet_animation import PetAnimation, compact_animation


class GestureSensorCard(Card):
    def __init__(self, movement):
        super().__init__("Sensor 5 · Entrance / Gesture")
        self.m = movement
        self.setProperty("petTone", "gesture")
        self.animation = PetAnimation("gesture")
        compact_animation(self, self.animation)
        self.distance = label("— cm", "metric")
        self.status = label("", "sectionTitle", True)
        self.updated = label("", "muted", True)
        self.box.addWidget(self.distance)
        self.box.addWidget(self.status)
        self.box.addWidget(self.updated)
        self.box.addWidget(label("Fixed gesture sensor · physical BOARD TRIG 7 / ECHO 11 · trigger below 20 cm", "muted", True))
        movement.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.distance.setText("— cm" if self.m.gesture_distance is None else f"{self.m.gesture_distance:.1f} cm")
        self.status.setText(self.m.gesture_status)
        self.updated.setText("Last reading received: " + self.m.gesture_updated)
