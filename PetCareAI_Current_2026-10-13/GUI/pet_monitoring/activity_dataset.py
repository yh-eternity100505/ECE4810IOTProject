"""Daily activity dataset tools. No training; missing observations remain missing."""
import argparse
import csv
import json
import math
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pet_monitoring.hub_controller import DEFAULT_MAPPING, MODE_THRESHOLDS

AREAS = ('eat', 'sleep', 'water', 'toilet')
METRICS = ('occupancy_seconds', 'visit_count', 'longest_stay_seconds', 'first_activity_hour', 'last_activity_hour', 'night_fraction')
FEATURES = [f'{area}_{metric}' for area in AREAS for metric in METRICS]
METADATA = ['date', 'source', 'scenario', 'seed', 'session_id', 'clock_scale', 'configuration', 'configuration_origin'] + [f'{a}_coverage' for a in AREAS]


def presence_for_area(area, distance, threshold):
    # Exactly the GUI contract: food/water are level measurements, not occupancy.
    return area not in {'eat', 'water'} and distance is not None and distance < threshold


def configuration(database=None):
    saved = None
    if database:
        path = Path(database).resolve()
        if not path.is_file():
            raise ValueError(f'GUI database does not exist: {path}')
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute("SELECT value FROM config WHERE key='hub_configuration'").fetchone()
            saved = json.loads(row[0]) if row else None
    mapping = saved['mapping'] if saved else dict(DEFAULT_MAPPING)
    thresholds = saved['thresholds'] if saved else {k: MODE_THRESHOLDS[v] for k, v in mapping.items()}
    result = dict(mapping=mapping, thresholds=thresholds, revision=saved.get('revision', 1) if saved else 1)
    return result, 'gui_database' if saved else 'gui_defaults'


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=METADATA + FEATURES)
        writer.writeheader()
        writer.writerows(rows)


