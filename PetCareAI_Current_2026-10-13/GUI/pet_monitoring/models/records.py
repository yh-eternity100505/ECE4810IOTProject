from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

AREAS = {"sleep": "Sleeping Area", "food": "Food Area", "water": "Water Area",
         "litter": "Litter Area", "entrance": "Entrance"}


@dataclass
class SensorReading:
    sensor_id: str
    sensor_name: str
    distance_cm: float | None
    detected: bool
    timestamp: str
    connection_status: str = "Connected"
    # Level sensing requires independent hardware; never infer bowl level from presence.
    level_low: bool | None = None


@dataclass
class BehaviourEvent:
    classification: str
    confidence: float
    timestamp: str
    model_name: str = "KNN (demo)"
    inference_time_ms: float = 28.0


@dataclass
class Alert:
    severity: str
    title: str
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    telegram_sent: bool = False
    read: bool = False
    id: str = field(default_factory=lambda: str(uuid4()))
    category: str = "abnormal"
    telegram_status: str = "Not sent"


@dataclass
class PetProfile:
    name: str = "Milo"
    animal_type: str = "Cat"
    breed: str = "British Shorthair"
    age: float = 3.0
    weight: float = 5.2
    baseline_sleep: float = 8.0
    baseline_food_visits: float = 6.0
    baseline_water_visits: float = 8.0
    baseline_litter_visits: float = 3.0
    baseline_activity: float = 5.0

    def baselines(self) -> dict[str, float]:
        return {"sleep": self.baseline_sleep, "food": self.baseline_food_visits,
                "water": self.baseline_water_visits, "litter": self.baseline_litter_visits,
                "activity": self.baseline_activity}


@dataclass
class DailySummary:
    date: str
    sleep: float = 0
    food: float = 0
    water: float = 0
    litter: float = 0
    activity: float = 0
    source: str = "live"

    def values(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in ("sleep", "food", "water", "litter", "activity")}
