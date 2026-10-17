"""One shared presentation of the current five hardware inputs."""
from datetime import datetime
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
from .widgets import Card, label
from pet_monitoring.hub_controller import MODES


def sensor_rows(hub, movement):
    rows = []
    for sensor_id, state in hub.states.items():
        distance = f"{state['distance']:.1f} cm" if state["distance"] is not None else "— cm"
        status = ("Near zone" if state["presence"] else "Away") if state["distance"] is not None else "No valid echo" if state["seen"] else "No recent readings"
        mode = hub.mapping[sensor_id]
        if mode in {"eat", "water"}:
            percent, litres, level_status = hub.level(sensor_id)
            status = level_status if percent is None else f"{percent:.1f}% remaining" + (f" · {litres:.3f} L" if litres is not None else "")
        if state["presence"] and state["distance"] is not None and mode in {"sleep", "toilet"}:
            _, _, session, _ = hub.activity(mode)
            status = f"{'Sleeping' if mode == 'sleep' else 'Using toilet'} (estimated) · {session / 3600:.3f} h"
        rows.append((f"Sensor {sensor_id}", MODES[hub.mapping[sensor_id]], distance, status, state.get("updated", "—")))
    rows.append(("Sensor 5", "Entrance / Gesture", "— cm" if movement.gesture_distance is None else f"{movement.gesture_distance:.1f} cm",
                 movement.gesture_status, movement.gesture_updated))
    return rows


class LiveSensorPanel(Card):
    def __init__(self, hub, movement):
        super().__init__("Live readings · all five sensors")
        self.hub, self.movement = hub, movement
        self.source = label("", "muted", True)
        self.box.addWidget(self.source)
        self.table = QTableWidget(5, 5)
        self.table.setHorizontalHeaderLabels(["Hardware", "Assignment", "Distance", "State", "Last reading"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(240)
        self.box.addWidget(self.table)
        hub.changed.connect(self.refresh)
        movement.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.source.setText(f"Pet inputs: {self.hub.mode} · Sensor 5: Pi telemetry. Readings continue updating on every page; missing echoes are not measured distances.")
        for row, values in enumerate(sensor_rows(self.hub, self.movement)):
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
