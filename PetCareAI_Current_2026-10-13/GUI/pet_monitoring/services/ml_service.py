import time
from datetime import datetime
from typing import Protocol
from pet_monitoring.models import BehaviourEvent


class MLService(Protocol):
    def predict(self, features: dict) -> BehaviourEvent: ...


class MockMLService:
    """Demo outputs, not an actually trained KNN model or measured accuracy."""
    performance = [
        {"model": "KNN", "accuracy": 86.7, "precision": 87.1, "recall": 86.3, "f1": 86.7, "latency": 28},
        {"model": "SVM", "accuracy": 83.3, "precision": 84.2, "recall": 82.6, "f1": 83.4, "latency": 34},
        {"model": "Decision Tree", "accuracy": 80.0, "precision": 80.8, "recall": 79.4, "f1": 80.1, "latency": 12}]

    def predict(self, features: dict) -> BehaviourEvent:
        abnormal = features.get("scenario") in ("abnormal", "restless")
        readings = features.get("readings", [])
        if not any(r.connection_status == "Connected" for r in readings):
            return BehaviourEvent("Awaiting sensor data", 0, datetime.now().isoformat(timespec="seconds"), inference_time_ms=0)
        sleeping = any(r.sensor_id == "sleep" and r.detected for r in readings)
        label = "Abnormal Movement" if abnormal else "Normal Sleep" if sleeping else "Pass-by"
        confidence = .81 if abnormal else .92 if sleeping else .88
        return BehaviourEvent(label, confidence, datetime.now().isoformat(timespec="seconds"))


class UnavailableMLService:
    performance = []

    def predict(self, features: dict) -> BehaviourEvent:
        return BehaviourEvent("ML Model Unavailable", 0, datetime.now().isoformat(timespec="seconds"), "Unavailable", 0)
