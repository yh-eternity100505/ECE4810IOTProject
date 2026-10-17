import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("pi_service", ROOT / "predict_movement.py")
pi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pi)
CONFIG = {"type": "SET_CONFIG", "revision": 1, "mapping": {"1": "eat", "2": "sleep", "3": "water", "4": "toilet"}, "thresholds_cm": {str(i): 20 for i in range(1, 5)}, "gesture_sensor": 5}


class PiTests(unittest.TestCase):
    def test_feature_definitions_match_original(self):
        def feature_ast(path):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "extract_features")
            fn.body = [n for n in fn.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
            return ast.dump(fn, include_attributes=False)
        self.assertEqual(feature_ast(ROOT / "predict_movement_original.py"), feature_ast(ROOT / "predict_movement.py"))

    def test_feature_order_and_confidence(self):
        import numpy as np
        names = ["mean_distance", "min_distance", "max_distance", "std_distance", "q1", "q3", "start_mean", "end_mean", "end_minus_start", "slope", "close_ratio", "mid_ratio", "far_ratio", "threshold_crossings"]
        result = pi.extract_features([10] * 60, names, 60, .1, 15, 25, 20)
        np.testing.assert_allclose(result, [10, 10, 10, 0, 10, 10, 10, 10, 0, 0, 1, 0, 0, 0], atol=1e-10)
        model = Mock(classes_=["PASS_BY", "AUTH_GESTURE"])
        model.predict.return_value = ["AUTH_GESTURE"]
        model.predict_proba.return_value = [[.08, .92]]
        prediction, confidence = pi.classify_movement([10] * 60, {"close_cm": 15, "mid_max_cm": 25, "crossing_cm": 20}, model, names, 60, .1)
        self.assertEqual((prediction, confidence), ("AUTH_GESTURE", .92))

    def test_pins_and_invalid_config(self):
        pins = [pin for pair in pi.PINS.values() for pin in pair]
        self.assertEqual(len(pins), len(set(pins)))
        self.assertEqual(pi.PINS["5"], (7, 11))
        with self.assertRaises(ValueError):
            pi.validate_configuration({**CONFIG, "gesture_sensor": 1})

    def test_zone_visits_reassignment_and_missing_echo(self):
        sender = Mock()
        sender.ready = threading.Event()
        sender.ready.set()
        tracker = pi.ZoneTracker()
        with tempfile.TemporaryDirectory() as temp:
            tracker.apply(CONFIG, Path(temp) / "config.json")
            tracker.read("1", 10, 1, sender)
            tracker.read("1", 50, 7, sender)
            events = [c for c in sender.send.call_args_list if c.args[0] == "ZONE_EVENT"]
            self.assertEqual(events[0].kwargs["duration_seconds"], 6)
            tracker.read("1", 10, 8, sender)
            tracker.read("1", None, 9, sender)
            tracker.read("1", 50, 10, sender)
            self.assertEqual(len([c for c in sender.send.call_args_list if c.args[0] == "ZONE_EVENT"]), 1)
            tracker.read("1", 10, 11, sender)
            tracker.apply({**CONFIG, "revision": 2}, Path(temp) / "config.json")
            self.assertIsNone(tracker.started["1"])

    def test_gesture_samples_fill_rearm_and_overrun(self):
        sender = Mock()
        recorder = pi.GestureRecorder(60, .1)
        self.assertIsNone(recorder.sample(10, 0, sender))
        sequence = None
        for index in range(1, 60):
            sequence = recorder.sample(None if index == 10 else 10, index * .1, sender)
        self.assertEqual(len(sequence), 60)
        self.assertEqual(sequence[10], 10)
        for index in range(3):
            recorder.sample(50, 6 + index * .1, sender)
        self.assertFalse(recorder.waiting_clear)
        recorder.sample(10, 7, sender)
        self.assertIsNone(recorder.sample(10, 7.3, sender))
        self.assertIsNone(recorder.sequence)

    def test_simulated_script_against_real_gui_protocol(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, str(ROOT.parent / "GUI"))
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest
        from pet_monitoring.controller import MonitoringController
        from pet_monitoring.hub_controller import HubController
        from pet_monitoring.movement_controller import MovementController
        from pet_monitoring.services.database_service import DatabaseService
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            db = DatabaseService(Path(temp) / "test.db")
            controller = MonitoringController(db)
            movement = MovementController(controller)
            hub = HubController(controller, movement.receiver)
            hub.set_mode("Live Pi")
            self.assertTrue(movement.receiver.start("127.0.0.1", 0))
            process = subprocess.Popen([sys.executable, "-u", str(ROOT / "predict_movement.py"), "--gui-host", "127.0.0.1", "--gui-port", str(movement.receiver.port), "--simulate", "--config-file", str(Path(temp) / "config.json")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                end = time.monotonic() + 12
                while time.monotonic() < end and not (hub.history() and movement.history):
                    app.processEvents()
                    QTest.qWait(10)
                self.assertEqual(hub.acknowledged, 1)
                self.assertTrue(movement.gesture_seen)
                self.assertIsNotNone(movement.gesture_distance)
                self.assertTrue(hub.history())
                self.assertEqual(movement.history[0]["prediction"], "PASS_BY")
                self.assertFalse(controller.auth.is_authenticated)
                controller.auth.authenticate("admin", "pet123")
                hub.save_configuration({**hub.mapping, "1": "water"}, hub.thresholds)
                end = time.monotonic() + 3
                while time.monotonic() < end and hub.acknowledged != 2:
                    app.processEvents()
                    QTest.qWait(10)
                self.assertEqual(hub.acknowledged, 2)
                self.assertEqual(json.loads((Path(temp) / "config.json").read_text())["mapping"]["1"], "water")
            finally:
                process.terminate()
                out, err = process.communicate(timeout=3)
                self.assertEqual(err, "", msg=out + err)
                hub.close()
                movement.close()
                controller.close()
                controller.executor.shutdown(wait=True)
                app.processEvents()
                db.close()


if __name__ == "__main__":
    unittest.main()

