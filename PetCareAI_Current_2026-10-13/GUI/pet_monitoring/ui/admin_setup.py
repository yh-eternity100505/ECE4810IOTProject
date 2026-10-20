"""Explicit local-only enrollment; never launched by the normal login page."""
import os

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLineEdit,
                              QCheckBox, QFileDialog, QPlainTextEdit)
from .widgets import label, button
from .otp_request import OtpRequest
from pet_monitoring.services.authentication_service import STORAGE_ERRORS


class AdminSetupDialog(QDialog):
    def __init__(self, auth, parent=None):
        super().__init__(parent)
        self.auth = auth
        self.enrollment = None
        self.recovery_saved = False
        self.request = OtpRequest(self)
        self.request.finished.connect(self.delivery_finished)
        self.setWindowTitle("PetCare AI · Set up administrator")
        self.setMinimumWidth(540)
        self.box = QVBoxLayout(self)
        self.box.addWidget(label("Set up administrator", "title"))
        self.box.addWidget(label("One local administrator: admin. Choose a password, verify a code sent to your Telegram, then save recovery codes.", "muted", True))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMaxLength(auth.MAX_PASSWORD_LENGTH)
        self.password.setPlaceholderText(f"New password (at least {auth.MIN_PASSWORD_LENGTH} characters)")
        self.confirmation = QLineEdit()
        self.confirmation.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirmation.setMaxLength(auth.MAX_PASSWORD_LENGTH)
        self.confirmation.setPlaceholderText("Confirm password")
        self.box.addWidget(self.password)
        self.box.addWidget(self.confirmation)
        token, chat_id = auth.telegram_defaults()
        self.telegram_hint = label("Telegram delivery: enter your bot token and your private chat ID. Open the bot in Telegram and press Start first. Saved alert details are filled in when available.", "muted", True)
        self.bot_token = QLineEdit(token)
        self.bot_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.bot_token.setPlaceholderText("Telegram bot token")
        self.chat_id = QLineEdit(chat_id)
        self.chat_id.setPlaceholderText("Your private Telegram chat ID")
        self.box.addWidget(self.telegram_hint)
        self.box.addWidget(self.bot_token)
        self.box.addWidget(self.chat_id)
        self.continue_button = button("Continue to Telegram verification", self.enrol, True)
        self.box.addWidget(self.continue_button)
        self.request_button = button("Request OTP on Telegram", self.request_code, True)
        self.code = QLineEdit()
        self.code.setPlaceholderText("6-digit code sent to Telegram")
        self.code.setMaxLength(6)
        self.code.setEchoMode(QLineEdit.EchoMode.Password)
        self.verify_button = button("Verify Telegram OTP", self.verify, True)
        self.codes = QPlainTextEdit()
        self.codes.setReadOnly(True)
        self.codes.setMaximumHeight(180)
        self.save_button = button("Save recovery codes…", self.save_recovery)
        self.saved = QCheckBox("I have securely saved my recovery codes.")
        self.saved.setEnabled(False)
        self.finish_button = button("Complete administrator setup", self.finish, True)
        self.finish_button.setEnabled(False)
        self.saved.toggled.connect(lambda value: self.finish_button.setEnabled(value and self.recovery_saved))
        self.auth_widgets = [self.request_button, self.code, self.verify_button]
        self.recovery_widgets = [self.codes, self.save_button, self.saved, self.finish_button]
        for widget in self.auth_widgets + self.recovery_widgets:
            self.box.addWidget(widget)
            widget.hide()
        self.status = label("", "muted", True)
        self.box.addWidget(self.status)
        self.box.addWidget(button("Cancel", self.reject))

    def enrol(self):
        try:
            self.enrollment = self.auth.begin_setup(self.password.text(), self.confirmation.text(),
                telegram_token=self.bot_token.text(), telegram_chat_id=self.chat_id.text())
        except STORAGE_ERRORS as error:
            self.status.setText(str(error))
            return
        finally:
            self.password.clear()
            self.confirmation.clear()
        self.bot_token.clear()
        for widget in (self.password, self.confirmation, self.continue_button, self.bot_token, self.chat_id, self.telegram_hint):
            widget.hide()
        for widget in self.auth_widgets:
            widget.show()
        self.status.setText("Request a code on Telegram, then enter it here. Codes expire in 2 minutes.")
        self.code.setFocus()

    def request_code(self):
        if self.request.busy:
            return
        self.request_button.setEnabled(False)
        self.verify_button.setEnabled(False)
        self.status.setText("Sending your code to Telegram…")
        self.request.start(self.auth.request_setup_otp)

    def delivery_finished(self, generation, success, message):
        if generation != self.request.generation:
            return
        self.request.busy = False
        self.request_button.setEnabled(True)
        self.verify_button.setEnabled(True)
        self.request_button.setText("Resend OTP on Telegram")
        self.status.setText(message)

    def verify(self):
        try:
            self.auth.verify_setup_otp(self.code.text())
        except ValueError as error:
            self.status.setText(str(error))
            return
        finally:
            self.code.clear()
        for widget in self.auth_widgets:
            widget.hide()
        self.request.invalidate()
        self.codes.setPlainText("\n".join(self.enrollment.recovery_codes))
        for widget in self.recovery_widgets:
            widget.show()
        self.status.setText("Save these eight recovery codes securely. Each replaces one Telegram OTP and still requires your password. They will not be shown again.")

    def save_recovery(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save recovery codes", "PetCare-admin-recovery.txt", "Text files (*.txt)")
        if not path:
            return
        try:
            # Do not overwrite an existing file or follow an existing symlink.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("PetCare AI administrator recovery codes\nAccount: admin\nKeep private. Each code can be used once, after your password.\n\n")
                handle.write("\n".join(self.enrollment.recovery_codes) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            self.status.setText("Could not save recovery codes. Choose a new filename in a writable, private location.")
            return
        self.recovery_saved = True
        self.saved.setEnabled(True)
        self.status.setText("Recovery codes saved. Confirm you have them, then complete setup.")

    def finish(self):
        try:
            self.auth.complete_setup(self.recovery_saved and self.saved.isChecked())
        except STORAGE_ERRORS as error:
            self.status.setText(str(error))
            self.finish_button.setEnabled(False)
            return
        self._clear()
        self.accept()

    def _clear(self):
        self.request.invalidate()
        self.auth.cancel_setup()
        self.enrollment = None
        self.password.clear()
        self.confirmation.clear()
        self.code.clear()
        self.bot_token.clear()
        self.codes.clear()

    def reject(self):
        self._clear()
        super().reject()


def run_setup(auth):
    from PySide6.QtWidgets import QMessageBox
    if auth.refresh() != "unconfigured":
        QMessageBox.warning(None, "Administrator setup disabled", auth.error or "Administrator setup is already complete.")
        return 1
    dialog = AdminSetupDialog(auth)
    return 0 if dialog.exec() == QDialog.DialogCode.Accepted else 1
