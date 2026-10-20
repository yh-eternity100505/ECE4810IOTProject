"""End-to-end checks run Qt offscreen, without hardware or Telegram credentials."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import math
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtTest import QTest
from pet_monitoring.controller import MonitoringController
from pet_monitoring.models import SensorReading, PetProfile
from pet_monitoring.logic.behaviour_interpreter import validate_reading, interpret
from pet_monitoring.logic.wellness_score import calculate
from pet_monitoring.logic.anomaly_detection import detect
from pet_monitoring.services.database_service import DatabaseService
from pet_monitoring.services.authentication_service import AuthenticationService
from pet_monitoring.services.telegram_service import TelegramService
from pet_monitoring.services.credential_service import CredentialService
from pet_monitoring.ui.main_window import MainWindow
from tests.auth_support import configure_auth, login, login_page, PASSWORD

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=3000):
    deadline = time.monotonic() + timeout / 1000
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        QTest.qWait(10)
    return predicate()


class ApplicationTests(unittest.TestCase):
    def test_all_sensor_displays_share_live_pi_readings(self):
        from pet_monitoring.ui.live_sensor_panel import LiveSensorPanel
        self.window.hub.set_mode("Live Pi")
        self.window.hub.process({"type": "CONFIG_ACK", "revision": self.window.hub.revision})
        self.window.hub.process({"type": "SENSOR_READING", "sensor_id": "1", "revision": self.window.hub.revision, "distance_cm": 11.2, "presence": True})
        self.window.movement.process({"type": "GESTURE_READING", "sensor_id": 5, "distance_cm": 24.2, "state": "WAITING", "samples": 0, "total": 60})
        live = self.window.pages[1]
        self.assertEqual(live.cards["entrance"][1].text(), "24.2 cm")
        self.assertEqual(live.cards["sleep"][1].text(), "11.2 cm")
        for index in (0,):
            panel = self.window.pages[index].findChild(LiveSensorPanel)
            self.assertEqual(panel.table.item(4, 2).text(), "24.2 cm")
        self.window.navigate(5)
        self.window.movement.process({"type": "GESTURE_READING", "sensor_id": 5, "distance_cm": 9.4, "state": "RECORDING", "samples": 12, "total": 60})
        self.assertEqual(live.cards["entrance"][1].text(), "9.4 cm")
        self.assertEqual(self.window.pages[0].findChild(LiveSensorPanel).table.item(4, 2).text(), "9.4 cm")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"
        self.db = DatabaseService(self.path)
        self.c = MonitoringController(self.db)
        # Existing monitoring tests operate after an authenticated session is created.
        configure_auth(self.c, self.tmp.name)
        login(self.c.auth)
        self.c.tick()
        self.window = MainWindow(self.c)
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.close()
        self.c.executor.shutdown(wait=True)
        APP.processEvents()
        self.db.close()
        self.window.deleteLater()
        APP.processEvents()
        self.tmp.cleanup()

    def login(self):
        page = self.window.login_page
        login_page(page)
        APP.processEvents()
        self.assertEqual(self.window.root.currentIndex(), 1)

    def test_login_navigation_and_logout(self):
        page = self.window.login_page
        page.username.setText("admin")
        page.password.setText("wrong")
        page.login_button.click()
        self.assertEqual(self.window.root.currentIndex(), 0)
        self.assertIn("failed", page.status.text())
        page.password.setText("hidden")
        page.show_button.click()
        self.assertEqual(page.password.echoMode(), QLineEdit.EchoMode.Normal)
        page.show_button.click()
        self.assertEqual(page.password.echoMode(), QLineEdit.EchoMode.Password)
        page.remember.setChecked(True)
        self.login()
        self.assertEqual(self.db.get("remembered_username"), "admin")
        self.assertEqual(page.password.text(), "")
        for i, nav in enumerate(self.window.nav_buttons):
            nav.click()
            APP.processEvents()
            self.assertEqual(self.window.page_stack.currentIndex(), i)
            self.assertTrue(nav.isChecked())
        self.window.logout()
        self.assertFalse(self.c.timer.isActive())
        self.assertEqual(self.window.root.currentIndex(), 0)

    def test_live_timer_and_believable_patterns(self):
        self.login()
        first = self.c.readings[0]
        QTest.qWait(1150)
        self.assertNotEqual(first.timestamp, self.c.readings[0].timestamp)
        self.assertEqual(len(self.c.readings), 5)
        self.assertEqual(self.c.connected_count, 5)
        self.assertEqual(self.c.latest_event.classification, "Normal Sleep")
        self.assertGreaterEqual(self.c.latest_event.confidence, .9)
        self.assertEqual(self.c.location, "Sleeping Area")
        # The schedule moves to a short entrance event, then food, rather than random jumping.
        self.c.sensor.start = time.monotonic() - 46
        self.c.tick()
        self.assertEqual(self.c.latest_event.classification, "Pass-by")
        self.assertEqual(self.c.location, "Entrance")
        self.c.sensor.start = time.monotonic() - 50
        self.c.tick()
        self.assertEqual(self.c.behaviour, "Eating")
        self.assertIn("Pet eating", self.window.pages[1].cards["food"][2].text())

    def test_all_demo_buttons_and_notification_delivery(self):
        self.login()
        demo = self.window.pages[5]
        self.c.simulate("normal")
        normal = self.c.wellness.score
        expectations = {
            "abnormal": "Abnormal movement", "low_food": "Low food activity",
            "low_water": "Low water activity", "restless": "Unusual sleep",
            "sensor_failure": "Sensor disconnected", "network_failure": "Network disconnected"}
        for scenario, title in expectations.items():
            with self.subTest(scenario=scenario):
                demo.demo_buttons[scenario].click()
                self.assertTrue(any(a.title == title for a in self.c.alerts))
                self.assertLess(self.c.wellness.score, normal)
                alert = next(a for a in self.c.alerts if a.title == title)
                self.assertTrue(wait_for(lambda: alert.telegram_status != "Sending…"))
                if scenario == "network_failure":
                    self.assertFalse(alert.telegram_sent)
                    self.assertIn("Failed", alert.telegram_status)
                else:
                    self.assertTrue(alert.telegram_sent)
                    self.assertIn("Mock", alert.telegram_status)
                if scenario == "abnormal":
                    self.assertEqual(self.c.latest_event.classification, "Abnormal Movement")
                    self.assertEqual(self.c.latest_event.classification, "Abnormal Movement")
                if scenario == "sensor_failure":
                    self.assertEqual(self.c.connected_count, 4)
                self.assertEqual(self.db.summaries(1)[0].source, "demo")
        demo.demo_buttons["reset"].click()
        self.assertEqual(self.c.latest_event.classification, "Normal Sleep")
        self.assertEqual(self.c.connected_count, 5)
        self.assertTrue(self.c.network_online)
        self.assertEqual(self.c.wellness.status, "NORMAL")
        self.assertTrue(any(e.classification == "Abnormal Movement" for e in self.db.behaviours()))
        count = self.db.execute("SELECT COUNT(*) FROM sensors").fetchone()[0]
        self.assertGreater(count, 5)

    def test_alert_filter_read_and_clear(self):
        self.c.simulate("abnormal")
        self.assertTrue(wait_for(lambda: self.c.alerts[0].telegram_sent))
        page = self.window.pages[3]
        page.table.selectRow(0)
        page.read_button.click()
        self.assertTrue(self.c.alerts[0].read)
        self.assertTrue(self.db.alerts()[0].read)
        page.filter.setCurrentText("Critical")
        self.assertEqual(page.table.rowCount(), 0)
        page.filter.setCurrentText("Warning")
        self.assertEqual(page.table.rowCount(), 1)
        page.table.selectRow(0)
        page.clear_button.click()
        self.assertEqual(self.c.alerts, [])
        self.assertEqual(self.db.alerts(), [])
        self.c.simulate("low_food")
        self.c.simulate("low_water")
        self.c.clear_all_alerts()
        self.assertEqual(self.db.alerts(), [])

    def test_profile_settings_and_restart_persistence(self):
        profile = self.window.pages[4]
        profile.fields["name"].setText("Luna")
        profile.fields["baseline_food_visits"].setValue(7)
        profile.save()
        settings = self.window.pages[5]
        settings.thresholds["sleep"].setValue(30)
        settings.refresh_rate.setValue(.5)
        settings.theme.setCurrentText("Dark")
        settings.notifications["water"].setChecked(False)
        settings.save()
        self.c.simulate("abnormal")
        self.assertTrue(wait_for(lambda: self.c.alerts[0].telegram_sent))
        self.c.mark_read(self.c.alerts[0].id)
        self.c.pause()
        reopened = DatabaseService(self.path)
        restarted = MonitoringController(reopened)
        try:
            self.assertEqual(restarted.profile.name, "Luna")
            self.assertEqual(restarted.profile.baseline_food_visits, 7)
            self.assertEqual(restarted.settings["thresholds"]["sleep"], 30)
            self.assertEqual(restarted.settings["theme"], "Dark")
            self.assertEqual(restarted.settings["refresh_seconds"], .5)
            self.assertTrue(any(a.title == "Abnormal movement" and a.read for a in restarted.alerts))
            self.assertTrue(any(e.classification == "Abnormal Movement" for e in restarted.events))
            self.assertEqual(len(reopened.summaries(30)), 30)
        finally:
            restarted.close()
            restarted.executor.shutdown(wait=True)
            APP.processEvents()
            reopened.close()

    def test_history_ranges_and_change_log(self):
        self.login()
        self.window.navigate(2)
        page = self.window.pages[2]
        self.window.change_log.add("Phone settings", "Pet name changed from Milo to Luna")
        for index in (0, 1, 2, 3):
            page.period.setCurrentIndex(index)
            page.refresh()
            self.assertEqual(page.table.rowCount(), 1)
        page.category.setCurrentText("Activity")
        self.assertEqual(page.table.rowCount(), 0)
        page.category.setCurrentText("Phone settings")
        self.assertIn("Luna", page.table.item(0, 2).text())

    def test_resize_and_dark_theme(self):
        self.login()
        for width, height in [(1440, 900), (1280, 720)]:
            self.window.resize(width, height)
            APP.processEvents()
            for i in range(7):
                self.window.navigate(i)
                APP.processEvents()
                scroll = self.window.page_stack.currentWidget()
                self.assertLessEqual(scroll.widget().minimumSizeHint().width(), scroll.viewport().width(), f"Page {i} at {width}x{height}")
        settings = self.window.pages[5]
        settings.theme.setCurrentText("Dark")
        settings.save()
        self.assertEqual(self.c.settings["theme"], "Dark")

    def test_offline_hardware_and_invalid_data_do_not_crash(self):
        page = self.window.pages[1]
        page.mock.setChecked(False)
        self.assertFalse(self.c.settings["mock_mode"])
        self.assertEqual(self.c.connected_count, 0)
        self.assertEqual(self.c.latest_event.classification, "ML Model Unavailable")
        self.assertEqual(self.c.location, "Unknown")
        self.assertTrue(all(interpret(r) == "Sensor Offline" for r in self.c.readings))
        # Mock demo controls are disabled without hardware simulation.
        self.assertFalse(self.window.pages[5].demo_buttons["abnormal"].isEnabled())
        page.mock.setChecked(True)
        invalid = SensorReading("sleep", "Sleeping Area", float("nan"), True, "2026-01-01T00:00:00")
        with patch.object(self.c.sensor, "read", return_value=[invalid]):
            self.c.tick()
        self.assertEqual(self.c.connected_count, 0)
        self.assertEqual(self.c.readings[0].connection_status, "Invalid data")
        with patch.object(self.c.ml, "predict", side_effect=RuntimeError("model unavailable")):
            self.c.tick()
        self.assertEqual(self.c.latest_event.classification, "ML Model Unavailable")

    def test_slow_network_does_not_block_gui(self):
        heartbeat = []
        timer = QTimer()
        timer.timeout.connect(lambda: heartbeat.append(True))
        timer.start(20)
        def slow_test(service):
            time.sleep(.35)
            return False, "Failed · simulated timeout"
        with patch.object(TelegramService, "test_connection", slow_test):
            started = time.monotonic()
            self.c.test_telegram()
            self.assertLess(time.monotonic() - started, .15)
            self.assertTrue(wait_for(lambda: "simulated timeout" in self.c.telegram_status))
        timer.stop()
        self.assertGreater(len(heartbeat), 5)

    def test_notifications_disabled_and_thresholds_applied(self):
        page = self.window.pages[5]
        page.notifications["food"].setChecked(False)
        page.save()
        self.c.simulate("low_food")
        food = next(a for a in self.c.alerts if a.category == "food")
        self.assertFalse(food.telegram_sent)
        self.assertIn("Disabled", food.telegram_status)
        page.thresholds["sleep"].setValue(5)
        page.save()
        self.assertFalse(self.c.readings[0].detected)

    def test_live_totals_survive_mock_mode_switch(self):
        page = self.window.pages[1]
        page.mock.setChecked(False)
        self.c.summary.food = 2
        self.c.summary.sleep = 1.5
        page.mock.setChecked(True)
        self.c.simulate("normal")
        page.mock.setChecked(False)
        self.assertEqual(self.c.summary.source, "live")
        self.assertEqual(self.c.summary.food, 2)
        self.assertEqual(self.c.summary.sleep, 1.5)
        self.assertEqual(self.db.summaries(1, "demo")[0].food, 6)

    def test_profile_validation_and_zero_weights(self):
        profile = self.window.pages[4]
        profile.fields["name"].setText("")
        profile.save()
        self.assertEqual(self.c.profile.name, "Milo")
        self.assertIn("required", profile.feedback.text())
        profile.fields["name"].setText("Milo")
        profile.fields["baseline_sleep"].setValue(23)
        profile.fields["baseline_activity"].setValue(5)
        profile.save()
        self.assertEqual(self.c.profile.baseline_sleep, 8)
        self.assertIn("24 hours", profile.feedback.text())
        settings = self.window.pages[5]
        for field in settings.weights.values():
            field.setValue(0)
        settings.save()
        self.assertIn("positive", settings.feedback.text())


class LogicAndServiceTests(unittest.TestCase):
    def test_wellness_and_anomaly_rules(self):
        baseline = PetProfile().baselines()
        self.assertEqual(calculate(baseline, baseline).score, 100)
        self.assertEqual(calculate(baseline, baseline, anomaly=True).status, "ATTENTION")
        self.assertEqual(calculate({k: 0 for k in baseline}, baseline).score, 0)
        self.assertEqual(calculate(baseline, baseline, weights={k: 0 for k in baseline}).score, 0)
        values = {**baseline, "food": 3, "litter": 6}
        self.assertEqual({a.title for a in detect(values, baseline, "Milo")}, {"Low food activity", "Frequent litter usage"})
        self.assertEqual(detect(baseline, baseline, "Milo"), [])

    def test_invalid_sensor_values_and_no_false_level_claim(self):
        for value in [None, -1, 501, float("inf"), float("nan"), "abc"]:
            r = validate_reading(SensorReading("food", "Food Area", value, True, "now"), 25)
            self.assertFalse(r.detected)
            self.assertEqual(r.connection_status, "Invalid data")
        r = validate_reading(SensorReading("food", "Food Area", 110, False, "now"), 25)
        self.assertEqual(interpret(r), "No activity")

    def test_auth_unicode_and_wrong_credentials(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            db = DatabaseService(Path(folder) / "auth.db")
            try:
                controller = SimpleNamespace(db=db, _auth_event=lambda kind: None)
                auth = configure_auth(controller, folder)
                self.assertFalse(auth.authenticate("\u732b", PASSWORD))
                self.assertFalse(auth.authenticate("admin", "wrong"))
                self.assertFalse(auth.is_authenticated)
            finally:
                db.close()

    def test_real_telegram_failure_handling_and_token_redaction(self):
        service = TelegramService("Real", "private-token", "123")
        import requests
        with patch("requests.post", side_effect=requests.ConnectionError("private-token")):
            with self.assertLogs(level="WARNING") as logs:
                success, status = service.test_connection()
            self.assertFalse(success)
            self.assertNotIn("private-token", " ".join(logs.output))
        self.assertEqual(TelegramService("Real").test_connection(), (False, "Not configured"))
        service.enabled = False
        self.assertEqual(service.test_connection(), (False, "Disabled"))

    def test_secure_token_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            db = DatabaseService(Path(folder) / "db.sqlite")
            service = CredentialService(db)
            try:
                stored = service.save("test-secret-token")
                self.assertEqual(service.load(), "test-secret-token")
                if stored:
                    self.assertNotIn("test-secret-token", db.get("telegram_token_dpapi"))
                    self.assertEqual(CredentialService(db).load(), "test-secret-token")
                else:
                    self.assertEqual(db.get("telegram_token_dpapi", ""), "")
                service.clear()
                self.assertEqual(service.load(), "")
            finally:
                db.close()

    def test_corrupt_database_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broken.db"
            path.write_bytes(b"not a database")
            db = DatabaseService(path)
            self.assertFalse(db.available)
            self.assertEqual(db.profile().name, "Milo")
            db.close()

    def test_legacy_summary_migration(self):
        import json
        from dataclasses import asdict
        from datetime import date
        from pet_monitoring.models import DailySummary
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE daily (date TEXT PRIMARY KEY, payload TEXT)")
            day = date.today().isoformat()
            summary = DailySummary(day, food=2)
            connection.execute("INSERT INTO daily VALUES (?,?)", (day, json.dumps(asdict(summary))))
            connection.commit()
            connection.close()
            db = DatabaseService(path)
            try:
                db.seed_demo()
                self.assertEqual(db.summaries(1, "live")[0].food, 2)
                self.assertEqual(db.summaries(1, "demo")[0].source, "demo")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
