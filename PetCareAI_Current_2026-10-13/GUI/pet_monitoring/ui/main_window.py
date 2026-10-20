from datetime import datetime
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QMainWindow, QWidget, QStackedWidget, QVBoxLayout, QHBoxLayout,
                              QFrame, QScrollArea, QButtonGroup, QApplication)
from .widgets import label, button, CorgiMascot
from .theme import apply_theme
from .login_page import LoginPage
from .dashboard_page import DashboardPage
from .live_monitoring_page import LiveMonitoringPage
from .behaviour_page import BehaviourPage
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
        self.movement.authentication_requested.connect(self.show_movement)
        self.movement.authenticated.connect(self.movement_authenticated)
        self.setWindowTitle("PetCare AI · Reconfigurable Smart Pet Hub")
        self.resize(1440, 900)
        self.setMinimumSize(1080, 680)
        self.root = QStackedWidget()
        self.setCentralWidget(self.root)
        self.login_page = LoginPage(controller.auth, controller.db)
        self.root.addWidget(self.login_page)
        self.login_page.movement_requested.connect(self.show_movement)
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
        self.pages = [DashboardPage(controller), LiveMonitoringPage(controller), BehaviourPage(controller),
                      HistoryPage(controller), AlertsPage(controller), PetProfilePage(controller), SettingsPage(controller),
                      HubPage(self.hub, self.show_movement, self.movement),
                      SecurityInspector(self.movement.receiver)]
        self.pages[1].bind_live_inputs(self.hub, self.movement)
        from pet_monitoring.services.hub_sync import HubSync
        self.hub_sync = HubSync(controller, self.hub, self.movement.receiver, self)
        self.hub_sync.synced.connect(lambda: (self.pages[7].sync_from_hub(self.hub_sync.state), self.pages[5].reload(self.hub_sync.state)))
        self.pages[7].sync_from_hub(self.hub_sync.state)
        if self.hub_sync.state:
            self.pages[5].reload(self.hub_sync.state)
        self.pages[1].heading.setText("Live Sensors")
        for index, title in ((0, "Pet activity overview"), (2, "Activity Analysis"), (3, "Sleep & toilet history")):
            self.pages[index].live_heading = title
        for page in (self.pages[0], self.pages[2], self.pages[3]):
            bind_analysis_page(page, self.hub)
            page.box.insertWidget(1, LiveSensorPanel(self.hub, self.movement))
        names = ["Overview", "Live Sensors", "Activity Analysis", "History", "Alerts", "Pet Profile", "Settings", "Sensor Hub", "Security Inspector"]
        symbols = ["▦", "◉", "◇", "↗", "⚑", "◎", "⚙", "▤", "◇"]
        for page in (self.pages[0], self.pages[2], self.pages[3]):
            notice = label("DEMO ANALYTICS · simulated five-zone prototype", "muted", True)
            page.box.insertWidget(0, notice)
            self.hub.changed.connect(lambda widget=notice: widget.setVisible(self.hub.mode != "Live Pi"))
            notice.setVisible(self.hub.mode != "Live Pi")
        self.nav_buttons = []
        self.navigation = QButtonGroup(self)
        self.navigation.setExclusive(True)
        for i, (page, name, symbol) in enumerate(zip(self.pages, names, symbols)):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.page_stack.addWidget(scroll)
            nav = button(f"{symbol}   {name}", lambda checked=False, index=i: self.navigate(index))
            nav.setObjectName("nav")
            nav.setCheckable(True)
            self.navigation.addButton(nav, i)
            self.nav_buttons.append(nav)
            if i == 7:
                side.insertWidget(3, nav)
            else:
                side.addWidget(nav)
        # Keep Settings last visually while preserving existing page indices.
        side.removeWidget(self.nav_buttons[6])
        side.addWidget(self.nav_buttons[6])
        self.nav_buttons[7].setChecked(True)
        self.page_stack.setCurrentIndex(7)
        side.addStretch()
        side.addWidget(button("Movement & Access", self.show_movement))
        self.phone_app_url = ""
        self.phone_app_button = button("Open phone app", self.open_phone_app)
        self.phone_app_button.setEnabled(False)
        self.phone_app_button.setToolTip("Phone app starts with the GUI")
        side.addWidget(self.phone_app_button)
        self.sidebar_pet = label("Milo\nCat · British Shorthair", wrap=True)
        companion_row = QHBoxLayout()
        companion_row.setSpacing(10)
        self.corgi_mascot = CorgiMascot()
        companion_row.addWidget(self.corgi_mascot)
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
        self.navigate(7)
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

    def set_phone_app_status(self, message, url=""):
        if url:
            self.phone_app_url = url
        self.phone_app_button.setEnabled(bool(self.phone_app_url))
        self.phone_app_button.setToolTip(message + ("\n" + self.phone_app_url if self.phone_app_url else ""))
        self.statusBar().showMessage(message, 15000)

    def open_phone_app(self):
        if self.phone_app_url:
            QDesktopServices.openUrl(QUrl(self.phone_app_url))

    def phone_app_stopped(self):
        self.phone_app_url = ""
        self.phone_app_button.setEnabled(False)

    def show_movement(self):
        self.movement_dialog.show()
        self.movement_dialog.raise_()
        self.movement_dialog.activateWindow()

    def movement_authenticated(self):
        # A movement signal is never an administrator authentication result.
        self.movement_dialog.hide()
        if not self.c.auth.is_authenticated:
            self.login_page.status.setText("Sign in with the administrator password and OTP.")
            self.root.setCurrentIndex(0)
