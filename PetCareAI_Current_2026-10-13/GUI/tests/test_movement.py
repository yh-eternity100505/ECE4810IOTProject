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
from pet_monitoring.movement_controller import MovementController
from pet_monitoring.services.database_service import DatabaseService
from pet_monitoring.services.movement_receiver import parse_message

APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        QTest.qWait(10)
    return predicate()


class MovementTests(unittest.TestCase):
    def setUp(self):
        saved_keys = patch("pet_monitoring.services.movement_receiver.has_saved_keys", return_value=False)
        saved_keys.start()
        self.addCleanup(saved_keys.stop)
        environment = patch.dict(os.environ, {"PETCARE_EDC": "0"})
        environment.start()
        self.addCleanup(environment.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseService(Path(self.tmp.name) / "test.db")
        self.c = MonitoringController(self.db)
        self.m = MovementController(self.c)

    def tearDown(self):
        self.m.close()
        self.c.close()
        self.c.executor.shutdown(wait=True)
        APP.processEvents()
        self.db.close()
        self.tmp.cleanup()

    def send_result(self, prediction="AUTH_GESTURE"):
        self.m.process({"type": "MOVEMENT_RESULT", "prediction": prediction, "confidence": .94})

    def test_validation(self):
        self.assertEqual(parse_message('{"type":"MOVEMENT_RESULT","class":"PASS_BY","confidence":0.9}')["prediction"], "PASS_BY")
        for value in [True, "0.9", -1, 2, float("nan")]:
            with self.assertRaises(ValueError):
                parse_message(json.dumps({"type": "MOVEMENT_RESULT", "prediction": "PASS_BY", "confidence": value}))

    def test_live_gesture_distance_no_echo_and_expiry(self):
        self.m.set_connection("Raspberry Pi connected · 10.0.0.1")
        self.assertIn("waiting for Sensor 5", self.m.gesture_status)
        self.send_result()
        self.assertIn("live distance not received", self.m.gesture_status)
        self.m.history.clear()
        packet = {"type": "GESTURE_READING", "sensor_id": 5, "distance_cm": 12.5, "state": "RECORDING", "samples": 20, "total": 60}
        self.m.process(parse_message(json.dumps(packet)))
        self.assertEqual(self.m.gesture_distance, 12.5)
        self.assertIn("20/60", self.m.gesture_status)
        self.assertEqual(self.m.history, [])
        self.assertFalse(self.c.auth.is_authenticated)
        self.m.process({**packet, "distance_cm": None})
        self.assertIn("No valid echo", self.m.gesture_status)
        self.m.gesture_seen = time.monotonic() - 6
        self.m.expire_challenge()
        self.assertIn("Offline", self.m.gesture_status)
        self.m.process(packet)
        self.m.set_connection("Listening · Raspberry Pi disconnected")
        self.assertIsNone(self.m.gesture_distance)
        for value in (float('nan'), -1, 201, True):
            with self.assertRaises(ValueError):
                parse_message(json.dumps({**packet, "distance_cm": value}))

    def test_decisions_cannot_create_an_administrator_session(self):
        self.send_result("PASS_BY")
        self.assertEqual(len(self.m.history), 1)
        self.send_result("APPROACH_STAY")
        self.assertTrue(any(a.title == "Suspicious approach detected" for a in self.c.alerts))
        self.send_result()
        for username in ("admin", "unknown", ""):
            self.assertFalse(self.m.request_otp(username))
            self.assertIsNone(self.m._challenge)
            self.assertEqual(self.m.demo_code, "")
            self.assertFalse(self.m.verify_otp("123456"))
            self.assertFalse(self.c.auth.is_authenticated)

    def test_each_approach_sends_telegram_and_records_delivery(self):
        from unittest.mock import Mock
        service = Mock(mode="Real")
        service.send_alert.return_value = (True, "Sent")
        self.c._telegram_service = lambda: service
        # Entrance notifications are independent of simulated packet security.
        self.c.settings["notifications"]["security"] = False
        for expected in (1, 2):
            self.send_result("APPROACH_STAY")
            alert = self.c.alerts[0]
            self.assertTrue(wait_for(lambda: alert.telegram_sent))
            self.assertEqual(service.send_alert.call_count, expected)
            self.assertIn("ENTRANCE ALERT", alert.message)
            self.assertEqual(alert.category, "approach")
            self.assertFalse(self.c.auth.is_authenticated)
        self.c.settings["notifications"]["approach"] = False
        self.send_result("APPROACH_STAY")
        self.assertEqual(self.c.alerts[0].telegram_status, "Disabled · category")
        self.assertEqual(service.send_alert.call_count, 2)

    def test_real_delivery_failure_never_grants_access(self):
        from unittest.mock import Mock
        service = Mock(mode="Real")
        service.send_message.return_value = (False, "Not configured")
        self.c._telegram_service = lambda: service
        self.send_result()
        self.m.request_otp("admin")
        self.assertTrue(wait_for(lambda: self.m._challenge is None))
        self.assertFalse(self.c.auth.is_authenticated)
        self.assertEqual(self.m.demo_code, "")

    def test_tcp_fragmentation_reconnect_and_stop(self):
        self.assertTrue(self.m.receiver.start("127.0.0.1", 0))
        port = self.m.receiver.port
        with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
            client.sendall(b'{"type":"TRIG')
            client.sendall(b'GERED"}\n{"type":"MOVEMENT_RESULT","prediction":"PASS_BY","confidence":0.8}\n')
        self.assertTrue(wait_for(lambda: len(self.m.history) == 1))
        with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
            client.sendall(b'{"type":"MOVEMENT_RESULT","prediction":"AUTH_GESTURE","confidence":0.9}\n')
        self.assertTrue(wait_for(lambda: len(self.m.history) == 2))
        self.assertFalse(self.c.auth.is_authenticated)
        self.m.receiver.stop()
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=.2)

    def test_legacy_telegram_otp_does_not_send_or_grant_access(self):
        from unittest.mock import Mock
        service = Mock(mode="Real")
        self.c._telegram_service = lambda: service
        self.send_result()
        self.m.request_otp("admin")
        service.send_message.assert_not_called()
        self.assertFalse(self.m.verify_otp("123456"))
        self.assertFalse(self.c.auth.is_authenticated)
