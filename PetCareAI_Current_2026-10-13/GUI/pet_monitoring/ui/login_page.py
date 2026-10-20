from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QCheckBox
from .widgets import Card, label, button, CorgiMascot
from .otp_request import OtpRequest


class LoginPage(QWidget):
    authenticated = Signal()
    movement_requested = Signal()

    def __init__(self, auth, database):
        super().__init__()
        self.auth, self.db = auth, database
        self.request = OtpRequest(self)
        self.request.finished.connect(self.delivery_finished)
        self.setObjectName("login")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(60, 50, 60, 50)
        story = QVBoxLayout()
        story.setSpacing(20)
        story.addStretch()
        story.addWidget(label("P E T C A R E   /   A I", "eyebrow"))
        self.corgi_mascot = CorgiMascot(112, 124)
        story.addWidget(self.corgi_mascot)
        story.addWidget(label("A closer look at\ntheir everyday wellbeing.", "title"))
        story.addWidget(label("Understand your pet's routines with discreet sensors,\nbehaviour insights, and timely notifications.", "muted", True))
        story.addWidget(label("01   Monitor daily routines\n\n02   Learn their normal baseline\n\n03   Notice meaningful changes", "sectionTitle"))
        story.addStretch()
        story.addWidget(label("ECE4810  ·  AIoT behavioural monitoring\nBehaviour insights, without cameras.", "muted", True))
        outer.addLayout(story, 3)
        card = Card()
        card.setFixedWidth(410)
        card.box.setContentsMargins(34, 34, 34, 34)
        card.box.addWidget(label("Welcome back", "title"))
        card.box.addWidget(label("Sign in to your monitoring workspace.", "muted"))
        card.box.addSpacing(20)
        self.username_label = label("Username", "eyebrow")
        card.box.addWidget(self.username_label)
        self.username = QLineEdit()
        self.username.setPlaceholderText("Enter your username")
        self.username.setText("admin")
        self.username.setReadOnly(True)
        card.box.addWidget(self.username)
        self.password_label = label("Password", "eyebrow")
        card.box.addWidget(self.password_label)
        password_row = QHBoxLayout()
        self.password = QLineEdit()
        self.password.setPlaceholderText("Enter your password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMaxLength(auth.MAX_PASSWORD_LENGTH)
        password_row.addWidget(self.password)
        self.show_button = button("Show", self.toggle_password)
        password_row.addWidget(self.show_button)
        card.box.addLayout(password_row)
        self.remember = QCheckBox("Remember username")
        self.remember.setChecked(bool(self.db.get("remembered_username", "")))
        card.box.addWidget(self.remember)
        self.login_button = button("Sign in  →", self.login, True)
        card.box.addWidget(self.login_button)
        self.password_widgets = [self.username_label, self.username, self.password_label,
                                 self.password, self.show_button, self.remember, self.login_button]
        self.forgot_button = button("Forgot password?", self.forgot_password)
        card.box.addWidget(self.forgot_button)
        self.password_widgets.append(self.forgot_button)
        self.otp_label = label("Telegram OTP", "eyebrow")
        self.request_button = button("Request OTP on Telegram", self.request_code, True)
        self.otp = QLineEdit()
        self.otp.setPlaceholderText("6-digit code sent to Telegram")
        self.otp.setMaxLength(64)
        self.otp.setEchoMode(QLineEdit.EchoMode.Password)
        self.recovery = QCheckBox("Use a recovery code instead")
        self.recovery.toggled.connect(self.change_factor)
        self.verify_button = button("Verify and sign in", self.verify, True)
        self.back_button = button("Back to password", self.reset)
        self.factor_widgets = [self.otp_label, self.request_button, self.otp, self.recovery, self.verify_button, self.back_button]
        for widget in self.factor_widgets:
            card.box.addWidget(widget)
            widget.hide()
        card.box.addWidget(button("Raspberry Pi movement access", self.movement_requested.emit))
        self.status = label(self.auth.error or "Administrator password and OTP required.", "muted", True)
        card.box.addWidget(self.status)
        card.box.addSpacing(8)
        card.box.addWidget(label("Monitor patterns. Stay informed.\nThis application does not provide veterinary diagnoses.", "muted", True))
        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignVCenter)
        self.password.returnPressed.connect(self.login)
        self.otp.returnPressed.connect(self.verify)
        self.username.returnPressed.connect(self.password.setFocus)

    def toggle_password(self):
        hidden = self.password.echoMode() == QLineEdit.EchoMode.Password
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self.show_button.setText("Hide" if hidden else "Show")

    def forgot_password(self):
        from PySide6.QtWidgets import QDialog
        from .forgot_password import ForgotPasswordDialog
        self.reset()
        self.auth.logout()
        dialog = ForgotPasswordDialog(self.auth, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.reset()
            self.status.setText("Password reset. Sign in with your new password, then verify your normal login OTP.")
            self.password.setFocus()

    def login(self):
        if self.auth.authenticate(self.username.text(), self.password.text()):
            self.password.clear()
            self.password.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_button.setText("Show")
            for widget in self.password_widgets:
                widget.hide()
            for widget in self.factor_widgets:
                widget.show()
            self.change_factor(False)
            self.request_button.setText("Request OTP on Telegram")
            self.status.setText("Password verified. Request an OTP on Telegram." if self.auth.factor_mode == "telegram" else "Password verified. Enter your existing authenticator code.")
            self.status.setStyleSheet("")
            self.otp.setFocus()
        else:
            self.status.setText(self.auth.last_error)
            self.status.setStyleSheet("color: #d15c67;")
            self.password.clear()

    def change_factor(self, recovery):
        self.otp.clear()
        telegram = self.auth.factor_mode == "telegram"
        self.otp_label.setText("Recovery code" if recovery else ("Telegram OTP" if telegram else "Authenticator code"))
        self.otp.setPlaceholderText("Single-use recovery code" if recovery else ("6-digit code sent to Telegram" if telegram else "6-digit authenticator code"))
        self.request_button.setVisible(not recovery and telegram and self.auth.awaiting_otp)

    def request_code(self):
        if self.request.busy:
            return
        self.request_button.setEnabled(False)
        self.verify_button.setEnabled(False)
        self.recovery.setEnabled(False)
        self.status.setText("Sending your code to Telegram…")
        self.request.start(self.auth.request_login_otp)

    def delivery_finished(self, generation, success, message):
        if generation != self.request.generation:
            return
        self.request.busy = False
        self.request_button.setEnabled(True)
        self.verify_button.setEnabled(True)
        self.recovery.setEnabled(True)
        self.request_button.setText("Resend OTP on Telegram")
        if not self.auth.awaiting_otp:
            self.reset()
        self.status.setText(message)

    def verify(self):
        valid = self.auth.verify_second_factor(self.otp.text(), recovery=self.recovery.isChecked())
        self.otp.clear()
        if valid:
            self.db.set("remembered_username", "admin" if self.remember.isChecked() else "")
            self.reset()
            self.status.setText("Authenticated")
            self.authenticated.emit()
        else:
            message = self.auth.last_error
            if not self.auth.awaiting_otp:
                self.reset()
            self.status.setText(message)
            self.status.setStyleSheet("color: #d15c67;")

    def reset(self):
        self.request.invalidate()
        self.auth.cancel_login()
        self.request_button.setEnabled(True)
        self.verify_button.setEnabled(True)
        self.recovery.setEnabled(True)
        self.password.clear()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_button.setText("Show")
        for widget in self.password_widgets:
            widget.show()
        self.otp.clear()
        self.recovery.setChecked(False)
        for widget in self.factor_widgets:
            widget.hide()
        self.status.setText(self.auth.error or "Sign in with your password and OTP.")
        self.status.setStyleSheet("")
