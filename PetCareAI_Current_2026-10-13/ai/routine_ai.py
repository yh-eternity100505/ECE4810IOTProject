#!/usr/bin/env python3
"""Routine AI: learn a pet's normal daily routine and score how unusual a new day is.

    python routine_ai.py              # score test_days.csv against normal_days.csv, write routine_report.html
    python routine_ai.py --generate   # (re)create a sample normal_days.csv and test_days.csv to edit

Each row of the CSVs is one day's behaviour fingerprint (edit them in Excel or any text editor).
Model: multivariate anomaly detection. It learns the average of each feature and how the features
move together (shrinkage covariance), then measures each new day with the Mahalanobis distance.
Unlike per-feature thresholds, this also flags *combinations* that are unusual although each value
alone looks acceptable. Standard library only. Behaviour monitoring, not a veterinary diagnosis.
"""
import argparse
import csv
import html
import math
import random
import statistics
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
FEATURES = [  # column, label, unit
    ("night_sleep_h", "Night sleep (22:00-07:00)", "h"),
    ("day_rest_h", "Daytime rest", "h"),
    ("rest_count", "Number of rests", ""),
    ("litter_visits", "Litter box visits", ""),
    ("food_eaten_pct", "Food eaten (bowl %)", "%"),
    ("water_drunk_pct", "Water drunk (bowl %)", "%"),
    ("entrance_events", "Entrance activity", ""),
]
TEST_START = date(2026, 10, 16)  # sample test days start here; the learning days are the days before it
SHRINK = 0.3                  # blend of full covariance and per-feature variance; stable with few days
# Tolerance: the smallest spread the model will assume for a behaviour, in its own unit. The bowl-level sensor
# reads a few % differently from moment to moment, so the water score should not treat a 5-10% change as a big event.
# Raise a value to make the score calmer for that behaviour, lower it to 0 to use only what was learned.
# The hub overrides these with the values set in the app (Activity > Wellness score > Tune sensitivity).
MIN_STD = {"night_sleep_h": 0.0, "day_rest_h": 0.0, "rest_count": 0.0, "litter_visits": 0.0,
           "food_eaten_pct": 0.0, "water_drunk_pct": 6.0, "entrance_events": 0.0}
# Reference distances for a normal day with 7 behaviours (chi distribution, 7 degrees of freedom):
# half of normal days fall below 2.52, 95% below 3.75, 99.9% below 4.93.
TYPICAL_D, RARE_D, EXTREME_D = math.sqrt(6.346), math.sqrt(14.067), math.sqrt(24.322)
FORMULA_WEIGHTS = {"sleep": .25, "toilet": .15}   # the hub's current formula


