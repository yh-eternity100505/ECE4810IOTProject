"""Telegram admin OTP uses real delivery only, after a verified password."""
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from pet_monitoring.services.authentication_service import AuthenticationService
from pet_monitoring.security.admin_store import CredentialStoreError
from pet_monitoring.services.database_service import DatabaseService
from pet_monitoring.ui.admin_setup import AdminSetupDialog
from pet_monitoring.ui.login_page import LoginPage
from tests.auth_support import MemoryStore, PASSWORD

APP = QApplication.instance() or QApplication([])
TOKEN = "123456789:" + "A" * 35
CHAT = "123456789"


def wait_for(predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        APP.processEvents()
        QTest.qWait(10)
    return predicate()


class TelegramAdminTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.marker = Path(self.folder.name) / "setup.lock"
        self.store = MemoryStore()
        self.delivered = []
        self.sender = Mock(side_effect=self.deliver)
        self.auth = self.service()

    def deliver(self, token, chat, text):
        self.assertEqual(token, TOKEN)
        self.assertEqual(chat, CHAT)
        self.delivered.append(text)
        return True, "Sent"

    def service(self):
        return AuthenticationService(store=self.store, marker_path=self.marker, telegram_sender=self.sender)

    def code(self):
        return re.search(r"code: ([0-9]{6})", self.delivered[-1]).group(1)

    def enrol(self):
        enrollment = self.auth.begin_setup(PASSWORD, PASSWORD, telegram_token=TOKEN, telegram_chat_id=CHAT)
        self.codes = list(enrollment.recovery_codes)
        self.assertEqual(enrollment.secret, "")
        self.assertTrue(self.auth.request_setup_otp()[0])
        self.auth.verify_setup_otp(self.code())
        self.auth.complete_setup(True)

    def start_login(self):
        self.assertTrue(self.auth.authenticate("admin", PASSWORD))
        self.assertFalse(self.auth.is_authenticated)
        self.assertTrue(self.auth.request_login_otp()[0])

    def test_password_then_requested_telegram_code_is_required(self):
        self.enrol()
        self.assertFalse(self.auth.request_login_otp()[0])
        self.assertFalse(self.auth.authenticate("admin", "wrong"))
        self.assertFalse(self.auth.request_login_otp()[0])
        self.start_login()
        code = self.code()
        self.assertTrue(self.auth.verify_second_factor(code))
        self.assertTrue(self.auth.is_authenticated)
        self.auth.logout()
        self.assertFalse(self.auth.verify_second_factor(code))

    def test_failed_setup_delivery_and_unrequested_code_cannot_enrol(self):
        self.auth.begin_setup(PASSWORD, PASSWORD, telegram_token=TOKEN, telegram_chat_id=CHAT)
        with self.assertRaises(ValueError):
            self.auth.verify_setup_otp("123456")
        self.sender.side_effect = lambda *args: (False, "Not configured")
        self.assertFalse(self.auth.request_setup_otp()[0])
        self.assertFalse(self.auth._setup_otp.delivered)
        with self.assertRaises(ValueError):
            self.auth.verify_setup_otp("123456")
        with self.assertRaises(ValueError):
            self.auth.complete_setup(True)
        self.assertFalse(self.marker.exists())

    def test_login_delivery_failure_never_grants_access_even_with_code(self):
        self.enrol()
        self.auth.authenticate("admin", PASSWORD)
        def failed_delivery(token, chat, text):
            self.delivered.append(text)
            return False, "Failed"
        self.sender.side_effect = failed_delivery
        self.assertFalse(self.auth.request_login_otp()[0])
        self.assertFalse(self.auth.verify_second_factor(self.code()))
        self.assertFalse(self.auth.is_authenticated)

    def test_otp_expires_and_resend_invalidates_older_challenge(self):
        self.enrol()
        self.start_login()
        old = self.auth._login_otp
        old_code = self.code()
        old.expires_at = time.monotonic() - 1
        self.assertFalse(self.auth.verify_second_factor(old_code))
        with patch("pet_monitoring.services.authentication_service.time.time", return_value=time.time() + 31):
            self.assertTrue(self.auth.request_login_otp()[0])
        self.assertNotEqual(self.auth._login_otp.identifier, old.identifier)
        if old_code != self.code():
            self.assertFalse(self.auth.verify_second_factor(old_code))
        self.assertTrue(self.auth.verify_second_factor(self.code()))

    def test_login_resend_cooldown_survives_restart(self):
        self.enrol()
        self.start_login()
        self.assertFalse(self.auth.request_login_otp()[0])
        other = self.service()
        self.assertTrue(other.authenticate("admin", PASSWORD))
        self.assertFalse(other.request_login_otp()[0])
        self.assertEqual(self.sender.call_count, 2)  # setup + first login

    def test_another_process_request_invalidates_previous_code(self):
        self.enrol()
        self.start_login()
        first = self.code()
        other = self.service()
        self.assertTrue(other.authenticate("admin", PASSWORD))
        with patch("pet_monitoring.services.authentication_service.time.time", return_value=time.time() + 31):
            self.assertTrue(other.request_login_otp()[0])
        self.assertFalse(self.auth.verify_second_factor(first))
        self.assertTrue(other.verify_second_factor(self.code()))

    def test_recovery_code_works_without_telegram_and_is_consumed(self):
        self.enrol()
        other = self.service()
        self.assertTrue(other.authenticate("admin", PASSWORD))
        self.assertTrue(other.verify_second_factor(self.codes[0], recovery=True))
        again = self.service()
        self.assertTrue(again.authenticate("admin", PASSWORD))
        self.assertFalse(again.verify_second_factor(self.codes[0], recovery=True))

    def test_failed_attempts_not_reset_by_resending_or_password_step(self):
        self.enrol()
        self.start_login()
        for _ in range(4):
            self.assertFalse(self.auth.verify_second_factor("invalid"))
        self.assertTrue(self.auth.authenticate("admin", PASSWORD))
        self.assertFalse(self.auth.verify_second_factor("invalid"))
        self.assertFalse(self.service().authenticate("admin", PASSWORD))

    def test_cancelling_login_during_delivery_cannot_restore_challenge(self):
        self.enrol()
        self.auth.authenticate("admin", PASSWORD)
        def delayed(token, chat, text):
            self.auth.cancel_login()
            return True, "Sent"
        self.sender.side_effect = delayed
        self.assertFalse(self.auth.request_login_otp()[0])
        self.assertIsNone(self.auth._login_otp)
        self.assertFalse(self.auth.is_authenticated)

    def test_cancelling_setup_during_delivery_cannot_restore_enrollment(self):
        self.auth.begin_setup(PASSWORD, PASSWORD, telegram_token=TOKEN, telegram_chat_id=CHAT)
        self.sender.side_effect = lambda *args: (self.auth.cancel_setup() is None, "Sent")
        self.assertFalse(self.auth.request_setup_otp()[0])
        self.assertIsNone(self.auth._enrollment)
        self.assertIsNone(self.auth._setup_otp)

    def test_fixed_private_destination_and_os_protected_bot_token(self):
        for invalid in ("-123", "@group", "", "0"):
            with self.assertRaises(ValueError):
                self.auth.begin_setup(PASSWORD, PASSWORD, telegram_token=TOKEN, telegram_chat_id=invalid)
        self.enrol()
        record = json.loads(self.store.value)
        self.assertEqual(record["v"], 2)
        self.assertEqual(record["telegram_chat_id"], CHAT)
        self.assertNotIn("secret", record)
        self.assertLess(len(self.store.value.encode("utf-16-le")), 2560)
        self.assertNotIn(TOKEN, self.marker.read_text())
        self.assertNotIn(PASSWORD, self.store.value)
        with self.assertRaises(ValueError):
            self.auth.begin_setup(PASSWORD, PASSWORD, telegram_token=TOKEN, telegram_chat_id="987654321")

    def test_missing_telegram_credentials_blocks_instead_of_reopening_setup(self):
        self.enrol()
        record = json.loads(self.store.value)
        del record["telegram_token"]
        self.store.value = json.dumps(record)
        other = self.service()
        self.assertEqual(other.state, "blocked")
        self.assertFalse(other.authenticate("admin", PASSWORD))
        with self.assertRaises(KeyError):
            other.begin_setup(PASSWORD, PASSWORD, telegram_token=TOKEN, telegram_chat_id=CHAT)

    def test_native_write_failure_cannot_issue_otp_or_session(self):
        self.enrol()
        self.auth.authenticate("admin", PASSWORD)
        with patch.object(self.store, "write", side_effect=CredentialStoreError("Failed")):
            self.assertFalse(self.auth.request_login_otp()[0])
        self.assertEqual(self.sender.call_count, 1)
        self.assertFalse(self.auth.is_authenticated)

    def test_default_sender_always_uses_real_telegram_not_alert_mock(self):
        with patch("pet_monitoring.services.telegram_service.TelegramService") as service:
            service.return_value.send_message.return_value = (True, "Sent")
            self.assertTrue(AuthenticationService._send_telegram(TOKEN, CHAT, "private code")[0])
            service.assert_called_once_with("Real", TOKEN, CHAT, True)

    def test_no_codes_or_tokens_in_auth_logs(self):
        with self.assertLogs(level="INFO") as captured:
            self.enrol()
            self.start_login()
            self.auth.verify_second_factor(self.code())
        output = " ".join(captured.output)
        for secret in (TOKEN, PASSWORD, self.code()):
            self.assertNotIn(secret, output)

    def test_sign_in_automatically_requests_otp_in_background_and_keeps_card(self):
        self.enrol()
        db = DatabaseService(Path(self.folder.name) / "ui.db")
        page = LoginPage(self.auth, db)
        try:
            page.show()
            page.password.setText(PASSWORD)
            page.login_button.click()
            self.assertTrue(page.request_button.isVisible())
            self.assertFalse(self.auth.is_authenticated)
            self.assertTrue(wait_for(lambda: not page.request.busy))
            self.assertEqual(self.sender.call_count, 2)  # setup + automatic login OTP
            self.assertIn("sent to Telegram", page.status.text())
            page.otp.setText(self.code())
            page.verify_button.click()
            self.assertTrue(self.auth.is_authenticated)
        finally:
            page.close()
            db.close()

    def test_restart_loads_saved_telegram_details_and_sign_in_requests_otp(self):
        self.enrol()
        restarted = self.service()
        self.assertEqual(restarted.state, "ready")
        self.assertEqual(restarted.factor_mode, "telegram")
        db = DatabaseService(Path(self.folder.name) / "restart-ui.db")
        page = LoginPage(restarted, db)
        try:
            page.password.setText(PASSWORD)
            page.login_button.click()
            self.assertTrue(wait_for(lambda: not page.request.busy))
            self.assertIn("sent to Telegram", page.status.text())
            self.assertEqual(self.sender.call_count, 2)
            self.assertFalse(restarted.is_authenticated)
            page.otp.setText(self.code())
            page.verify_button.click()
            self.assertTrue(restarted.is_authenticated)
        finally:
            page.close()
            db.close()

    def test_wrong_password_does_not_automatically_send_otp(self):
        self.enrol()
        db = DatabaseService(Path(self.folder.name) / "wrong-ui.db")
        page = LoginPage(self.auth, db)
        try:
            page.password.setText("incorrect-password")
            page.login_button.click()
            self.assertEqual(self.sender.call_count, 1)  # setup only
            self.assertFalse(page.request.busy)
            self.assertFalse(self.auth.is_authenticated)
        finally:
            page.close()
            db.close()

    def test_setup_has_request_button_without_qr_and_cancels_safely(self):
        dialog = AdminSetupDialog(self.auth)
        dialog.password.setText(PASSWORD)
        dialog.confirmation.setText(PASSWORD)
        dialog.bot_token.setText(TOKEN)
        dialog.chat_id.setText(CHAT)
        dialog.enrol()
        self.assertFalse(hasattr(dialog, "qr"))
        dialog.request_button.click()
        self.assertTrue(wait_for(lambda: not dialog.request.busy))
        self.assertIn("sent to Telegram", dialog.status.text())
        dialog.reject()
        self.assertIsNone(self.auth._enrollment)
        self.assertFalse(self.marker.exists())