def aggregate(segments, metadata, minimum_coverage=.95):
    """Segments use logical seconds; None is unknown. Split exactly at midnight.

    Count starts only after observed absence, so a left-censored stay is not a visit.
    Unknown intervals break stays. Insufficient daily coverage blanks all activity features.
    """
    days = {}
    for segment in sorted(segments, key=lambda s: s['start']):
        begin, end = segment['start'], segment['end']
        while begin < end:
            day = int(begin // 86400)
            stop = min(end, (day + 1) * 86400)
            days.setdefault(day, []).append((begin - day*86400, stop - day*86400, segment['states']))
            begin = stop
    rows = []
    for day, parts in sorted(days.items()):
        row = dict(metadata)
        row['date'] = (datetime.fromisoformat(metadata['date']) + timedelta(days=day)).date().isoformat()
        for area in AREAS:
            covered = occupied = night = longest = run = visits = 0
            first = last = None
            previous = None
            last_end = None
            for start, end, states in parts:
                state = states.get(area)
                if last_end is not None and abs(start-last_end) > 1e-6:
                    previous, run = None, 0
                duration = end-start
                if state is not None:
                    covered += duration
                if state is True:
                    visits += int(previous is False)
                    run = run + duration if previous is True else duration
                    longest = max(longest, run)
                    occupied += duration
                    night += max(0, min(end, 6*3600)-start) + max(0, end-max(start,22*3600))
                    first = start if first is None else first
                    last = end
                else:
                    run = 0
                previous, last_end = state, end
            coverage = covered/86400
            row[f'{area}_coverage'] = round(coverage, 6)
            values = [occupied, visits, longest, first/3600 if first is not None else None,
                      last/3600 if last is not None else None, night/occupied if occupied else None]
            for metric, value in zip(METRICS, values):
                row[f'{area}_{metric}'] = round(value, 6) if coverage >= minimum_coverage and value is not None else None
        rows.append(row)
    return rows


def simulate(args):
    config, origin = configuration(args.database)
    assumptions = json.loads(Path(args.assumptions).read_text(encoding='utf-8-sig'))
    rng = random.Random(args.seed)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    for scenario, count in [('baseline', args.baseline_days), ('changed_routine', args.test_days)]:
        segments = []
        routine = assumptions[scenario]
        # Minute resolution, applying the same GUI threshold semantics to synthetic distances.
        for day in range(count):
            spans = {a: [] for a in AREAS}
            for area, spec in routine.items():
                for hour, hours in spec['stays']:
                    start = max(0, min(23.9, hour + rng.uniform(-spec['jitter_hours'], spec['jitter_hours'])))
                    duration = hours * rng.uniform(.9, 1.1)
                    spans[area].append((start*3600, min(86400, (start+duration)*3600)))
            for minute in range(1440):
                t = minute*60
                states = {}
                for area in AREAS:
                    sensor_ids = [s for s, a in config['mapping'].items() if a == area]
                    if not sensor_ids or area in {'eat','water'}:
                        states[area] = None
                        continue
                    active = any(start <= t < end for start,end in spans[area])
                    states[area] = any(presence_for_area(area, config['thresholds'][s] * (.5 if active else 1.5), config['thresholds'][s]) for s in sensor_ids)
                segments.append(dict(start=day*86400+t, end=day*86400+t+60, states=states))
        metadata = dict(date=(datetime.fromisoformat(args.start_date)+timedelta(days=args.baseline_days if scenario == 'changed_routine' else 0)).date().isoformat(), source='simulated', scenario=scenario, seed=args.seed,
                        session_id=f'sim-{args.seed}-{scenario}', clock_scale=1, configuration=json.dumps(config,sort_keys=True), configuration_origin=origin)
        rows = aggregate(segments, metadata)
        write_csv(out / ('baseline_train.csv' if scenario == 'baseline' else 'changed_routine_test.csv'), rows)
    (out/'feature_schema.json').write_text(json.dumps(dict(model_inputs=FEATURES, metadata_columns=METADATA,
        missing_value='empty CSV cell (NaN in pandas); never fill missing occupancy with zero',
        units='durations: seconds; timing: local logical hours 0-24; night: 22:00-06:00',
        minimum_coverage=.95, resolution_seconds=60, assumptions=assumptions,
        limitations='Food/water level sensors cannot measure visits. Synthetic assumptions are not clinical labels.'),indent=2))
    print(f'Created baseline_train.csv, changed_routine_test.csv and feature_schema.json in {out}')


class ActivityRecorder:
    """Observe the existing validated GUI hub, without changing receiver/security clocks."""
    def __init__(self, hub, path, scale=1, start_date=None):
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError('Clock scale must be a positive finite number')
        from PySide6.QtCore import QTimer
        self.hub, self.scale = hub, scale
        self.origin = time.monotonic()
        self.last = self.origin
        wall = datetime.now()
        self.offset = (wall - wall.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() if scale == 1 else 0
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('x', encoding='utf-8')
        self.config = self.get_config()
        self.metadata = dict(date=start_date or datetime.now().date().isoformat(), source='pi_live' if scale == 1 else 'pi_accelerated',
            scenario='unlabelled', seed='', session_id=datetime.now(timezone.utc).isoformat(), clock_scale=scale,
            configuration=json.dumps(self.config, sort_keys=True), configuration_origin='gui_runtime')
        self.file.write(json.dumps(dict(metadata=self.metadata))+'\n')
        self._pending = None
        self._flushed = self.origin
        self.snapshot = self.states(self.origin)
        # This slot runs BEFORE each accepted message changes state (connected with main hook below).
        self.timer = QTimer(hub)
        self.timer.timeout.connect(self.advance)
        self.timer.start(100)
        self.closed = False

    def get_config(self):
        return dict(mapping=self.hub.mapping.copy(), thresholds=self.hub.thresholds.copy(), revision=self.hub.revision)

    def states(self, now):
        result = {}
        valid_session = (self.hub.mode == 'Live Pi' and self.hub.acknowledged == self.hub.revision and self.get_config() == self.config)
        for area in AREAS:
            relevant = [s for k,s in self.hub.states.items() if self.hub.mapping[k] == area]
            if not valid_session or not relevant or area in {'eat','water'}:
                result[area] = None
            else:
                known = [s for s in relevant if s['distance'] is not None and s['seen'] > 0 and now-s['seen'] <= 5]
                # A positive sensor proves presence. Absence needs every assigned sensor online.
                result[area] = True if any(s['presence'] for s in known) else False if len(known) == len(relevant) else None
        return result

    def advance(self):
        if self.closed:
            return
        now = time.monotonic()
        # Split at real freshness expiry, not the accelerated logical expiry.
        boundaries = sorted({self.last, now} | {s['seen']+5 for s in self.hub.states.values() if self.last < s['seen']+5 < now})
        for start,end in zip(boundaries,boundaries[1:]):
            # Cached prior state avoids attributing a new reading to the interval before its arrival.
            states = dict(self.snapshot)
            current = self.states((start+end)/2)
            for area in AREAS:
                if current[area] is None:
                    states[area] = None
            segment = dict(start=self.offset+(start-self.origin)*self.scale, end=self.offset+(end-self.origin)*self.scale,
                received_at=datetime.now(timezone.utc).isoformat(), states=states)
            if self._pending and self._pending['states'] == states and abs(self._pending['end']-segment['start']) < 1e-6:
                self._pending.update(end=segment['end'], received_at=segment['received_at'])
            else:
                self.flush_pending()
                self._pending = segment
        self.last = now
        self.snapshot = self.states(now)
        if now-self._flushed >= 1:
            self.flush_pending()
            self._flushed = now
        self.file.flush()

    def flush_pending(self):
        if self._pending:
            self.file.write(json.dumps(self._pending)+"\n")
            self._pending = None

    def updated(self, *_):
        self.snapshot = self.states(time.monotonic())

    def close(self):
        if not self.closed:
            self.advance()
            self.timer.stop()
            self.flush_pending()
            self.file.close()
            self.closed = True
            export_recording(self.path, self.path.with_suffix('.csv'))


def export_recording(path, output, minimum_coverage=.95):
    with Path(path).open(encoding='utf-8') as f:
        metadata = json.loads(next(f))['metadata']
        segments = [json.loads(line) for line in f if line.strip()]
    write_csv(output, aggregate(segments, metadata, minimum_coverage))
    print(f'Exported {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sim = sub.add_parser('simulate')
    sim.add_argument('--database', type=Path)
    sim.add_argument('--assumptions', default='dataset/routines.json')
    sim.add_argument('--output', default='dataset/generated')
    sim.add_argument('--seed', type=int, default=4810)
    sim.add_argument('--baseline-days', type=int, default=90)
    sim.add_argument('--test-days', type=int, default=30)
    sim.add_argument('--start-date', default='2026-01-01')
    exp = sub.add_parser('export')
    exp.add_argument('recording')
    exp.add_argument('--output', required=True)
    exp.add_argument('--minimum-coverage', type=float, default=.95)
    args = parser.parse_args()
    if args.command == 'simulate':
        simulate(args)
    else:
        export_recording(args.recording, args.output, args.minimum_coverage)

if __name__ == '__main__':
    main()
