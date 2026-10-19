import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from pet_monitoring.controller import MonitoringController
from tests.auth_support import configure_auth, login
from pet_monitoring.hub_controller import HubController, DEFAULT_MAPPING
from pet_monitoring.services.database_service import DatabaseService
from pet_monitoring.services.movement_receiver import MovementReceiver, parse_message

APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    end = time.monotonic() + 3
    while time.monotonic() < end and not predicate():
        APP.processEvents()
        QTest.qWait(10)
    return predicate()


class HubTests(unittest.TestCase):
    def test_food_water_calibration_percentage_volume_and_history(self):
        login(self.c.auth)
        values = {"1": {"full_cm": 2, "empty_cm": 10, "capacity_l": 1, "linear_volume": True},
                  "3": {"full_cm": 2, "empty_cm": 10, "capacity_l": 2, "linear_volume": False}}
        self.assertTrue(self.hub.save_level_calibration(values)[0])
        self.hub.set_mode("Live Pi")
        self.hub.process({"type": "CONFIG_ACK", "revision": self.hub.revision})
        for sensor in ("1", "3"):
            self.hub.process({"type": "SENSOR_READING", "sensor_id": sensor, "revision": self.hub.revision, "distance_cm": 6, "presence": True})
        self.assertEqual(self.hub.level("1")[:2], (50, .5))
        self.assertEqual(self.hub.level("3")[:2], (50, None))
        self.assertFalse(self.hub.states["1"]["presence"])
        self.assertEqual(len(self.hub.level_history()), 2)
        for distance, expected in ((2, 100), (8, 25), (10, 0), (12, 0)):
            self.hub.states["1"]["distance"] = distance
            self.assertEqual(self.hub.level("1")[0], expected)
        self.hub.process({"type": "ZONE_EVENT", "sensor_id": "1", "revision": self.hub.revision, "event_id": "not-a-pet-visit", "duration_seconds": 5})
        self.assertEqual(self.hub.history(), [])
        self.hub.states["1"]["seen"] = time.monotonic() - 6
        self.assertIsNone(self.hub.level("1")[0])
        self.assertFalse(self.hub.save_level_calibration({"1": {"full_cm": 10, "empty_cm": 2, "capacity_l": 1}})[0])
        self.hub.level_calibration = {}
        self.hub.states["3"]["distance"] = 6
        self.assertIn("Calibration required", self.hub.level("3")[2])

    def test_report_thresholds_and_live_activity_duration(self):
        from unittest.mock import patch
        self.assertEqual(self.hub.thresholds["2"], 14)
        self.assertEqual(self.hub.thresholds["4"], 13)
        self.hub.set_mode("Live Pi")
        self.hub.process({"type": "CONFIG_ACK", "revision": self.hub.revision})
        with patch('pet_monitoring.hub_controller.time.monotonic', return_value=100):
            self.hub._activity_clock = 100
            for sensor, distance in (("2", 14), ("4", 13)):
                self.hub.process({"type": "SENSOR_READING", "sensor_id": sensor, "revision": self.hub.revision, "distance_cm": distance, "presence": True})
                self.assertFalse(self.hub.states[sensor]["presence"])
            for sensor, distance in (("2", 13.9), ("4", 12.9)):
                self.hub.process({"type": "SENSOR_READING", "sensor_id": sensor, "revision": self.hub.revision, "distance_cm": distance, "presence": False})
                self.assertTrue(self.hub.states[sensor]["presence"])
        self.hub._advance_activity(now=103)
        with patch('pet_monitoring.hub_controller.time.monotonic', return_value=103):
            self.assertEqual(self.hub.activity("sleep")[2:], (3, 3))
            self.assertEqual(self.hub.activity("toilet")[2:], (3, 3))
        self.hub._advance_activity(now=110)
        with patch('pet_monitoring.hub_controller.time.monotonic', return_value=110):
            self.assertFalse(self.hub.activity("sleep")[0])
            self.assertEqual(self.hub.activity("sleep")[3], 5)
        self.hub.states["2"]["distance"] = None
        self.hub.states["4"]["distance"] = None

    def test_duration_union_and_midnight_split(self):
        from datetime import datetime
        self.hub.set_mode("Live Pi")
        self.hub.mapping["1"] = "sleep"
        self.hub._activity_clock = 100
        for sensor in ("1", "2"):
            self.hub.states[sensor].update(distance=10, presence=True, seen=100)
        self.hub._advance_activity(now=102, wall=datetime(2026, 10, 13, 0, 0, 1))
        rows = self.db.execute("SELECT day,seconds FROM hub_activity_daily WHERE mode='sleep' ORDER BY day").fetchall()
        self.assertEqual(rows, [("2026-10-12", 1), ("2026-10-13", 1)])
        self.hub._reset_states()

    def setUp(self):
        saved_keys = patch("pet_monitoring.services.movement_receiver.has_saved_keys", return_value=False)
        saved_keys.start()
        self.addCleanup(saved_keys.stop)
        environment = patch.dict(os.environ, {"PETCARE_EDC": "0"})
        environment.start()
        self.addCleanup(environment.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseService(Path(self.tmp.name) / "hub.db")
        self.c = MonitoringController(self.db)
        configure_auth(self.c, self.tmp.name)
        self.receiver = MovementReceiver()
        self.hub = HubController(self.c, self.receiver)

    def tearDown(self):
        self.hub.close()
        self.receiver.stop()
        self.c.close()
        self.c.executor.shutdown(wait=True)
        APP.processEvents()
        self.db.close()
        self.tmp.cleanup()

    def test_mapping_save_requires_login_and_keeps_sensor_five_fixed(self):
        mapping = {"1": "water", "2": "eat", "3": "toilet", "4": "sleep"}
        thresholds = {key: 20 for key in mapping}
        self.assertFalse(self.hub.save_configuration(mapping, thresholds))
        login(self.c.auth)
        self.assertTrue(self.hub.save_configuration(mapping, thresholds))
        self.assertEqual(self.db.get("hub_configuration")["mapping"], mapping)
        self.assertEqual(json.loads(self.receiver._config)["gesture_sensor"], 5)
        with self.assertRaises(ValueError):
            self.hub.save_configuration({**mapping, "5": "eat"}, thresholds)

    def test_confirmed_revision_and_event_deduplication(self):
        self.hub.set_mode("Live Pi")
        event = {"type": "ZONE_EVENT", "sensor_id": "2", "event_id": "visit-1", "duration_seconds": 6, "revision": 1}
        self.hub.process(event)
        self.assertEqual(self.hub.history(), [])
        self.hub.process({"type": "CONFIG_ACK", "revision": 1})
        self.hub.process(event)
        self.hub.process(event)
        self.assertEqual(self.hub.totals()["sleep"], (1, 6))
        self.hub.process({**event, "revision": 2, "event_id": "invalid-revision"})
        self.assertEqual(len(self.hub.history()), 1)

    def test_mapping_preserves_historical_zone_and_stale_readings_expire(self):
        login(self.c.auth)
        self.hub.set_mode("Live Pi")
        self.hub.mapping["1"] = "sleep"
        self.hub.process({"type": "CONFIG_ACK", "revision": 1})
        self.hub.process({"type": "ZONE_EVENT", "sensor_id": "1", "event_id": "old", "duration_seconds": 4, "revision": 1})
        self.hub.save_configuration({**DEFAULT_MAPPING, "1": "water"}, self.hub.thresholds)
        self.assertEqual(self.hub.history()[0][2], "sleep")
        self.assertIsNone(self.hub.acknowledged)
        self.hub.process({"type": "CONFIG_ACK", "revision": 2})
        self.hub.process({"type": "SENSOR_READING", "sensor_id": "1", "distance_cm": 10, "presence": True, "revision": 2})
        self.assertFalse(self.hub.states["1"]["presence"])  # Water measures level, not pet presence.
        self.hub.states["1"]["seen"] = time.monotonic() - 6
        self.hub.tick()
        self.assertIsNone(self.hub.states["1"]["distance"])

    def test_protocol_rejects_sensor_five_zone_and_wrong_gesture_sensor(self):
        for message in [
            {"type": "SENSOR_READING", "sensor_id": 5, "distance_cm": 10, "presence": True, "revision": 1},
            {"type": "MOVEMENT_RESULT", "sensor_id": 1, "prediction": "PASS_BY", "confidence": .9},
            {"type": "SENSOR_READING", "sensor_id": 1, "distance_cm": None, "presence": True, "revision": 1},
            {"type": "ZONE_EVENT", "sensor_id": 1, "event_id": "a", "duration_seconds": -1, "revision": 1}]:
            with self.assertRaises(ValueError):
                parse_message(json.dumps(message))

    def test_tcp_sends_configuration_and_accepts_pi_ack(self):
        self.hub.set_mode("Live Pi")
        self.assertTrue(self.receiver.start("127.0.0.1", 0))
        with socket.create_connection(("127.0.0.1", self.receiver.port), timeout=2) as client:
            config = json.loads(client.makefile("rb").readline())
            self.assertEqual(config["mapping"], DEFAULT_MAPPING)
            client.sendall(b'{"type":"CONFIG_ACK","revision":1}\n')
            self.assertTrue(wait_for(lambda: self.hub.acknowledged == 1))
            login(self.c.auth)
            self.hub.save_configuration({**DEFAULT_MAPPING, "1": "water"}, self.hub.thresholds)
            config = json.loads(client.makefile("rb").readline())
            self.assertEqual(config["revision"], 2)
            self.assertEqual(config["mapping"]["1"], "water")

    def test_demo_runs_without_navigation_and_remains_separate(self):
        for _ in range(10):
            self.hub.tick()
        self.assertTrue(self.hub.history())
        self.hub.set_mode("Live Pi")
        self.assertEqual(self.hub.history(), [])
