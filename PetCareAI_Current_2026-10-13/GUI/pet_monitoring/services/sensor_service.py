"""Adapters expose read() so GPIO/MQTT integration does not affect the UI."""
import math
import time
from datetime import datetime
from typing import Protocol
from pet_monitoring.models import SensorReading
from pet_monitoring.models.records import AREAS


class SensorService(Protocol):
    def read(self) -> list[SensorReading]: ...


class MockSensorService:
    # Long sleep occupancy, occasional station visits and brief entrance transitions.
    SCHEDULE = [("sleep", 45), ("entrance", 3), ("food", 12), ("entrance", 2),
                ("water", 9), ("sleep", 35), ("litter", 10), ("entrance", 3)]

    def __init__(self):
        self.start = time.monotonic()
        self.scenario = "normal"

    def reset(self):
        self.start = time.monotonic()
        self.scenario = "normal"

    def read(self) -> list[SensorReading]:
        elapsed = time.monotonic() - self.start
        position = elapsed % sum(length for _, length in self.SCHEDULE)
        area = "sleep"
        for name, length in self.SCHEDULE:
            if position < length:
                area = name
                break
            position -= length
        if self.scenario in ("abnormal", "restless"):
            area = "sleep"
        if self.scenario == "secure_food":
            area = "food"
        now = datetime.now().isoformat(timespec="seconds")
        readings = []
        for i, (key, name) in enumerate(AREAS.items()):
            detected = key == area
            distance = (13.4 if detected else 95 + i * 13) + 1.2 * math.sin(elapsed / 3 + i)
            if self.scenario == "secure_food" and key == "food":
                distance = 14.3
            if self.scenario in ("abnormal", "restless") and key == "sleep":
                distance = 14 + 9 * math.sin(elapsed * 3)
            connected = not (self.scenario == "sensor_failure" and key == "sleep")
            readings.append(SensorReading(key, name, distance if connected else None,
                                          detected and connected, now, "Connected" if connected else "Offline"))
        return readings


class RealSensorService:
    """Safe offline adapter. Override read() with GPIO, serial, or MQTT readings.

    Blocking acquisition should run in a worker; read() returns cached latest samples.
    """
    def read(self) -> list[SensorReading]:
        return [SensorReading(key, name, None, False, datetime.now().isoformat(timespec="seconds"), "Offline")
                for key, name in AREAS.items()]
