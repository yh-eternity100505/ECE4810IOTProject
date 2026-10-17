from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QCheckBox
from .widgets import Card, label, button, CorgiMascot


class LoginPage(QWidget):
    authenticated = Signal()
    movement_requested = Signal()

    def __init__(self, auth, database):
        super().__init__()
        self.auth, self.db = auth, database
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
        card.box.addWidget(label("Username", "eyebrow"))
        self.username = QLineEdit()
        self.username.setPlaceholderText("Enter your username")
        self.username.setText(self.db.get("remembered_username", ""))
        card.box.addWidget(self.username)
        card.box.addWidget(label("Password", "eyebrow"))
        password_row = QHBoxLayout()
        self.password = QLineEdit()
        self.password.setPlaceholderText("Enter your password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        password_row.addWidget(self.password)
        self.show_button = button("Show", self.toggle_password)
        password_row.addWidget(self.show_button)
        card.box.addLayout(password_row)
        self.remember = QCheckBox("Remember username")
        self.remember.setChecked(bool(self.username.text()))
        card.box.addWidget(self.remember)
        self.login_button = button("Sign in  →", self.login, True)
        card.box.addWidget(self.login_button)
        card.box.addWidget(button("Raspberry Pi movement access", self.movement_requested.emit))
        self.status = label("Local demo authentication", "muted", True)
        card.box.addWidget(self.status)
        card.box.addSpacing(8)
        card.box.addWidget(label("Monitor patterns. Stay informed.\nThis application does not provide veterinary diagnoses.", "muted", True))
        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignVCenter)
        self.password.returnPressed.connect(self.login)
        self.username.returnPressed.connect(self.password.setFocus)

    def toggle_password(self):
        hidden = self.password.echoMode() == QLineEdit.EchoMode.Password
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self.show_button.setText("Hide" if hidden else "Show")

    def login(self):
        if self.auth.authenticate(self.username.text(), self.password.text()):
            self.db.set("remembered_username", self.username.text().strip() if self.remember.isChecked() else "")
            self.password.clear()
            self.password.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_button.setText("Show")
            self.status.setText("Authenticated")
            self.authenticated.emit()
        else:
            self.status.setText("Invalid username or password. Please try again.")
            self.status.setStyleSheet("color: #d15c67;")
            self.password.clear()

    def reset(self):
        self.password.clear()
        self.status.setText("Signed out. Sign in to continue.")
        self.status.setStyleSheet("")
