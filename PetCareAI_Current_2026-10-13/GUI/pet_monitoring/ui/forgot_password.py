"""Password recovery requires Telegram delivery AND a saved unused code."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit
from .widgets import label, button
from .otp_request import OtpRequest


class ForgotPasswordDialog(QDialog):
    def __init__(self, auth, parent=None):
        super().__init__(parent)
        self.auth = auth
        self.auth.cancel_password_reset()
        self.request = OtpRequest(self)
        self.request.finished.connect(self.delivery_finished)
        self.setWindowTitle("PetCare AI · Forgot password")
        self.setMinimumWidth(490)
        box = QVBoxLayout(self)
        box.addWidget(label("Reset administrator password", "title"))
        box.addWidget(label("Account: admin. Enter an unused recovery code saved during setup, then request a reset OTP on your registered Telegram chat.", "muted", True))
        box.addWidget(label("Saved recovery code", "eyebrow"))
        self.recovery_code = QLineEdit()
        self.recovery_code.setPlaceholderText("An unused code from your recovery file")
        self.recovery_code.setEchoMode(QLineEdit.EchoMode.Password)
        self.recovery_code.setMaxLength(64)
        box.addWidget(self.recovery_code)
        self.request_button = button("Request password reset OTP on Telegram", self.request_code, True)
        box.addWidget(self.request_button)
        self.otp = QLineEdit()
        self.otp.setPlaceholderText("6-digit Telegram password reset code")
        self.otp.setEchoMode(QLineEdit.EchoMode.Password)
        self.otp.setMaxLength(6)
        self.password = QLineEdit()
        self.password.setPlaceholderText(f"New password (at least {auth.MIN_PASSWORD_LENGTH} characters)")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMaxLength(auth.MAX_PASSWORD_LENGTH)
        self.confirmation = QLineEdit()
        self.confirmation.setPlaceholderText("Confirm new password")
        self.confirmation.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirmation.setMaxLength(auth.MAX_PASSWORD_LENGTH)
        self.reset_button = button("Reset password", self.reset_password, True)
        self.reset_widgets = [self.otp, self.password, self.confirmation, self.reset_button]
        for widget in self.reset_widgets:
            box.addWidget(widget)
            widget.hide()
        self.status = label("Both an unused recovery code and a Telegram OTP are required. Resetting consumes the recovery code and returns you to sign-in.", "muted", True)
        box.addWidget(self.status)
        box.addWidget(button("Cancel", self.reject))

    def request_code(self):
        if self.request.busy:
            return
        recovery_code = self.recovery_code.text()
        self.request_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.recovery_code.setEnabled(False)
        self.otp.clear()
        self.status.setText("Checking recovery code and sending a reset OTP to Telegram…")
        request_id = self.auth.prepare_password_reset_request()
        self.request.start(lambda: self.auth.request_password_reset_otp(recovery_code, request_id))

    def delivery_finished(self, generation, success, message):
        if generation != self.request.generation:
            return
        self.request.busy = False
        self.request_button.setEnabled(True)
        self.reset_button.setEnabled(True)
        self.recovery_code.setEnabled(True)
        self.status.setText(message)
        if success:
            for widget in self.reset_widgets:
                widget.show()
            self.request_button.setText("Resend password reset OTP")
            self.otp.setFocus()

    def reset_password(self):
        if self.request.busy:
            return
        success = self.auth.reset_password(self.otp.text(), self.password.text(), self.confirmation.text())
        self.otp.clear()
        self.password.clear()
        self.confirmation.clear()
        if success:
            self._clear()
            self.accept()
        else:
            self.status.setText(self.auth.last_error)

    def _clear(self):
        self.request.invalidate()
        self.auth.cancel_password_reset()
        for widget in (self.recovery_code, self.otp, self.password, self.confirmation):
            widget.clear()

    def reject(self):
        self._clear()
        super().reject()
