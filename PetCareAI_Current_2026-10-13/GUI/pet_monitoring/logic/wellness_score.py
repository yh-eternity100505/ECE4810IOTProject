from dataclasses import dataclass

DEFAULT_WEIGHTS = {"activity": .20, "sleep": .25, "food": .20, "water": .20, "litter": .15}
DISCLAIMER = "This score represents behavioural patterns detected by the monitoring system and is not a veterinary diagnosis."


@dataclass
class WellnessResult:
    score: int
    status: str
    categories: dict[str, float]


def calculate(values: dict, baseline: dict, weights: dict | None = None,
              anomaly: bool = False, offline: bool = False) -> WellnessResult:
    weights = weights or DEFAULT_WEIGHTS
    scores = {key: max(0.0, 100.0 - abs(values.get(key, 0) - normal) / normal * 100)
              for key, normal in baseline.items() if normal > 0}
    total = sum(max(0, weights.get(k, 0)) for k in scores)
    score = round(sum(v * max(0, weights.get(k, 0)) for k, v in scores.items()) / total) if total else 0
    if anomaly:
        score -= 40
    if offline:
        score -= 15
    score = max(0, min(100, score))
    return WellnessResult(score, "NORMAL" if score >= 80 else "ATTENTION" if score >= 60 else "ABNORMAL", scores)