# ---------------------------------------------------------------- data
def read_days(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    days = []
    for row in rows:
        if not any((row.get(k) or "").strip() for k, _, _ in FEATURES):
            continue
        try:
            values = [float(row[k]) for k, _, _ in FEATURES]
        except (KeyError, ValueError) as exc:
            raise SystemExit(f"{path.name}: every row needs numbers in {', '.join(k for k, _, _ in FEATURES)} ({exc})")
        days.append({"name": (row.get("day") or f"Day {len(days) + 1}").strip(), "x": values})
    return days


def generate(seed=7, days=30):
    """A believable cat routine. Activity shifts daytime rest, number of rests and entrance activity;
    night sleep falls a little when the cat rests more by day; food and water rise and fall together
    but do not depend on how lazy the day was. These are the relationships the model learns."""
    rng = random.Random(seed)
    rows = []
    for i in range(days):
        activity = rng.gauss(0, 1)                        # how active the day was
        day_rest = 5.0 - 0.55 * activity + rng.gauss(0, 0.25)
        night = 8.0 - 0.35 * (day_rest - 5.0) + rng.gauss(0, 0.3)
        rests = round(6 - 0.8 * activity + rng.gauss(0, .6))
        litter = max(1, round(3 + rng.gauss(0, .55)))
        appetite = rng.gauss(0, 1)
        food = 58 + 4 * appetite + rng.gauss(0, 1.5)
        water = 46 + 3.5 * appetite + rng.gauss(0, 1.5)
        entrance = max(0, round(4 + 1.4 * activity + rng.gauss(0, .8)))
        rows.append([(TEST_START - timedelta(days=days - i)).strftime("%a %d %b %Y"), round(night, 1), round(day_rest, 1), rests, litter, round(food), round(water), entrance])
    header = ["day"] + [k for k, _, _ in FEATURES]
    with open(HERE / "normal_days.csv", "w", newline="", encoding="utf-8") as h:
        csv.writer(h).writerows([header] + rows)
    if (HERE / "test_days.csv").exists():   # never overwrite test days you have edited
        print(f"Wrote normal_days.csv ({days} days); kept your test_days.csv.")
        return
    tests = [[(TEST_START + timedelta(days=i)).strftime("%a %d %b %Y"), *values] for i, values in enumerate([
        [8.0, 5.0, 6, 3, 58, 46, 4],      # ordinary day
        [8.3, 4.0, 5, 3, 60, 47, 6],      # very active but healthy
        [8.0, 6.4, 6, 3, 50, 34, 4],      # less water + more daytime rest
        [8.0, 5.0, 6, 3, 59, 38, 4],      # drinks much less, eats normally
        [8.0, 5.1, 6, 7, 57, 47, 4],      # many litter visits
    ])]
    with open(HERE / "test_days.csv", "w", newline="", encoding="utf-8") as h:
        csv.writer(h).writerows([header] + tests)
    print(f"Wrote normal_days.csv ({days} days) and test_days.csv (5 days). Edit them, then run: python routine_ai.py")


# ---------------------------------------------------------------- model
def invert(m):
    n = len(m)
    a = [row[:] + [float(i == j) for j in range(n)] for i, row in enumerate(m)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-12:
            raise SystemExit("A feature never changes in normal_days.csv; vary it a little so the model can learn it.")
        a[c], a[p] = a[p], a[c]
        pivot = a[c][c]
        a[c] = [v / pivot for v in a[c]]
        for r in range(n):
            if r != c:
                f = a[r][c]
                a[r] = [v - f * w for v, w in zip(a[r], a[c])]
    return [row[n:] for row in a]


def regularise(cov):
    """Shrink the links between behaviours, then give each behaviour at least its MIN_STD spread."""
    k = len(cov)
    out = [[cov[i][j] * (1 - SHRINK) if i != j else cov[i][j] for j in range(k)] for i in range(k)]
    for i, (key, _, _) in enumerate(FEATURES):
        out[i][i] = max(out[i][i], MIN_STD.get(key, 0.0) ** 2)
    return out


class RoutineModel:
    def __init__(self, days):
        if len(days) < 8:
            raise SystemExit("Give the model at least 8 normal days to learn from (14+ is better).")
        xs = [d["x"] for d in days]
        k = len(FEATURES)
        self.mean = [statistics.fmean(col) for col in zip(*xs)]
        self.learned_std = [statistics.stdev(col) for col in zip(*xs)]
        self.std = [max(sd, MIN_STD.get(FEATURES[i][0], 0.0)) or 1e-6 for i, sd in enumerate(self.learned_std)]
        cov = [[sum((x[i] - self.mean[i]) * (x[j] - self.mean[j]) for x in xs) / (len(xs) - 1) for j in range(k)] for i in range(k)]
        self.inv = invert(regularise(cov))
        # How unusual are normal days themselves? Leave-one-out keeps a day from judging itself.
        self.normal_d = sorted(self._leave_one_out(days))
        self.typical = statistics.median(self.normal_d)
        self.limit = self.normal_d[min(len(self.normal_d) - 1, int(round(.95 * (len(self.normal_d) - 1))))]

    def _leave_one_out(self, days):
        out = []
        for i in range(len(days)):
            sub = RoutineModel.__new__(RoutineModel)
            others = [d["x"] for j, d in enumerate(days) if j != i]
            k = len(FEATURES)
            sub.mean = [statistics.fmean(c) for c in zip(*others)]
            cov = [[sum((x[a] - sub.mean[a]) * (x[b] - sub.mean[b]) for x in others) / (len(others) - 1) for b in range(k)] for a in range(k)]
            sub.inv = invert(regularise(cov))
            out.append(sub.distance(days[i]["x"]))
        return out

    def distance(self, x):
        d = [x[i] - self.mean[i] for i in range(len(x))]
        return math.sqrt(max(0.0, sum(d[i] * self.inv[i][j] * d[j] for i in range(len(d)) for j in range(len(d)))))

    def score(self, x):
        """100 = a typical day; 50 = more unusual than 95% of normal days; 0 = more unusual than 99.9%."""
        d = self.distance(x)
        if d <= TYPICAL_D:
            value = 100.0
        elif d <= RARE_D:
            value = 100 - 50 * (d - TYPICAL_D) / (RARE_D - TYPICAL_D)
        else:
            value = 50 - 50 * (d - RARE_D) / (EXTREME_D - RARE_D)
        value = round(max(0.0, min(100.0, value)))
        status = "Typical" if value >= 70 else "Somewhat unusual" if value >= 50 else "Unusual"
        z = [(x[i] - self.mean[i]) / self.std[i] for i in range(len(x))]
        reasons = sorted(range(len(z)), key=lambda i: -abs(z[i]))[:3]
        return {"distance": d, "score": value, "status": status, "z": z,
                "reasons": [(FEATURES[i][1], z[i]) for i in reasons if abs(z[i]) >= 1]}


def formula_score(x, model):
    """The hub's current wellness formula: total sleep and litter visits against the usual values."""
    names = [k for k, _, _ in FEATURES]
    usual_sleep = model.mean[names.index("night_sleep_h")] + model.mean[names.index("day_rest_h")]
    usual_litter = model.mean[names.index("litter_visits")]
    parts = {"sleep": (x[names.index("night_sleep_h")] + x[names.index("day_rest_h")], usual_sleep),
             "toilet": (x[names.index("litter_visits")], usual_litter)}
    value = sum(max(0, 100 - abs(o - b) / b * 100) * FORMULA_WEIGHTS[k] for k, (o, b) in parts.items()) / sum(FORMULA_WEIGHTS.values())
    value = round(value)
    return value, "NORMAL" if value >= 80 else "ATTENTION" if value >= 60 else "ABNORMAL"


# ---------------------------------------------------------------- for the Pi hub / phone app
def describe(model, name, x):
    r = model.score(x)
    f, fs = formula_score(x, model)
    return {"name": name, "score": r["score"], "status": r["status"], "distance": round(r["distance"], 2),
            "formula": f, "formula_status": fs,
            "reasons": [{"label": label, "z": round(z, 1)} for label, z in r["reasons"]],
            "values": [{"key": key, "label": label, "unit": unit, "value": x[i], "usual": round(model.mean[i], 1),
                        "z": round(r["z"][i], 1)} for i, (key, label, unit) in enumerate(FEATURES)]}


def evaluate(normal_path=None, test_path=None):
    """Learn from normal_days.csv and score every row of test_days.csv (used by the hub's /api/routine)."""
    normal = read_days(normal_path or HERE / "normal_days.csv")
    model = RoutineModel(normal)
    tests = read_days(test_path or HERE / "test_days.csv")
    scores = [model.score(d["x"])["score"] for d in normal]
    return model, {"learned_days": len(normal), "normal_scores": {"min": min(scores), "max": max(scores), "avg": round(statistics.fmean(scores))},
                   "features": [{"key": k, "label": label, "unit": unit, "usual": round(model.mean[i], 1),
                                 "learned_std": round(model.learned_std[i], 2), "tolerance": MIN_STD.get(k, 0.0)}
                                for i, (k, label, unit) in enumerate(FEATURES)],
                   "days": [describe(model, d["name"], d["x"]) for d in tests]}


# ---------------------------------------------------------------- output
def report(model, normal, tests):
    lines, rows = [], []
    scores = [model.score(d["x"])["score"] for d in normal]
    print(f"\nLearned the routine from {len(normal)} normal days. Those normal days score {min(scores)}-{max(scores)} "
          f"(average {round(statistics.fmean(scores))}).\n")
    print(f"{'Day':38} {'AI routine':>12} {'Formula':>14}   Why")
    print("-" * 100)
    for day in tests:
        r = model.score(day["x"])
        f, fs = formula_score(day["x"], model)
        why = ", ".join(f"{name} {'+' if z > 0 else ''}{z:.1f}σ" for name, z in r["reasons"]) or "close to the usual routine"
        print(f"{day['name'][:38]:38} {r['score']:>4} {r['status'][:7]:>7} {f:>6} {fs:>7}   {why}")
        rows.append((day, r, f, fs, why))
    print("\nσ = how many standard deviations from this pet's usual value.\n")

    def bar(v, colour):
        return f'<div class="bar"><span style="width:{v}%;background:{colour}"></span></div>'
    colour = lambda s: "#1f9a68" if s >= 70 else "#e9692f" if s >= 50 else "#d63a55"
    cards = "".join(f"""
      <div class="card"><h3>{html.escape(day['name'])}</h3>
        <div class="row"><b>AI routine score</b><span class="num" style="color:{colour(r['score'])}">{r['score']}</span><span class="tag">{r['status']}</span></div>{bar(r['score'], colour(r['score']))}
        <div class="row"><b>Current formula</b><span class="num">{f}</span><span class="tag">{fs.title()}</span></div>{bar(f, '#9b8fc4')}
        <p class="why">{html.escape(why)}</p>
        <table>{''.join(f'<tr><td>{html.escape(label)}</td><td>{day["x"][i]:g}{unit}</td><td>usual {model.mean[i]:.1f}{unit}</td><td class="{"hi" if abs(r["z"][i]) >= 2 else ""}">{r["z"][i]:+.1f}σ</td></tr>' for i, (_, label, unit) in enumerate(FEATURES))}</table>
      </div>""" for day, r, f, fs, why in rows)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Routine AI report</title><style>
:root{{--bg:#f6f1ff;--card:#fff;--text:#2a2140;--muted:#74698f;--line:#ebe3fb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#1a1428;--card:#251d38;--text:#f3edff;--muted:#b3a7d1;--line:#3a2f57}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,Segoe UI,sans-serif}}
main{{max-width:1100px;margin:auto;padding:24px 16px}} h1{{margin:0 0 4px}} .muted{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;margin-top:18px}}
.card{{background:var(--card);border-radius:20px;padding:16px;box-shadow:0 8px 24px rgba(124,58,237,.10)}} h3{{margin:0 0 10px}}
.row{{display:flex;align-items:baseline;gap:10px;margin-top:8px}} .row b{{flex:1;font-weight:600}} .num{{font-size:26px;font-weight:800}} .tag{{color:var(--muted);font-size:13px;width:110px;text-align:right}}
.bar{{height:8px;background:var(--line);border-radius:99px;overflow:hidden}} .bar span{{display:block;height:100%}}
.why{{margin:10px 0;font-weight:600}} table{{width:100%;border-collapse:collapse;font-size:13px}} td{{padding:4px 2px;border-top:1px solid var(--line)}} td:nth-child(n+2){{text-align:right}} .hi{{color:#d63a55;font-weight:800}}
</style></head><body><main>
<h1>Routine AI · how unusual is each day?</h1>
<p class="muted">Learned from {len(normal)} normal days. The model compares the whole day (all {len(FEATURES)} behaviours together) with the pet's usual routine, using the Mahalanobis distance with a shrinkage covariance. 100 = typical; below 50 = more unusual than 95% of normal days; 0 = more unusual than 99.9%. The current formula only checks total sleep and litter visits one at a time. Behaviour monitoring, not a veterinary diagnosis.</p>
<div class="grid">{cards}</div></main></body></html>"""
    out = HERE / "routine_report.html"
    out.write_text(page, encoding="utf-8")
    print(f"Report: {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--generate", action="store_true", help="write a sample normal_days.csv (and test_days.csv if missing)")
    parser.add_argument("--days", type=int, default=30, help="how many normal days --generate writes (default 30)")
    parser.add_argument("--normal", type=Path, default=HERE / "normal_days.csv")
    parser.add_argument("--test", type=Path, default=HERE / "test_days.csv")
    args = parser.parse_args()
    if args.generate or not args.normal.exists():
        generate(days=max(8, args.days))
        if args.generate:
            return
    normal, tests = read_days(args.normal), read_days(args.test)
    report(RoutineModel(normal), normal, tests)


if __name__ == "__main__":
    main()
