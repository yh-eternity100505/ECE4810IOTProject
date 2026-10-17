"""Five physical inputs, three configured logical zones, one location state."""
import json
import math
import time
from pathlib import Path
from threading import RLock


def load_config(path=None):
    cfg = json.loads(Path(path or Path(__file__).with_name('config.json')).read_text())
    groups = cfg['groups']
    if set(groups) != {'A', 'B', 'C'} or sorted(i for ids in groups.values() for i in ids) != list(range(1, 6)) or any(not ids for ids in groups.values()):
        raise ValueError('groups must assign sensors 1–5 exactly once to three nonempty zones A/B/C')
    if set(cfg['zones']) != set(groups):
        raise ValueError('zones must contain A/B/C')
    for key in ('baselines_cm', 'drop_cm'):
        if set(cfg[key]) != set('12345') or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in cfg[key].values()):
            raise ValueError(f'{key} requires five positive finite values')
    for key in ('confirm_frames', 'clear_frames'):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise ValueError(f'{key} must be a positive integer')
    for key in ('hold_seconds', 'stale_seconds'):
        if not math.isfinite(cfg[key]) or cfg[key] <= 0:
            raise ValueError(f'{key} must be positive and finite')
    if sorted(s['n'] for s in cfg['sensors']) != list(range(1, 6)):
        raise ValueError('exactly five physical sensors required')
    return cfg


class Tracker:
    def __init__(self, config=None, clock=time.monotonic):
        self.config = config or load_config()
        self.clock = clock
        self.lock = RLock()
        self.zone = None
        self.last_seen = float('-inf')
        self.streaks = {i: 0 for i in range(1, 6)}
        self.clears = {i: 0 for i in range(1, 6)}
        self.active = {i: False for i in range(1, 6)}
        self.distances = {i: None for i in range(1, 6)}
        self.updated = {i: float('-inf') for i in range(1, 6)}
        self.status = 'unknown'
        self.mode = 'live'

    def _expire(self, now):
        if self.mode == 'simulation':
            return
        for i in self.active:
            if now - self.updated[i] >= self.config['stale_seconds']:
                self.active[i] = False
                self.streaks[i] = self.clears[i] = 0

    def _resolve(self, now):
        candidates = [z for z, ids in self.config['groups'].items() if any(self.active[i] for i in ids)]
        if candidates:
            chosen = self.zone if self.zone in candidates else candidates[0] if len(candidates) == 1 else None
            if chosen:
                self.zone, self.last_seen = chosen, now
            self.status = 'ambiguous' if len(candidates) > 1 else 'detected'
        elif self.zone and now - self.last_seen < self.config['hold_seconds']:
            self.status = 'last_known'
        else:
            self.zone, self.status = None, 'unknown'

    def update(self, readings, forced_active=None):
        with self.lock:
            now = self.clock()
            self._expire(now)
            new_mode = 'simulation' if forced_active is not None else 'live'
            if self.mode != new_mode:
                self.active = {i: False for i in self.active}
                self.streaks = {i: 0 for i in self.active}
                self.clears = {i: 0 for i in self.active}
                self.updated = {i: float('-inf') for i in self.active}
                self.distances = {i: None for i in self.active}
                self.zone = None
                self.last_seen = float('-inf')
            self.mode = new_mode
            for i in self.active:
                d = readings.get(i)
                if forced_active is None and d is None:
                    continue  # Missing frames do not count toward confirmation or refresh freshness.
                self.updated[i] = now
                self.distances[i] = float(d) if d is not None else None
                detected = bool(forced_active.get(i, False)) if forced_active is not None else (2 <= d <= 400 and self.config['baselines_cm'][str(i)] - d >= self.config['drop_cm'][str(i)])
                if detected:
                    self.streaks[i] += 1
                    self.clears[i] = 0
                    if self.streaks[i] >= self.config['confirm_frames']:
                        self.active[i] = True
                else:
                    self.clears[i] += 1
                    self.streaks[i] = 0
                    if self.clears[i] >= self.config['clear_frames']:
                        self.active[i] = False
            self._resolve(now)
            return self._snapshot(now)

    def simulate(self, sensor):
        with self.lock:
            for _ in range(max(self.config['confirm_frames'], self.config['clear_frames'])):
                result = self.update({}, forced_active={i: i == sensor for i in self.active})
            if sensor == 0:
                self.zone, self.status = None, 'unknown'
            return self._snapshot(self.clock())

    def _snapshot(self, now):
        return {'zone': self.zone, 'status': self.status, 'mode': self.mode,
                'sensors': {str(i): {'distance_cm': self.distances[i], 'active': self.active[i],
                    'stale': self.mode == 'live' and now - self.updated[i] >= self.config['stale_seconds']} for i in self.active},
                'groups': self.config['groups']}

    def snapshot(self):
        with self.lock:
            now = self.clock()
            self._expire(now)
            self._resolve(now)
            return self._snapshot(now)
