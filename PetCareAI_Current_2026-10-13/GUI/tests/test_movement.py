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
        from tests.auth_support import configure_auth
        configure_auth(self.c, self.tmp.name)
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

    def test_gesture_telegram_otp_creates_session_and_cannot_replay(self):
        import re
        from tests.test_telegram_admin import TOKEN, CHAT
        from tests.auth_support import MemoryStore, PASSWORD
        from pet_monitoring.services.authentication_service import AuthenticationService
        delivered = []
        auth = AuthenticationService(store=MemoryStore(), marker_path=Path(self.tmp.name)/'telegram.lock',
                                     telegram_sender=lambda token, chat, text: (delivered.append(text) or True, 'Sent'))
        auth.begin_setup(PASSWORD, PASSWORD, telegram_token=TOKEN, telegram_chat_id=CHAT)
        self.assertTrue(auth.request_setup_otp()[0])
        auth.verify_setup_otp(re.search(r'code: ([0-9]{6})', delivered[-1]).group(1))
        auth.complete_setup(True)
        self.c.auth = auth
        self.assertFalse(self.m.request_otp('admin'))
        self.send_result()
        self.assertTrue(self.m.request_otp('admin'))
        self.assertIn('GESTURE LOGIN', delivered[-1])
        code = re.search(r'code: ([0-9]{6})', delivered[-1]).group(1)
        with patch.object(self.m.receiver, 'send_door_command', return_value=True) as commands:
            self.assertTrue(self.m.verify_otp(code))
            self.assertEqual([c.args[0] for c in commands.call_args_list], ['AUTH_OK', 'OTP_OK'])
        self.assertTrue(auth.is_authenticated)
        self.assertIsNone(auth._pending)
        self.assertFalse(self.m.verify_otp(code))
        self.assertEqual(self.m.history[0]['prediction'], 'ACCESS_GRANTED')
        auth.logout()
        # Five wrong codes invalidate the gesture; resending does not grant access.
        self.send_result()
        self.m._last_otp_request = -float('inf')
        self.assertTrue(self.m.request_otp('admin'))
        new_code = re.search(r'code: ([0-9]{6})', delivered[-1]).group(1)
        wrong = '000000' if new_code != '000000' else '111111'
        for _ in range(5):
            self.assertFalse(self.m.verify_otp(wrong))
        self.assertFalse(self.m.gesture_access_ready)
        self.assertFalse(self.m.verify_otp(new_code))
        self.send_result()
        self.m._last_otp_request = -float('inf')
        auth._telegram_sender = lambda *args: (False, 'Failed')
        self.assertFalse(self.m.request_otp('admin'))
        self.assertIsNone(self.m._challenge)
        self.assertFalse(auth.is_authenticated)

    def test_gesture_code_expires_and_disconnect_cancels_access(self):
        self.send_result()
        self.assertTrue(self.m.gesture_access_ready)
        self.m._gesture_until = time.monotonic()-1
        self.m.expire_challenge()
        self.assertFalse(self.m.gesture_access_ready)
        self.send_result()
        self.m.set_connection('Listening - Raspberry Pi disconnected')
        self.assertFalse(self.m.gesture_access_ready)
        self.assertFalse(self.m.verify_otp('123456'))

    def test_entrance_feedback_commands_use_existing_encrypted_transport(self):
        from pet_monitoring.security.edc_transport import EDCCodec
        codec = EDCCodec('ENTRANCECHECK', b'E'*32)
        self.m.receiver._codec = EDCCodec('ENTRANCECHECK', b'E'*32)
        self.assertFalse(self.m.receiver.send_door_command('OTP_OK'))
        self.assertTrue(self.m.receiver.start('127.0.0.1', 0))
        with socket.create_connection(('127.0.0.1', self.m.receiver.port), timeout=2) as client:
            self.assertTrue(wait_for(lambda: self.m.receiver._client_active))
            self.assertTrue(self.m.receiver.send_door_command('AUTH_OK'))
            data = b''
            while b'\n' not in data:
                data += client.recv(65536)
            packet = json.loads(data.split(b'\n',1)[0])
            self.assertEqual(packet['type'], 'EDC_PACKET')
            self.assertEqual(codec.decode(packet), {'type':'DOOR_COMMAND','action':'AUTH_OK'})
        self.assertTrue(wait_for(lambda: not self.m.receiver._client_active))
        self.assertFalse(self.m.receiver.send_door_command('OTP_OK'))

    def test_intruder_alert_uses_verified_admin_telegram_when_alert_fields_blank(self):
        from unittest.mock import Mock
        self.c.settings.update(telegram_mode='Real',telegram_enabled=True,telegram_chat_id='')
        self.c.auth.notification_destination = Mock(return_value=('test-bot-token','123456'))
        response=Mock(status_code=200)
        response.json.return_value={'ok':True}
        with patch.object(self.c.credentials,'load',return_value=''), patch('requests.post',return_value=response) as send:
            self.send_result('APPROACH_STAY')
            alert=self.c.alerts[0]
            self.assertTrue(wait_for(lambda: alert.telegram_sent))
            self.assertEqual(alert.telegram_status,'Sent')
            self.assertEqual(send.call_count,1)
            self.assertEqual(send.call_args.kwargs['json']['chat_id'],'123456')
            self.assertIn('APPROACH_STAY',send.call_args.kwargs['json']['text'])
            self.assertIn('ENTRANCE ALERT',send.call_args.kwargs['json']['text'])

    def test_explicit_alert_destination_and_disabled_fallback_are_respected(self):
        from unittest.mock import Mock
        self.c.auth.notification_destination = Mock(return_value=('admin-token','111'))
        self.c.settings.update(telegram_mode='Real',telegram_enabled=True,telegram_chat_id='222')
        with patch.object(self.c.credentials,'load',return_value='alert-token'):
            service=self.c._telegram_service()
            self.assertEqual((service.token,service.chat_id),('alert-token','222'))
        self.c.auth.notification_destination.assert_not_called()
        self.c.settings.update(telegram_chat_id='',telegram_use_admin=False)
        with patch.object(self.c.credentials,'load',return_value=''):
            service=self.c._telegram_service()
            self.assertEqual((service.token,service.chat_id),('',''))
        self.c.auth.notification_destination.assert_not_called()

    def test_mock_or_disabled_notifications_do_not_use_admin_credentials(self):
        from unittest.mock import Mock
        self.c.auth.notification_destination=Mock(return_value=('admin-token','111'))
        self.c.settings.update(telegram_mode='Mock',telegram_enabled=True,telegram_chat_id='')
        with patch.object(self.c.credentials,'load',return_value=''):
            self.assertEqual(self.c._telegram_service().mode,'Mock')
            self.c.settings.update(telegram_mode='Real',telegram_enabled=False)
            self.assertFalse(self.c._telegram_service().enabled)
        self.c.auth.notification_destination.assert_not_called()
