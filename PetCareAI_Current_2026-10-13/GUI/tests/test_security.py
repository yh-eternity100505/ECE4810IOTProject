import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import base64
import copy
import json
import tempfile
import time
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from pet_monitoring.services.security_service import SecurityService
from pet_monitoring.services.authentication_service import AuthenticationService
from pet_monitoring.services.database_service import DatabaseService
from pet_monitoring.models import SensorReading
from pet_monitoring.controller import MonitoringController
from pet_monitoring.ui.main_window import MainWindow

APP = QApplication.instance() or QApplication([])


def payload():
    return {"schema": 1, "pet_id": "DOG002", "pet_name": "Milo", "reading": asdict(
        SensorReading("food", "Food Area", 14.3, True, datetime.now().isoformat(timespec="seconds")))}


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        QTest.qWait(10)
    return predicate()


class SecurityServiceTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.s = SecurityService(event_sink=self.events.append)

    def packet(self):
        return self.s.create_secure_packet(payload(), "SENSOR_FOOD")

    def test_real_encryption_roundtrip_and_wire_has_no_plaintext(self):
        packet = self.packet()
        wire = packet.to_wire()
        for text in (b"DOG002", b"Milo", b"distance_cm", b"14.3", b"pet_name"):
            self.assertNotIn(text, wire)
        self.assertNotIn(b"verified", wire)
        self.assertTrue(self.s.verify_authentication_tag(packet))
        result = self.s.process_secure_packet(wire)
        self.assertTrue(result.accepted)
        self.assertTrue(result.packet.verified)
        self.assertEqual(result.data["pet_id"], "DOG002")
        self.assertEqual(result.data["reading"]["distance_cm"], 14.3)
        self.assertEqual([e.event_type for e in self.events], ["PACKET_VERIFIED", "PACKET_DECRYPTED"])
        self.assertGreater(self.s.metrics.average_encryption_ms, 0)
        self.assertGreater(self.s.metrics.average_verification_ms, 0)
        self.assertGreater(self.s.metrics.average_decryption_ms, 0)

    def test_changed_ciphertext_is_rejected_without_decrypting(self):
        packet = self.packet()
        cipher = bytearray(base64.b64decode(packet.ciphertext))
        cipher[0] ^= 1
        changed = replace(packet, ciphertext=base64.b64encode(cipher).decode())
        with patch.object(self.s.backend, "decrypt", wraps=self.s.backend.decrypt) as decrypt:
            result = self.s.decrypt_message(changed)
        decrypt.assert_not_called()
        self.assertFalse(result.accepted)
        self.assertIsNone(result.data)
        self.assertEqual(result.reason, "PACKET_REJECTED_HMAC_FAILURE")
        self.assertEqual(self.s.metrics.rejected_packets, 1)
        self.assertEqual(self.s.metrics.decrypted_packets, 0)
        self.assertEqual(self.s.metrics.success_rate, 0)

    def test_metadata_is_authenticated(self):
        for key, value in [("packet_id", "modified_id"), ("source_id", "SENSOR_WATER"),
                           ("timestamp", datetime.now(timezone.utc).isoformat()),
                           ("nonce", base64.b64encode(b"X" * 16).decode())]:
            with self.subTest(field=key):
                packet = replace(self.packet(), **{key: value})
                with patch.object(self.s.backend, "decrypt") as decrypt:
                    result = self.s.process_secure_packet(packet)
                decrypt.assert_not_called()
                self.assertEqual(result.reason, "PACKET_REJECTED_HMAC_FAILURE")

    def test_malformed_packets_and_missing_tag(self):
        valid = json.loads(self.packet().to_wire())
        missing = valid.copy()
        missing.pop("authentication_tag")
        cases = [b"not json", b"[]", b"null", {}, missing,
                 {**valid, "authentication_tag": ""}, {**valid, "ciphertext": "not-base64!"},
                 {**valid, "nonce": ""}, {**valid, "verified": True}, {**valid, "version": True},
                 {**valid, "ciphertext": "A" * 100001}]
        for wire in cases:
            with self.subTest(packet_type=type(wire).__name__):
                with patch.object(self.s.backend, "decrypt") as decrypt:
                    result = self.s.process_secure_packet(wire)
                decrypt.assert_not_called()
                self.assertFalse(result.accepted)
                self.assertEqual(result.reason, "PACKET_REJECTED_MALFORMED")

    def test_replayed_and_old_packets_not_decrypted(self):
        packet = self.packet()
        self.assertTrue(self.s.process_secure_packet(packet).accepted)
        with patch.object(self.s.backend, "decrypt") as decrypt:
            replay = self.s.process_secure_packet(packet)
        decrypt.assert_not_called()
        self.assertEqual(replay.reason, "PACKET_REJECTED_REPLAY")
        old = replace(self.packet(), timestamp=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
        old.authentication_tag = self.s.create_authentication_tag(old)
        with patch.object(self.s.backend, "decrypt") as decrypt:
            result = self.s.process_secure_packet(old)
        decrypt.assert_not_called()
        self.assertEqual(result.reason, "PACKET_REJECTED_REPLAY")

    def test_wrong_receiver_key_cannot_decrypt(self):
        other = SecurityService()
        with patch.object(other.backend, "decrypt") as decrypt:
            result = other.process_secure_packet(self.packet())
        decrypt.assert_not_called()
        self.assertEqual(result.reason, "PACKET_REJECTED_HMAC_FAILURE")

    def test_backend_adapter_and_separate_receiver_with_provisioned_key(self):
        from pet_monitoring.security.edc_adapter import EDCAdapter, DemoCipherBackend
        # Verify adapter plumbing with a test cipher; this does not implement EDC.
        test_cipher = DemoCipherBackend()
        adapter = EDCAdapter(test_cipher.encrypt, test_cipher.decrypt)
        shared_key = os.urandom(32)
        sender = SecurityService(adapter, authentication_key=shared_key)
        receiver = SecurityService(adapter, authentication_key=shared_key)
        result = receiver.process_secure_packet(sender.create_secure_packet(payload(), "SENSOR_FOOD").to_wire())
        self.assertTrue(result.accepted)
        self.assertEqual(result.data["reading"]["distance_cm"], 14.3)
        with self.assertRaises(ValueError):
            SecurityService(authentication_key=b"short")

    def test_decryption_failure_and_invalid_recovered_payload(self):
        with patch.object(self.s.backend, "decrypt", side_effect=RuntimeError("sensitive-key-do-not-log")):
            result = self.s.process_secure_packet(self.packet())
        self.assertEqual(result.reason, "PACKET_REJECTED_DECRYPTION_FAILURE")
        bad = payload()
        bad["reading"]["sensor_id"] = "water"
        packet = self.s.create_secure_packet(bad, "SENSOR_FOOD")
        result = self.s.process_secure_packet(packet)
        self.assertEqual(result.reason, "PACKET_REJECTED_INVALID_PAYLOAD")
        self.assertIsNone(result.data)

    def test_service_offline_does_not_decrypt(self):
        packet = self.packet()
        self.s.available = False
        with patch.object(self.s.backend, "decrypt") as decrypt:
            result = self.s.process_secure_packet(packet)
        decrypt.assert_not_called()
        self.assertEqual(result.reason, "SECURITY_MODULE_OFFLINE")


class SecurityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "app.db"
        self.db = DatabaseService(self.path)
        self.c = MonitoringController(self.db)
        self.w = MainWindow(self.c)
        self.w.show()
        APP.processEvents()

    def tearDown(self):
        self.w.close()
        self.c.executor.shutdown(wait=True)
        APP.processEvents()
        self.db.close()
        self.w.deleteLater()
        APP.processEvents()
        self.tmp.cleanup()

    def login(self):
        self.w.login_page.username.setText("admin")
        self.w.login_page.password.setText("pet123")
        self.w.login_page.login_button.click()
        APP.processEvents()
        self.assertEqual(self.w.root.currentIndex(), 1)
        self.assertTrue(self.c.auth.is_authenticated)

    def test_login_is_required_and_expired_session_returns_to_login(self):
        self.assertEqual(self.c.security_status, "Authentication Required")
        with patch.object(self.c.sensor, "read") as read:
            self.c.tick()
        read.assert_not_called()
        self.assertEqual(self.c.security.metrics.total_packets, 0)
        self.login()
        self.assertEqual(self.c.connected_count, 5)
        previous = copy.deepcopy(self.c.readings)
        self.c.auth.session.expires_at = time.monotonic() - 1
        with patch.object(self.c.security.backend, "decrypt") as decrypt:
            self.c.tick()
        decrypt.assert_not_called()
        self.assertEqual(self.c.readings, previous)
        self.assertEqual(self.w.root.currentIndex(), 0)
        self.assertFalse(self.c.timer.isActive())
        self.assertIn("Session expired", self.w.login_page.status.text())
        self.assertTrue(any(e.event_type == "SESSION_EXPIRED" for e in self.db.security_events()))

    def test_normal_and_tampered_demo_buttons_execute_real_path(self):
        self.login()
        self.c.timer.stop()  # Isolate exact state changes from legitimate later timer updates.
        page = self.w.pages[6]
        page.demo_buttons["secure_packet"].click()
        self.assertEqual(self.c.location, "Food Area")
        self.assertEqual(self.c.behaviour, "Eating")
        food = next(r for r in self.c.readings if r.sensor_id == "food")
        self.assertEqual(food.distance_cm, 14.3)
        self.assertEqual(self.c.security_inspector["verification"], "PASSED")
        self.assertIn("HMAC Verified", self.w.pages[0].security_card.values["Integrity"].text())
        self.assertIn("Security: Secure", self.w.global_status.text())
        before = (copy.deepcopy(self.c.readings), self.c.location, self.c.behaviour,
                  copy.deepcopy(self.c.summary), copy.deepcopy(self.c.latest_event))
        received = self.c.security.metrics.total_packets
        rejected = self.c.security.metrics.rejected_packets
        with patch.object(self.c.security.backend, "decrypt", wraps=self.c.security.backend.decrypt) as decrypt:
            page.demo_buttons["tampered_packet"].click()
        decrypt.assert_not_called()
        self.assertEqual(before, (self.c.readings, self.c.location, self.c.behaviour, self.c.summary, self.c.latest_event))
        self.assertEqual(self.c.security.metrics.total_packets, received + 1)
        self.assertEqual(self.c.security.metrics.rejected_packets, rejected + 1)
        self.assertIn("Not decrypted", self.c.security_inspector["decrypted"])
        self.assertIn("Verification Failed", self.w.global_status.text())
        alert = next(a for a in self.c.alerts if a.category == "security")
        self.assertEqual(alert.title, "IoT Packet Integrity Failure")
        self.assertTrue(wait_for(lambda: alert.telegram_sent))
        self.assertIn("Mock", alert.telegram_status)
        types = [e.event_type for e in self.db.security_events()]
        self.assertIn("PACKET_REJECTED_HMAC_FAILURE", types)
        self.assertIn("TELEGRAM_ALERT_SENT", types)
        self.assertTrue(any("Tampered packet rejected" in page.security_panel.log.item(i, 1).text()
                            for i in range(page.security_panel.log.rowCount())))

    def test_corrupted_automatic_tick_is_atomic(self):
        self.login()
        self.c.timer.stop()
        before = copy.deepcopy(self.c.readings)
        original_create = self.c.security.create_secure_packet
        def corrupt(data, source):
            packet = original_create(data, source)
            if source == "SENSOR_FOOD":
                raw = bytearray(base64.b64decode(packet.ciphertext))
                raw[0] ^= 1
                packet.ciphertext = base64.b64encode(raw).decode()
            return packet
        with patch.object(self.c.security, "create_secure_packet", side_effect=corrupt):
            self.c.tick()
        self.assertEqual(self.c.readings, before)
        self.assertEqual(self.c.security.metrics.rejected_packets, 1)
        self.assertEqual(self.c.security_status, "Verification Failed")

    def test_security_events_persist_and_secrets_do_not(self):
        self.login()
        self.c.simulate_secure_packet(True)
        self.assertTrue(wait_for(lambda: any(a.telegram_sent for a in self.c.alerts if a.category == "security")))
        contents = " ".join(row[0] for row in self.db.execute("SELECT payload FROM security_events").fetchall())
        config = " ".join(row[0] for row in self.db.execute("SELECT value FROM config").fetchall())
        for secret in ("pet123", self.c.security._authentication_key.hex(), self.c.security.backend._key.hex(), self.c.auth.session.identifier):
            self.assertNotIn(secret, contents + config)
        reopened = DatabaseService(self.path)
        try:
            self.assertTrue(any(e.event_type == "PACKET_REJECTED_HMAC_FAILURE" for e in reopened.security_events()))
            self.assertTrue(any(a.category == "security" for a in reopened.alerts()))
            self.assertIn("hash", reopened.get("authentication_users")["admin"])
        finally:
            reopened.close()

    def test_repeated_invalid_login_creates_alert_without_logging_password(self):
        with self.assertLogs(level="INFO") as logs:
            for _ in range(3):
                self.assertFalse(self.c.auth.authenticate("admin", "do-not-log-password"))
        self.assertNotIn("do-not-log-password", " ".join(logs.output))
        self.assertTrue(any(a.title == "Repeated authentication failure" for a in self.c.alerts))
        self.assertTrue(any(e.event_type == "REPEATED_AUTHENTICATION_FAILURE" for e in self.db.security_events()))

    def test_security_offline_keeps_previous_readings(self):
        self.login()
        self.c.timer.stop()
        before = copy.deepcopy(self.c.readings)
        self.c.security.available = False
        self.c.tick()
        self.assertEqual(self.c.readings, before)
        self.assertEqual(self.c.security_status, "Security Module Offline")
        self.assertTrue(any(a.title == "Security module offline" for a in self.c.alerts))

    def test_processing_does_not_block_gui_heartbeat(self):
        self.login()
        beats = []
        heartbeat = QTimer()
        heartbeat.timeout.connect(lambda: beats.append(True))
        heartbeat.start(20)
        self.c.settings["refresh_seconds"] = .2
        self.c.start()
        QTest.qWait(800)
        heartbeat.stop()
        self.assertGreater(len(beats), 15)
        self.assertGreater(self.c.security.metrics.accepted_packets, 10)


class AccountTests(unittest.TestCase):
    def test_local_provisioning_command_hides_password_and_persists_only_hash(self):
        import io
        from pet_monitoring.security import provision_account
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "accounts.db"
            output = io.StringIO()
            with patch("sys.argv", ["provision_account", "--database", str(path), "--username", "group5"]), \
                 patch("getpass.getpass", side_effect=["new-test-password", "new-test-password"]), \
                 patch("sys.stdout", output):
                self.assertEqual(provision_account.main(), 0)
            self.assertNotIn("new-test-password", output.getvalue())
            db = DatabaseService(path)
            try:
                self.assertTrue(AuthenticationService(db).authenticate("group5", "new-test-password"))
                self.assertNotIn("new-test-password", json.dumps(db.get("authentication_users")))
            finally:
                db.close()

    def test_hash_provisioning_session_expiry_and_lockout(self):
        with tempfile.TemporaryDirectory() as folder:
            db = DatabaseService(Path(folder) / "accounts.db")
            auth = AuthenticationService(db)
            auth.set_credentials("group5", "new-test-password")
            self.assertNotIn("new-test-password", json.dumps(db.get("authentication_users")))
            second = AuthenticationService(db)
            self.assertTrue(second.authenticate("group5", "new-test-password"))
            self.assertTrue(second.is_authenticated)
            second.session.expires_at = time.monotonic() - 1
            self.assertFalse(second.is_authenticated)
            for _ in range(5):
                self.assertFalse(second.authenticate("group5", "wrong"))
            self.assertFalse(second.authenticate("group5", "new-test-password"))
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
