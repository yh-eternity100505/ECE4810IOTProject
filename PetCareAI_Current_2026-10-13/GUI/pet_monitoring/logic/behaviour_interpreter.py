import math
from pet_monitoring.models import SensorReading


def validate_reading(reading: SensorReading, threshold: float) -> SensorReading:
    """Validate at the service boundary, including NaN and infinite hardware values."""
    valid = (isinstance(reading.distance_cm, (int, float))
             and math.isfinite(reading.distance_cm) and 0 <= reading.distance_cm <= 500)
    if reading.connection_status != "Connected" or not valid:
        reading.detected = False
        reading.distance_cm = None
        if reading.connection_status == "Connected":
            reading.connection_status = "Invalid data"
    else:
        reading.detected = reading.distance_cm < threshold
    return reading


def interpret(reading: SensorReading) -> str:
    if reading.connection_status != "Connected":
        return "Invalid sensor data" if reading.connection_status == "Invalid data" else "Sensor Offline"
    if reading.level_low:
        return "Food level low" if reading.sensor_id == "food" else "Water level low"
    active = {"sleep": "Pet detected · resting", "food": "Pet eating",
              "water": "Pet drinking", "litter": "Pet detected", "entrance": "Movement detected"}
    inactive = {"sleep": "Empty", "food": "No activity", "water": "No activity",
                "litter": "Empty", "entrance": "No movement"}
    return (active if reading.detected else inactive).get(reading.sensor_id, "Unknown")


def current_behaviour(readings: list[SensorReading], classification: str) -> tuple[str, str]:
    detected = next((r for r in readings if r.detected and r.connection_status == "Connected"), None)
    if not detected:
        return "Unknown", "Resting / not detected"
    behaviours = {"sleep": "Sleeping" if classification == "Normal Sleep" else "Resting",
                  "food": "Eating", "water": "Drinking", "litter": "Toilet", "entrance": "Moving"}
    return detected.sensor_name, "Abnormal" if classification == "Abnormal Movement" else behaviours[detected.sensor_id]
