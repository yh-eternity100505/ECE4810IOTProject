import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import socket
import tempfile
import time
import unittest
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

    def test_decisions_and_single_use_otp(self):
        self.send_result("PASS_BY")
        self.assertFalse(self.c.auth.is_authenticated)
        self.assertEqual(len(self.m.history), 1)
        self.send_result("APPROACH_STAY")
        self.assertTrue(any(a.title == "Suspicious approach detected" for a in self.c.alerts))
        self.send_result()
        self.assertFalse(self.c.auth.is_authenticated)
        self.m.request_otp("admin")
        code = self.m.demo_code
        self.assertEqual(len(code), 6)
        self.assertTrue(self.m.verify_otp(code))
        self.assertTrue(self.c.auth.is_authenticated)
        self.assertFalse(self.m.verify_otp(code))
        self.assertNotIn(code, json.dumps(self.db.get("movement_history")))

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

    def test_expiry_attempt_limit_and_username(self):
        self.m.request_otp("admin")
        self.assertIsNone(self.m._challenge)
        self.send_result()
        self.m.request_otp("unknown")
        self.assertIsNone(self.m._challenge)
        self.m.request_otp("admin")
        self.m._challenge["expires"] = 0
        self.assertFalse(self.m.verify_otp(self.m.demo_code))
        self.send_result()
        self.m._last_request = -60
        self.m.request_otp("admin")
        wrong = "000000" if self.m.demo_code != "000000" else "111111"
        for _ in range(5):
            self.assertFalse(self.m.verify_otp(wrong))
        self.assertIsNone(self.m._challenge)
        self.assertFalse(self.c.auth.is_authenticated)

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

    def test_pending_delivery_and_cancelled_callback(self):
        self.send_result()
        self.m.request_otp("admin")
        code = self.m.demo_code
        self.m._challenge["ready"] = False
        self.assertFalse(self.m.verify_otp(code))
        generation = self.m._generation
        self.m.cancel_authentication()
        self.m._delivered(generation, True, "Sent")
        self.assertIsNone(self.m._challenge)
        self.assertFalse(self.m.verify_otp(code))
        self.assertFalse(self.c.auth.is_authenticated)

    def test_successful_real_otp_stays_hidden_until_verification(self):
        from unittest.mock import Mock
        service = Mock(mode="Real")
        service.send_message.return_value = (True, "Sent")
        self.c._telegram_service = lambda: service
        self.send_result()
        self.m.request_otp("admin")
        code = self.m._challenge["code"]
        self.assertTrue(wait_for(lambda: self.m._challenge["ready"]))
        self.assertEqual(self.m.demo_code, "")
        self.assertFalse(self.c.auth.is_authenticated)
        self.assertTrue(self.m.verify_otp(code))
