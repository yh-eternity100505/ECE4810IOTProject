"""Security boundaries, enrollment failures, persisted replay and UI MFA."""
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pyotp
from argon2 import Type, extract_parameters
from PySide6.QtWidgets import QApplication
from pet_monitoring.security.admin_store import AdminCredentialStore, CredentialStoreError
from pet_monitoring.services.authentication_service import AuthenticationService, DEFAULT_MARKER
from pet_monitoring.services.database_service import DatabaseService
from pet_monitoring.ui.admin_setup import AdminSetupDialog
from pet_monitoring.ui.login_page import LoginPage
from tests.auth_support import MemoryStore, PASSWORD

APP = QApplication.instance() or QApplication([])
FIXED_TIME = 1800000000


class AdminAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.marker = Path(self.tmp.name) / "setup.lock"
        self.store = MemoryStore()
        self.clock = patch("pet_monitoring.services.authentication_service.time.time", return_value=FIXED_TIME)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.auth = self.service()

    def service(self):
        return AuthenticationService(store=self.store, marker_path=self.marker)

    def enrol(self):
        enrollment = self.auth.begin_setup(PASSWORD, PASSWORD)
        self.secret = enrollment.secret
        self.codes = list(enrollment.recovery_codes)
        self.auth.verify_setup_otp(pyotp.TOTP(self.secret).at(FIXED_TIME))
        self.auth.complete_setup(True)
        self.assertFalse(self.auth.is_authenticated)

    def totp(self, offset=30):
        return pyotp.TOTP(self.secret).at(FIXED_TIME + offset)

    def test_no_demo_password_no_password_only_session(self):
        self.assertEqual(self.auth.state, "unconfigured")
        self.assertFalse(self.auth.authenticate("admin", "pet123"))
        self.enrol()
        self.assertFalse(self.auth.authenticate("admin", "pet123"))
        self.assertFalse(self.auth.verify_second_factor(self.totp()))
        self.assertTrue(self.auth.authenticate("admin", PASSWORD))
        self.assertFalse(self.auth.is_authenticated)
        self.assertTrue(self.auth.verify_second_factor(self.totp()))
        self.assertEqual(self.auth.session.username, "admin")
        self.auth.logout()
        self.assertFalse(self.auth.is_authenticated)

    def test_argon2id_and_secret_only_in_os_store(self):
        self.enrol()
        record = json.loads(self.store.value)
        self.assertEqual(extract_parameters(record["password_hash"]).type, Type.ID)
        self.assertTrue(self.auth.hasher.verify(record["password_hash"], PASSWORD))
        self.assertNotIn(PASSWORD, self.store.value)
        self.assertLess(len(self.store.value.encode("utf-16-le")), 2560)
        for code in self.codes:
            self.assertNotIn(code, self.store.value)
        self.assertNotIn(self.secret, self.marker.read_text())
        self.assertNotIn("password", self.marker.read_text())

    def test_setup_requires_confirmation_otp_and_saved_recovery(self):
        for password, confirmation in (("short", "short"), (PASSWORD, "different")):
            with self.assertRaises(ValueError):
                self.auth.begin_setup(password, confirmation)
        enrollment = self.auth.begin_setup(PASSWORD, PASSWORD)
        with self.assertRaises(ValueError):
            self.auth.complete_setup(True)
        with self.assertRaises(ValueError):
            self.auth.verify_setup_otp("invalid")
        self.auth.verify_setup_otp(pyotp.TOTP(enrollment.secret).at(FIXED_TIME))
        with self.assertRaises(ValueError):
            self.auth.complete_setup(False)
        self.assertFalse(self.marker.exists())
        self.auth.complete_setup(True)
        with self.assertRaises(ValueError):
            self.service().begin_setup(PASSWORD, PASSWORD)

    def test_password_minimum_is_eight_characters(self):
        for length in (0, 6, 7):
            with self.subTest(length=length):
                password = "x" * length
                with self.assertRaisesRegex(ValueError, "8"):
                    self.auth.begin_setup(password, password)
        password = "x" * 8
        enrollment = self.auth.begin_setup(password, password)
        self.assertTrue(self.auth.hasher.verify(enrollment.password_hash, password))
        self.auth.cancel_setup()

    def test_single_account_and_setup_cannot_replace_it(self):
        self.enrol()
        before = self.store.value
        with self.assertRaises(ValueError):
            self.auth.begin_setup(PASSWORD + "changed", PASSWORD + "changed")
        self.assertEqual(self.store.value, before)
        self.assertFalse(self.auth.authenticate("another-admin", PASSWORD))
        self.assertFalse(hasattr(self.auth, "set_credentials"))

    def test_setup_cancel_leaves_no_credentials(self):
        self.auth.begin_setup(PASSWORD, PASSWORD)
        self.auth.cancel_setup()
        self.assertIsNone(self.store.value)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.service().state, "unconfigured")

    def test_missing_and_corrupt_credentials_never_reopen_setup(self):
        self.enrol()
        original = self.store.value
        bad_records = [None, "", "{", "[]"]
        for field, value in (("secret", "invalid"), ("recovery", [None]),
                             ("username", "other"), ("password_hash", "$argon2i$bad"),
                             ("last_step", "0"), ("locked_until", float("nan"))):
            record = json.loads(original)
            record[field] = value
            bad_records.append(json.dumps(record))
        for value in bad_records:
            with self.subTest(value=value):
                self.store.value = value
                auth = self.service()
                self.assertEqual(auth.state, "blocked")
                self.assertFalse(auth.authenticate("admin", PASSWORD))
                with self.assertRaises(Exception):
                    auth.begin_setup(PASSWORD, PASSWORD)
                self.assertIsNone(auth.session)

    def test_missing_marker_does_not_enable_setup(self):
        self.enrol()
        self.marker.unlink()
        self.assertEqual(self.service().state, "blocked")
        with self.assertRaises(OSError):
            self.service().begin_setup(PASSWORD, PASSWORD)

    def test_missing_native_store_does_not_offer_setup_or_fallback(self):
        store = Mock()
        store.read.side_effect = CredentialStoreError("Unavailable")
        auth = AuthenticationService(store=store, marker_path=self.marker)
        self.assertEqual(auth.state, "blocked")
        with self.assertRaises(CredentialStoreError):
            auth.begin_setup(PASSWORD, PASSWORD)
        self.assertFalse(auth.authenticate("admin", PASSWORD))

    def test_failed_setup_commit_leaves_setup_permanently_locked(self):
        enrollment = self.auth.begin_setup(PASSWORD, PASSWORD)
        self.auth.verify_setup_otp(pyotp.TOTP(enrollment.secret).at(FIXED_TIME))
        with patch.object(self.store, "write", side_effect=CredentialStoreError("Write failed")):
            with self.assertRaises(CredentialStoreError):
                self.auth.complete_setup(True)
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.service().state, "blocked")

    def test_setup_race_only_first_enrollment_wins(self):
        other = self.service()
        enrollment = other.begin_setup(PASSWORD, PASSWORD)
        other.verify_setup_otp(pyotp.TOTP(enrollment.secret).at(FIXED_TIME))
        self.enrol()
        before = self.store.value
        with self.assertRaises(ValueError):
            other.complete_setup(True)
        self.assertEqual(self.store.value, before)

    def test_totp_replay_is_rejected_after_restart(self):
        self.enrol()
        self.assertTrue(self.auth.authenticate("admin", PASSWORD))
        self.assertFalse(self.auth.verify_second_factor(self.totp(0)))  # enrollment code consumed
        self.assertTrue(self.auth.verify_second_factor(self.totp()))
        other = self.service()
        self.assertTrue(other.authenticate("admin", PASSWORD))
        self.assertFalse(other.verify_second_factor(self.totp()))
        self.assertFalse(other.is_authenticated)

    def test_recovery_requires_password_and_is_single_use_after_restart(self):
        self.enrol()
        self.assertFalse(self.auth.verify_second_factor(self.codes[0], recovery=True))
        self.assertTrue(self.auth.authenticate("admin", PASSWORD))
        self.assertTrue(self.auth.verify_second_factor(self.codes[0], recovery=True))
        other = self.service()
        self.assertTrue(other.authenticate("admin", PASSWORD))
        self.assertFalse(other.verify_second_factor(self.codes[0], recovery=True))
        self.assertTrue(other.verify_second_factor(self.codes[1], recovery=True))

    def test_storage_failure_never_grants_session(self):
        self.enrol()
        self.assertTrue(self.auth.authenticate("admin", PASSWORD))
        with patch.object(self.store, "write", side_effect=CredentialStoreError("Write failed")):
            self.assertFalse(self.auth.verify_second_factor(self.totp()))
        self.assertFalse(self.auth.is_authenticated)
        self.assertEqual(self.auth.state, "blocked")

    def test_wrong_otp_and_password_share_persisted_lockout(self):
        self.enrol()
        for _ in range(4):
            self.assertFalse(self.auth.authenticate("admin", "wrong"))
        other = self.service()
        self.assertTrue(other.authenticate("admin", PASSWORD))
        self.assertFalse(other.verify_second_factor("invalid"))
        self.assertFalse(self.service().authenticate("admin", PASSWORD))
        with patch("pet_monitoring.services.authentication_service.time.time", return_value=FIXED_TIME + 31):
            self.assertTrue(other.authenticate("admin", PASSWORD))
            self.assertTrue(other.verify_second_factor(self.totp()))

    def test_second_factor_challenge_expires_and_logout_cancels_it(self):
        self.enrol()
        self.auth.authenticate("admin", PASSWORD)
        self.auth._pending = (self.auth._pending[0], time.monotonic() - 1)
        self.assertFalse(self.auth.verify_second_factor(self.totp()))
        self.auth.authenticate("admin", PASSWORD)
        self.auth.logout()
        self.assertFalse(self.auth.verify_second_factor(self.totp()))

    def test_monitoring_database_does_not_control_admin_identity(self):
        self.enrol()
        db = DatabaseService(Path(self.tmp.name) / "different.db")
        try:
            db.set("authentication_users", {"admin": {"password": "pet123"}})
            auth = AuthenticationService(db, store=self.store, marker_path=self.marker)
            self.assertEqual(auth.state, "ready")
            self.assertFalse(auth.authenticate("admin", "pet123"))
            self.assertTrue(auth.authenticate("admin", PASSWORD))
            self.assertFalse(auth.is_authenticated)
            self.assertEqual(AuthenticationService(db, store=MemoryStore()).marker, DEFAULT_MARKER)
        finally:
            db.close()

    def test_login_card_requires_both_steps_and_clears_sensitive_fields(self):
        self.enrol()
        db = DatabaseService(Path(self.tmp.name) / "ui.db")
        page = LoginPage(self.auth, db)
        try:
            page.show()
            page.password.setText(PASSWORD)
            page.login_button.click()
            self.assertEqual(page.password.text(), "")
            self.assertFalse(self.auth.is_authenticated)
            self.assertTrue(page.otp.isVisible())
            page.otp.setText(self.totp())
            page.verify_button.click()
            self.assertTrue(self.auth.is_authenticated)
            self.assertEqual(page.otp.text(), "")
            self.assertFalse(page.otp.isVisible())
            self.assertTrue(page.username.isReadOnly())
        finally:
            page.close()
            db.close()

    def test_setup_wizard_telegram_recovery_export_and_finish(self):
        delivered = []
        self.auth._telegram_sender = lambda token, chat, message: (delivered.append(message) is None, "Sent")
        dialog = AdminSetupDialog(self.auth)
        dialog.password.setText(PASSWORD)
        dialog.confirmation.setText(PASSWORD)
        dialog.bot_token.setText("123456789:" + "A" * 35)
        dialog.chat_id.setText("123456789")
        dialog.enrol()
        self.assertIsNotNone(dialog.enrollment)
        self.assertFalse(hasattr(dialog, "qr"))
        self.assertEqual(dialog.password.text(), "")
        self.assertEqual(dialog.bot_token.text(), "")
        self.assertTrue(self.auth.request_setup_otp()[0])
        dialog.code.setText(re.search(r"code: ([0-9]{6})", delivered[-1]).group(1))
        dialog.verify()
        self.assertFalse(dialog.finish_button.isEnabled())
        target = Path(self.tmp.name) / "recovery.txt"
        with patch("pet_monitoring.ui.admin_setup.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            dialog.save_recovery()
        self.assertIn(dialog.enrollment.recovery_codes[0], target.read_text())
        dialog.saved.setChecked(True)
        self.assertTrue(dialog.finish_button.isEnabled())
        dialog.finish()
        self.assertEqual(self.auth.state, "ready")
        self.assertIsNone(dialog.enrollment)
        self.assertEqual(dialog.codes.toPlainText(), "")
        self.assertFalse(self.auth.is_authenticated)

    def test_no_secrets_in_authentication_logs(self):
        with self.assertLogs(level="INFO") as captured:
            self.enrol()
            self.auth.authenticate("admin", PASSWORD)
            self.auth.verify_second_factor(self.codes[0], recovery=True)
        logs = " ".join(captured.output)
        for value in (PASSWORD, self.secret, self.codes[0]):
            self.assertNotIn(value, logs)

    @unittest.skipUnless(os.name == "nt", "Native Windows credential API")
    def test_native_windows_store_uses_single_target_and_unicode_write(self):
        from win32ctypes.pywin32 import win32cred
        store = AdminCredentialStore()
        payload = '{"nonsecret":"test"}'
        credential = {"UserName": "admin", "CredentialBlob": payload.encode("utf-16-le")}
        with patch("pet_monitoring.security.admin_store.sys.platform", "win32"), \
             patch.object(win32cred, "CredWrite") as write, \
             patch.object(win32cred, "CredRead", return_value=credential) as read:
            store.write(payload)
            self.assertEqual(write.call_count, 1)
            self.assertEqual(write.call_args.args[0]["CredentialBlob"], payload)
            self.assertEqual(write.call_args.args[0]["TargetName"], store.SERVICE)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(store.read(), payload)

    def test_expired_enrollment_cannot_complete(self):
        enrollment = self.auth.begin_setup(PASSWORD, PASSWORD)
        self.auth.verify_setup_otp(pyotp.TOTP(enrollment.secret).at(FIXED_TIME))
        enrollment.expires_at = time.monotonic() - 1
        with self.assertRaises(ValueError):
            self.auth.complete_setup(True)
        self.assertIsNone(self.store.value)
        self.assertFalse(self.marker.exists())

    def test_shared_mutex_rejects_simultaneous_credential_update(self):
        self.enrol()
        other = self.service()
        with self.auth._exclusive():
            self.assertFalse(other.authenticate("admin", PASSWORD))
        self.assertFalse(other.is_authenticated)
        self.assertTrue(other.authenticate("admin", PASSWORD))


if __name__ == "__main__":
    unittest.main()
