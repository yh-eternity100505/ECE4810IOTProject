from datetime import datetime
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QMainWindow, QWidget, QStackedWidget, QVBoxLayout, QHBoxLayout,
                              QFrame, QScrollArea, QButtonGroup, QApplication)
from .widgets import label, button, DogMascot
from .theme import apply_theme
from .navigation import NavigationButton
from .login_page import LoginPage
from .dashboard_page import DashboardPage
from .live_monitoring_page import LiveMonitoringPage
from .history_page import HistoryPage
from .alerts_page import AlertsPage
from .pet_profile_page import PetProfilePage
from .settings_page import SettingsPage
from .movement_dialog import MovementDialog
from pet_monitoring.movement_controller import MovementController
from pet_monitoring.hub_controller import HubController
from .hub_page import HubPage
from .live_sensor_panel import LiveSensorPanel
from .activity_analysis import bind_analysis_page
from .security_panel import SecurityInspector


class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.movement = MovementController(controller)
        self.hub = HubController(controller, self.movement.receiver)
        self.movement_dialog = MovementDialog(self.movement, self)
        self.movement.authenticated.connect(self.movement_authenticated)
        self.setWindowTitle("PetCare AI · Reconfigurable Smart Pet Hub")
        self.resize(1440, 900)
        self.setMinimumSize(1080, 680)
        self.root = QStackedWidget()
        self.setCentralWidget(self.root)
        self.login_page = LoginPage(controller.auth, controller.db)
        self.movement.authentication_requested.connect(self.show_movement)
        self.movement_dialog.sign_in_requested.connect(self.continue_gesture_sign_in)
        self.root.addWidget(self.login_page)
        shell = QWidget()
        shell.setObjectName("shell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(216)
        sidebar.setMinimumHeight(680)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 28, 14, 22)
        side.setSpacing(7)
        brand = label("◈  PetCare AI", "sectionTitle")
        side.addWidget(brand)
        side.addWidget(label("BEHAVIOUR & WELLNESS", "muted"))
        side.addSpacing(30)
        self.page_stack = QStackedWidget()
        from pet_monitoring.services.change_log import ChangeLog
        self.change_log = ChangeLog(controller.db, self, hub=self.hub)
        self.movement.receiver.received.connect(self.change_log.event)
        self.pages = [DashboardPage(controller), LiveMonitoringPage(controller),
                      HistoryPage(controller, self.change_log), AlertsPage(controller), PetProfilePage(controller), SettingsPage(controller),
                      HubPage(self.hub, self.show_movement, self.movement),
                      SecurityInspector(self.movement.receiver),
                      MovementDialog(self.movement, embedded=True)]
        self.door_auth_page = self.pages[8]
        self.pages[1].bind_live_inputs(self.hub, self.movement)
        from pet_monitoring.services.hub_sync import HubSync
        self.hub_sync = HubSync(controller, self.hub, self.movement.receiver, self, change_log=self.change_log)
        self.hub_sync.synced.connect(lambda: (self.pages[6].sync_from_hub(self.hub_sync.state), self.pages[4].reload(self.hub_sync.state)))
        self.pages[6].sync_from_hub(self.hub_sync.state)
        if self.hub_sync.state:
            self.pages[4].reload(self.hub_sync.state)
        self.pages[1].heading.setText("Live Sensors")
        self.pages[0].live_heading = "Pet activity overview"
        bind_analysis_page(self.pages[0], self.hub)
        self.pages[0].box.insertWidget(1, LiveSensorPanel(self.hub, self.movement))
        notice = label("DEMO ANALYTICS - simulated five-zone prototype", "muted", True)
        self.pages[0].box.insertWidget(0, notice)
        self.hub.changed.connect(lambda: notice.setVisible(self.hub.mode != "Live Pi"))
        notice.setVisible(self.hub.mode != "Live Pi")
        names = ["Overview", "Live Sensors", "History", "Alerts", "Pet Profile", "Settings", "Sensor Hub", "Security Inspector", "Door Authentication"]
        self.nav_buttons = []
        self.navigation = QButtonGroup(self)
        self.navigation.setExclusive(True)
        for i, (page, name) in enumerate(zip(self.pages, names)):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.page_stack.addWidget(scroll)
            nav = NavigationButton(name, lambda checked=False, index=i: self.navigate(index))
            nav.setObjectName("nav")
            nav.setCheckable(True)
            self.navigation.addButton(nav, i)
            self.nav_buttons.append(nav)
            if i == 6:
                side.insertWidget(3, nav)
            else:
                side.addWidget(nav)
        # Keep Settings last visually while preserving existing page indices.
        side.removeWidget(self.nav_buttons[5])
        side.addWidget(self.nav_buttons[5])
        self.nav_buttons[6].setChecked(True)
        self.page_stack.setCurrentIndex(6)
        side.addStretch()
        self.phone_lan_url = ""
        self.phone_app_url = ""
        self.phone_app_button = button("Open phone app", self.open_phone_app)
        self.phone_app_button.setEnabled(False)
        self.phone_app_button.setToolTip("Phone app starts with the GUI")
        side.addWidget(self.phone_app_button)
        self.sidebar_pet = label("Milo\nCat · British Shorthair", wrap=True)
        companion_row = QHBoxLayout()
        companion_row.setSpacing(10)
        self.dog_mascot = DogMascot()
        companion_row.addWidget(self.dog_mascot)
        companion_row.addWidget(self.sidebar_pet, 1)
        side.addLayout(companion_row)
        side.addWidget(label("Private, camera-free insights", "muted", True))
        side.addSpacing(18)
        logout = button("↪   Logout", self.logout)
        logout.setObjectName("nav")
        side.addWidget(logout)
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setObjectName("sidebarScroll")
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_scroll.setFixedWidth(236)
        sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sidebar_scroll.setWidget(sidebar)
        shell_layout.addWidget(sidebar_scroll)
        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        status_frame = QFrame()
        status_frame.setObjectName("topStatusBar")
        status_row = QHBoxLayout(status_frame)
        status_row.setContentsMargins(30, 16, 30, 16)
        self.global_status = label("", "muted", True)
        self.clock = label("", "muted")
        status_row.addWidget(self.global_status, 1)
        status_row.addWidget(self.clock)
        content.addWidget(status_frame)
        content.addWidget(self.page_stack, 1)
        shell_layout.addLayout(content, 1)
        self.root.addWidget(shell)
        self.login_page.authenticated.connect(self.logged_in)
        controller.session_required.connect(self.session_expired)
        controller.changed.connect(self.refresh_status)
        self.hub.changed.connect(self.refresh_status)
        self.movement.changed.connect(self.refresh_status)
        controller.settings_changed.connect(self.change_theme)
        controller.message.connect(lambda message: self.statusBar().showMessage(message, 10000))
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.refresh_clock)
        self.clock_timer.start(1000)
        self.change_theme()
        self.refresh_status()
        self.refresh_clock()

    def logged_in(self):
        if not self.c.auth.is_authenticated:
            return
        self.movement.cancel_authentication()
        self.root.setCurrentIndex(1)
        self.navigate(6)
        self.c.start()
        self.statusBar().showMessage("Signed in · Monitoring active", 5000)

    def logout(self):
        self.movement.cancel_authentication()
        self.c.pause()
        self.c.auth.logout()
        self.login_page.reset()
        self.root.setCurrentIndex(0)
        self.statusBar().clearMessage()

    def session_expired(self):
        self.movement.cancel_authentication()
        self.login_page.reset()
        self.login_page.status.setText("Session expired. Please sign in again.")
        self.root.setCurrentIndex(0)
        self.statusBar().showMessage("Authentication Required", 10000)

    def navigate(self, index):
        self.page_stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)

    def change_theme(self):
        apply_theme(QApplication.instance(), self.c.settings["theme"])

    def refresh_status(self):
        c = self.c
        zones = sum(state['distance'] is not None for state in self.hub.states.values())
        gesture = 'Live distance' if self.movement.gesture_distance is not None else 'No valid reading'
        self.global_status.setText(f"● Hub: {self.hub.mode}    /    Pet valid readings: {zones} / 4    /    Sensor 5: {gesture}    /    Telegram: {c.telegram_status}    /    Legacy Security: {c.security_status}")
        self.sidebar_pet.setText(f"{c.profile.name}\n{c.profile.animal_type} · {c.profile.breed}")
        if not c.db.available:
            self.statusBar().showMessage(c.db.error)

    def refresh_clock(self):
        self.clock.setText(datetime.now().strftime("%d %b  ·  %H:%M:%S"))

    def closeEvent(self, event):
        self.hub.close()
        self.movement.close()
        self.c.close()
        event.accept()

    def set_phone_app_status(self, message, url="", phone_url=""):
        if url:
            self.phone_app_url = url
        if phone_url:
            self.phone_lan_url = phone_url
        self.phone_app_button.setEnabled(bool(self.phone_app_url))
        self.phone_app_button.setToolTip(message + ("\nOn this laptop: " + self.phone_app_url if self.phone_app_url else "") + ("\nOn your phone: " + self.phone_lan_url if self.phone_lan_url else ""))
        self.statusBar().showMessage(message, 15000)

    def open_phone_app(self):
        if self.phone_app_url:
            if QDesktopServices.openUrl(QUrl(self.phone_app_url)):
                return True
            self.statusBar().showMessage("Could not open a browser. Open " + self.phone_app_url, 15000)
        else:
            self.statusBar().showMessage("Phone app is not connected yet. Check Pi power, Wi-Fi and SSH status.", 15000)
        return False

    def phone_app_stopped(self):
        self.phone_app_url = ""
        self.phone_app_button.setEnabled(False)

    def show_movement(self):
        # Door authentication is available only inside the signed-in dashboard.
        if self.root.currentIndex() == 1 and self.c.auth.is_authenticated:
            self.navigate(8)

    def continue_gesture_sign_in(self):
        self.movement_dialog.hide()
        if self.c.auth.is_authenticated:
            return
        self.root.setCurrentIndex(0)
        self.login_page.status.setText("Gesture recognised. Complete administrator password and OTP verification.")
        if self.c.auth.awaiting_otp:
            self.login_page.otp.setFocus()
        else:
            self.login_page.password.setFocus()

    def movement_authenticated(self):
        # Only a verified, consumed Telegram OTP can create this session.
        if not self.c.auth.is_authenticated:
            self.root.setCurrentIndex(0)
            return
        self.movement_dialog.hide()
        if self.root.currentIndex() == 1:
            self.statusBar().showMessage("Door authentication verified with gesture and Telegram OTP", 10000)
            return
        self.login_page.reset()
        self.logged_in()
        self.statusBar().showMessage("Signed in with gesture and Telegram OTP", 10000)
