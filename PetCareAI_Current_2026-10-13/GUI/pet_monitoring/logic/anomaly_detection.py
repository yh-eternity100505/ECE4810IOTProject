from pet_monitoring.models import Alert

LABELS = {"sleep": "Unusual sleep", "activity": "Excessive inactivity",
          "food": "Low food activity", "water": "Low water activity", "litter": "Frequent litter usage"}
CATEGORIES = {"sleep": "sleep", "activity": "inactivity", "food": "food", "water": "water", "litter": "litter"}


def compare(values: dict, baseline: dict, threshold: float = .30) -> dict[str, float]:
    return {key: (values.get(key, 0) - normal) / normal if normal > 0 else 0
            for key, normal in baseline.items()}


def detect(values: dict, baseline: dict, pet_name: str, threshold: float = .30) -> list[Alert]:
    alerts = []
    for key, delta in compare(values, baseline).items():
        # Food/water: low activity; sleep/activity/litter: changes in either direction.
        unusual = delta < -threshold if key in ("food", "water") else abs(delta) > threshold
        if unusual:
            title = LABELS[key]
            if key == "activity" and delta > 0:
                title = "Excessive activity"
            if key == "litter" and delta < 0:
                title = "Low litter activity"
            direction = "above" if delta > 0 else "below"
            alerts.append(Alert("WARNING", title,
                f"{pet_name}'s {key} activity is {abs(delta):.0%} {direction} the normal daily baseline. Continue monitoring your pet.",
                category=CATEGORIES[key]))
    return alerts
