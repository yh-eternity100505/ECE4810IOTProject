"""Password recovery must not become a password, OTP, or setup bypass."""
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtTest import QTest
from pet_monitoring.security.admin_store import CredentialStoreError
from pet_monitoring.services.authentication_service import AuthenticationService
from pet_monitoring.services.database_service import DatabaseService
from pet_monitoring.ui.forgot_password import ForgotPasswordDialog
from pet_monitoring.ui.login_page import LoginPage
from tests.auth_support import MemoryStore, PASSWORD
from tests.test_telegram_admin import TOKEN, CHAT, wait_for

APP = QApplication.instance() or QApplication([])
NEW_PASSWORD = "new-test-password-456"


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.marker = Path(self.temp.name) / "setup.lock"
        self.store = MemoryStore()
        self.messages = []
        self.sender = Mock(side_effect=self.deliver)
        self.auth = self.service()
        enrollment = self.auth.begin_setup(PASSWORD, PASSWORD, telegram_token=TOKEN, telegram_chat_id=CHAT)
        self.codes = list(enrollment.recovery_codes)
        self.auth.request_setup_otp()
        self.auth.verify_setup_otp(self.code())
        self.auth.complete_setup(True)
        self.original_record = json.loads(self.store.value)
        self.original_marker = self.marker.read_bytes()

    def service(self):
        return AuthenticationService(store=self.store, marker_path=self.marker, telegram_sender=self.sender)

    def deliver(self, token, chat, text):
        self.assertEqual(token, TOKEN)
        self.assertEqual(chat, CHAT)
        self.messages.append(text)
        return True, "Sent"

    def code(self):
        return re.search(r"code: ([0-9]{6})", self.messages[-1]).group(1)

    def request(self, auth=None, recovery=None):
        auth = auth or self.auth
        success, message = auth.request_password_reset_otp(recovery or self.codes[0])
        self.assertTrue(success, message)
        return self.code()

    def test_reset_needs_both_factors_and_never_logs_in(self):
        self.assertFalse(self.auth.reset_password("123456", NEW_PASSWORD, NEW_PASSWORD))
        otp = self.request()
        self.assertTrue(self.auth.reset_password(otp, NEW_PASSWORD, NEW_PASSWORD))
        self.assertFalse(self.auth.is_authenticated)
        self.assertFalse(self.auth.authenticate("admin", PASSWORD))
        self.assertTrue(self.auth.authenticate("admin", NEW_PASSWORD))
        self.assertFalse(self.auth.is_authenticated)
        self.assertTrue(self.auth.verify_second_factor(self.codes[1], recovery=True))

    def test_same_account_marker_and_telegram_preserved_recovery_consumed(self):
        otp = self.request()
        self.auth.reset_password(otp, NEW_PASSWORD, NEW_PASSWORD)
        record = json.loads(self.store.value)
        for key in ("username", "id", "v", "telegram_token", "telegram_chat_id"):
            self.assertEqual(record[key], self.original_record[key])
        self.assertEqual(self.marker.read_bytes(), self.original_marker)
        self.assertNotIn(self.auth._recovery_hash(self.codes[0]), record["recovery"])
        self.assertEqual(len(record["recovery"]), 7)
        with self.assertRaises(ValueError):
            self.auth.begin_setup(NEW_PASSWORD, NEW_PASSWORD, telegram_token=TOKEN, telegram_chat_id=CHAT)
        self.assertNotIn(NEW_PASSWORD, self.store.value)
        self.assertTrue(record["password_hash"].startswith("$argon2id$"))

    def test_incorrect_recovery_code_cannot_request_reset_message(self):
        calls = self.sender.call_count
        self.assertFalse(self.auth.request_password_reset_otp("invalid")[0])
        self.assertEqual(self.sender.call_count, calls)
        self.assertIsNone(self.auth._reset_otp)
        self.assertEqual(json.loads(self.store.value)["password_hash"], self.original_record["password_hash"])

    def test_used_recovery_code_cannot_request_reset(self):
        self.auth.authenticate("admin", PASSWORD)
        self.auth.verify_second_factor(self.codes[0], recovery=True)
        self.assertFalse(self.auth.request_password_reset_otp(self.codes[0])[0])

    def test_failed_delivery_cannot_change_password_even_with_received_digits(self):
        def failed(token, chat, text):
            self.messages.append(text)
            return False, "Failed"
        self.sender.side_effect = failed
        self.assertFalse(self.auth.request_password_reset_otp(self.codes[0])[0])
        self.assertFalse(self.auth.reset_password(self.code(), NEW_PASSWORD, NEW_PASSWORD))
        self.assertEqual(json.loads(self.store.value)["password_hash"], self.original_record["password_hash"])
        self.assertEqual(json.loads(self.store.value)["recovery"], self.original_record["recovery"])

    def test_expired_reset_code_cannot_change_password(self):
        otp = self.request()
        self.auth._reset_otp.expires_at = time.monotonic() - 1
        self.assertFalse(self.auth.reset_password(otp, NEW_PASSWORD, NEW_PASSWORD))
        self.assertEqual(json.loads(self.store.value)["password_hash"], self.original_record["password_hash"])

    def test_reset_code_single_use(self):
        otp = self.request()
        self.assertTrue(self.auth.reset_password(otp, NEW_PASSWORD, NEW_PASSWORD))
        self.assertFalse(self.auth.reset_password(otp, "another-password", "another-password"))
        self.assertFalse(self.service().request_password_reset_otp(self.codes[0])[0])

    def test_password_length_and_confirmation_do_not_consume_proofs(self):
        otp = self.request()
        self.assertFalse(self.auth.reset_password(otp, "1234567", "1234567"))
        self.assertFalse(self.auth.reset_password(otp, NEW_PASSWORD, "different"))
        self.assertIn(self.auth._recovery_hash(self.codes[0]), json.loads(self.store.value)["recovery"])
        self.assertTrue(self.auth.reset_password(otp, "12345678", "12345678"))

    def test_shared_persisted_attempt_limits_and_resend_cooldown(self):
        otp = self.request()
        self.assertFalse(self.auth.request_password_reset_otp(self.codes[0])[0])
        for _ in range(5):
            self.assertFalse(self.auth.reset_password("invalid", NEW_PASSWORD, NEW_PASSWORD))
        self.assertFalse(self.auth.reset_password(otp, NEW_PASSWORD, NEW_PASSWORD))
        self.assertFalse(self.service().request_password_reset_otp(self.codes[0])[0])
        self.assertFalse(self.service().authenticate("admin", PASSWORD))

    def test_repeated_invalid_recovery_codes_are_throttled(self):
        for _ in range(5):
            self.assertFalse(self.auth.request_password_reset_otp("invalid")[0])
        self.assertFalse(self.service().request_password_reset_otp(self.codes[0])[0])
        self.assertEqual(self.sender.call_count, 1)  # initial setup only

    def test_cancel_during_delivery_cannot_restore_reset(self):
        def delayed(token, chat, text):
            self.messages.append(text)
            self.auth.cancel_password_reset()
            return True, "Sent"
        self.sender.side_effect = delayed
        self.assertFalse(self.auth.request_password_reset_otp(self.codes[0])[0])
        self.assertIsNone(self.auth._reset_otp)
        self.assertFalse(self.auth.reset_password(self.code(), NEW_PASSWORD, NEW_PASSWORD))

    def test_cancelling_before_worker_starts_does_not_send_or_restore_reset(self):
        request_id = self.auth.prepare_password_reset_request()
        self.auth.cancel_password_reset()
        self.assertFalse(self.auth.request_password_reset_otp(self.codes[0], request_id)[0])
        self.assertEqual(self.sender.call_count, 1)
        self.assertIsNone(self.auth._reset_context)

    def test_failed_storage_does_not_report_success_or_create_session(self):
        otp = self.request()
        with patch.object(self.store, "write", side_effect=CredentialStoreError("Unavailable")):
            self.assertFalse(self.auth.reset_password(otp, NEW_PASSWORD, NEW_PASSWORD))
        self.assertFalse(self.auth.is_authenticated)
        self.assertEqual(json.loads(self.store.value)["password_hash"], self.original_record["password_hash"])

    def test_missing_or_corrupt_vault_cannot_reset_or_reopen_setup(self):
        for invalid in (None, "not json"):
            self.store.value = invalid
            self.assertFalse(self.auth.request_password_reset_otp(self.codes[0])[0])
            self.assertEqual(self.auth.state, "blocked")
            self.assertEqual(self.marker.read_bytes(), self.original_marker)

    def test_reset_invalidates_another_instances_existing_session(self):
        other = self.service()
        self.assertTrue(other.authenticate("admin", PASSWORD))
        self.assertTrue(other.verify_second_factor(self.codes[1], recovery=True))
        self.assertTrue(other.is_authenticated)
        otp = self.request()
        self.assertTrue(self.auth.reset_password(otp, NEW_PASSWORD, NEW_PASSWORD))
        self.assertFalse(other.is_authenticated)
        self.assertFalse(other.authenticate("admin", PASSWORD))

    def test_reset_invalidates_password_already_verified_in_another_instance(self):
        other = self.service()
        self.assertTrue(other.authenticate("admin", PASSWORD))
        otp = self.request()
        self.auth.reset_password(otp, NEW_PASSWORD, NEW_PASSWORD)
        self.assertFalse(other.verify_second_factor(self.codes[1], recovery=True))
        self.assertFalse(other.is_authenticated)
        self.assertFalse(other.request_login_otp()[0])

    def test_reset_and_login_codes_cannot_be_interchanged(self):
        otp = self.request()
        other = self.service()
        other.authenticate("admin", PASSWORD)
        self.assertFalse(other.verify_second_factor(otp))
        self.assertFalse(other.is_authenticated)
        self.assertTrue(self.auth.reset_password(otp, NEW_PASSWORD, NEW_PASSWORD))

    def test_other_instance_consuming_recovery_code_invalidates_reset(self):
        otp = self.request()
        other = self.service()
        other.authenticate("admin", PASSWORD)
        other.verify_second_factor(self.codes[0], recovery=True)
        self.assertFalse(self.auth.reset_password(otp, NEW_PASSWORD, NEW_PASSWORD))

    def test_restart_cannot_complete_in_memory_reset_challenge(self):
        otp = self.request()
        self.assertFalse(self.service().reset_password(otp, NEW_PASSWORD, NEW_PASSWORD))

    def test_no_password_or_proof_in_logs(self):
        with self.assertLogs(level="INFO") as captured:
            otp = self.request()
            self.auth.reset_password(otp, NEW_PASSWORD, NEW_PASSWORD)
        for value in (PASSWORD, NEW_PASSWORD, TOKEN, self.codes[0], otp):
            self.assertNotIn(value, " ".join(captured.output))

    def test_reset_dialog_sends_asynchronously_and_clears_secrets(self):
        dialog = ForgotPasswordDialog(self.auth)
        dialog.show()
        dialog.recovery_code.setText(self.codes[0])
        dialog.request_button.click()
        self.assertTrue(wait_for(lambda: not dialog.request.busy))
        self.assertTrue(dialog.reset_button.isVisible())
        dialog.otp.setText(self.code())
        dialog.password.setText(NEW_PASSWORD)
        dialog.confirmation.setText(NEW_PASSWORD)
        dialog.reset_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertFalse(self.auth.is_authenticated)
        for field in (dialog.recovery_code, dialog.otp, dialog.password, dialog.confirmation):
            self.assertEqual(field.text(), "")

    def test_login_forgot_button_returns_to_password_without_authenticating(self):
        db = DatabaseService(Path(self.temp.name) / "ui.db")
        page = LoginPage(self.auth, db)
        try:
            page.password.setText("wrong-attempt")
            with patch("pet_monitoring.ui.forgot_password.ForgotPasswordDialog.exec", return_value=QDialog.DialogCode.Accepted):
                page.forgot_button.click()
            self.assertEqual(page.password.text(), "")
            self.assertIn("Password reset", page.status.text())
            self.assertFalse(self.auth.is_authenticated)
        finally:
            page.close()
            db.close()
