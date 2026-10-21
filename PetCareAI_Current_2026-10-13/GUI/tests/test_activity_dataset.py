"""Dataset boundary, missingness, reproducibility and real-time freshness checks."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtCore import QObject
from pet_monitoring.activity_dataset import (ActivityRecorder, aggregate, configuration, simulate, presence_for_area, FEATURES, METADATA)

class DatasetTests(unittest.TestCase):
    def metadata(self):
        return dict(date='2026-01-01', source='pi_live', scenario='unlabelled', seed='', session_id='test', clock_scale=1, configuration='{}',configuration_origin='test')

    def test_missing_is_not_zero_and_observed_absence_is_zero(self):
        rows=aggregate([dict(start=0,end=86400,states=dict(sleep=False,toilet=None))],self.metadata())
        self.assertEqual(rows[0]['sleep_occupancy_seconds'],0)
        self.assertEqual(rows[0]['sleep_visit_count'],0)
        self.assertIsNone(rows[0]['sleep_first_activity_hour'])
        self.assertIsNone(rows[0]['toilet_occupancy_seconds'])
        self.assertEqual(rows[0]['toilet_coverage'],0)

    def test_midnight_and_visits(self):
        rows=aggregate([dict(start=0,end=86340,states=dict(sleep=False)),dict(start=86340,end=86460,states=dict(sleep=True)),dict(start=86460,end=172800,states=dict(sleep=False))],self.metadata())
        self.assertEqual([r['sleep_occupancy_seconds'] for r in rows],[60,60])
        self.assertEqual([r['sleep_visit_count'] for r in rows],[1,0])
        self.assertEqual([r['sleep_longest_stay_seconds'] for r in rows],[60,60])
        self.assertEqual(rows[1]['date'],'2026-01-02')

    def test_threshold_contract(self):
        self.assertTrue(presence_for_area('sleep',13,14))
        self.assertFalse(presence_for_area('sleep',14,14))
        self.assertFalse(presence_for_area('toilet',None,13))
        self.assertFalse(presence_for_area('eat',5,20))
        self.assertFalse(presence_for_area('water',5,20))
        self.assertFalse(set(FEATURES)&set(METADATA))

    def test_reproducible_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            args=SimpleNamespace(database=None,assumptions='dataset/routines.json',seed=77,output=temp,baseline_days=2,test_days=1,start_date='2026-01-01')
            simulate(args)
            first=(Path(temp)/'baseline_train.csv').read_bytes()
            simulate(args)
            self.assertEqual(first,(Path(temp)/'baseline_train.csv').read_bytes())
            with (Path(temp)/'baseline_train.csv').open() as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['eat_occupancy_seconds'],'')
            self.assertEqual(rows[0]['sleep_coverage'],'1.0')

    def test_acceleration_keeps_real_five_second_expiry(self):
        from PySide6.QtCore import QCoreApplication
        app=QCoreApplication.instance() or QCoreApplication([])
        hub=QObject()
        hub.mapping={'1':'sleep'}; hub.thresholds={'1':14}; hub.revision=1; hub.acknowledged=1; hub.mode='Live Pi'
        hub.states={'1':dict(distance=10,presence=True,seen=100)}
        with tempfile.TemporaryDirectory() as temp, patch('pet_monitoring.activity_dataset.time.monotonic',return_value=100) as clock:
            path=Path(temp)/'recording.jsonl'
            recorder=ActivityRecorder(hub,path,3600,'2026-01-01')
            clock.return_value=106
            recorder.advance()
            recorder.timer.stop(); recorder.file.close()
            lines=[json.loads(x) for x in path.read_text().splitlines()]
            self.assertEqual(lines[1]['end'],18000)
            self.assertTrue(lines[1]['states']['sleep'])
            self.assertEqual(lines[2]['end'],21600)
            self.assertIsNone(lines[2]['states']['sleep'])
            self.assertEqual(lines[0]['metadata']['source'],'pi_accelerated')

    def test_full_accelerated_day_exports_same_schema(self):
        from PySide6.QtCore import QCoreApplication
        app=QCoreApplication.instance() or QCoreApplication([])
        hub=QObject()
        hub.mapping={'1':'sleep'}; hub.thresholds={'1':14}; hub.revision=1; hub.acknowledged=1; hub.mode='Live Pi'
        hub.states={'1':dict(distance=30,presence=False,seen=100)}
        with tempfile.TemporaryDirectory() as temp, patch('pet_monitoring.activity_dataset.time.monotonic',return_value=100) as clock:
            path=Path(temp)/'day.jsonl'
            recorder=ActivityRecorder(hub,path,3600,'2026-01-01')
            for second in range(1,25):
                clock.return_value=100+second
                recorder.advance()
                active=1 <= second < 3
                hub.states['1'].update(distance=10 if active else 30,presence=active,seen=100+second)
                recorder.updated()
            recorder.close()
            recorder.advance()  # Late shutdown signals must not write a closed file.
            with path.with_suffix('.csv').open() as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),1)
            self.assertEqual(set(rows[0]),set(FEATURES+METADATA))
            self.assertEqual(float(rows[0]['sleep_occupancy_seconds']),7200)
            self.assertEqual(float(rows[0]['sleep_longest_stay_seconds']),7200)
            self.assertEqual(float(rows[0]['sleep_visit_count']),1)
            self.assertEqual(float(rows[0]['sleep_coverage']),1)
            self.assertEqual(rows[0]['toilet_occupancy_seconds'],'')

if __name__=='__main__': unittest.main()
