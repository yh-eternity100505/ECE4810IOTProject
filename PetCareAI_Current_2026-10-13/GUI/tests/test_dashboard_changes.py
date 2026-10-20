import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from pet_monitoring.controller import MonitoringController
from pet_monitoring.services.database_service import DatabaseService
from pet_monitoring.services.change_log import ChangeLog
from pet_monitoring.services.pi_hub_launcher import PiHubLauncher
from pet_monitoring.services.movement_receiver import parse_message
from pet_monitoring.ui.main_window import MainWindow
from tests.test_pi_hub_startup import phone

APP = QApplication.instance() or QApplication([])


class DashboardChangesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.db"
        self.db = DatabaseService(self.path)
        self.c = MonitoringController(self.db)
        self.window = MainWindow(self.c)

    def tearDown(self):
        self.window.close()
        self.c.executor.shutdown(wait=True)
        self.window.deleteLater()
        APP.processEvents()
        self.db.close()
        self.temp.cleanup()

    def snapshot(self):
        spec = importlib.util.spec_from_file_location("phone_snapshot_test", Path(__file__).resolve().parents[2] / "pi_hub" / "gui_session.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        data = copy.deepcopy(phone.DEFAULT_SETTINGS)
        data["telegram"]["token"] = "DO-NOT-EXPORT-THIS-TOKEN"
        data["users"] = {"admin": {"hash": "DO-NOT-EXPORT-THIS-HASH"}}
        (Path(self.temp.name) / "hub_settings.json").write_text(json.dumps(data), encoding="utf-8")
        with patch.dict("sys.modules", {"pi_hub": phone}):
            message = module.phone_snapshot(Path(self.temp.name))
        encoded = json.dumps(message)
        self.assertNotIn("DO-NOT-EXPORT", encoded)
        return parse_message(encoded)

    def test_unique_overview_history_log_and_removed_movement_navigation(self):
        names = [nav.text() for nav in self.window.nav_buttons]
        self.assertEqual(len(names), 8)
        self.assertEqual(sum("Overview" in name for name in names), 1)
        self.assertFalse(any("Activity Analysis" in name for name in names))
        self.assertTrue(any("History" in name for name in names))
        self.assertFalse(any(button.text() in ("Movement & Access", "Raspberry Pi movement access") for button in self.window.findChildren(QPushButton)))
        self.assertFalse(any("Presence, not a camera" in label.text() for label in self.window.findChildren(QLabel)))
        self.assertTrue(hasattr(self.window.pages[2], "table"))
        self.assertFalse(hasattr(self.window.pages[2], "plots"))

    def test_settings_changes_logged_once_and_remain_after_restarting_log(self):
        message = self.snapshot()
        self.window.hub_sync.apply(message)
        self.window.hub_sync.apply(message)
        self.assertEqual(len(self.window.change_log.rows()), 1)
        changed = copy.deepcopy(message)
        changed["pet"]["name"] = "Luna"
        changed["sensors"]["2"]["threshold_cm"] = 12
        self.window.hub_sync.apply(changed)
        rows = self.window.change_log.rows()
        self.assertEqual(len(rows), 3)
        self.assertTrue(any("Luna" in row[2] for row in rows))
        self.assertTrue(any("Sensor 2 threshold" in row[2] for row in rows))
        restarted = ChangeLog(self.db)
        restarted.settings(changed)
        self.assertEqual(len(restarted.rows()), 3)
        self.assertEqual(restarted.rows(category="Activity"), [])

    def test_activity_log_deduplicates_events_and_rejects_stale_revision(self):
        self.window.hub.set_mode("Live Pi")
        revision = self.window.hub.revision
        event = {"type": "ZONE_EVENT", "sensor_id": "2", "revision": revision,
                 "event_id": "visit-1", "duration_seconds": 8.5}
        self.window.change_log.event(event)
        self.assertEqual(self.window.change_log.rows(), [])
        self.window.hub.process({"type": "CONFIG_ACK", "revision": revision})
        self.window.change_log.event(event)
        self.window.change_log.event(event)
        self.assertEqual(len(self.window.change_log.rows()), 1)
        self.assertIn("Sleeping", self.window.change_log.rows()[0][2])
        self.window.change_log.event({**event, "revision": revision + 1, "event_id": "invalid"})
        self.assertEqual(len(self.window.change_log.rows()), 1)

    def test_phone_button_uses_loopback_url_and_reports_browser_failure(self):
        self.window.set_phone_app_status("Ready", "http://127.0.0.1:23457", "http://192.0.2.10:8080")
        self.assertTrue(self.window.phone_app_button.isEnabled())
        self.assertIn("192.0.2.10", self.window.phone_app_button.toolTip())
        with patch("pet_monitoring.ui.main_window.QDesktopServices.openUrl", return_value=True) as opened:
            self.assertTrue(self.window.open_phone_app())
            self.assertEqual(opened.call_args.args[0].toString(), "http://127.0.0.1:23457")
        with patch("pet_monitoring.ui.main_window.QDesktopServices.openUrl", return_value=False):
            self.assertFalse(self.window.open_phone_app())
            self.assertIn("127.0.0.1:23457", self.window.statusBar().currentMessage())
        self.window.phone_app_stopped()
        self.assertFalse(self.window.phone_app_button.isEnabled())

    def test_launcher_reassembles_and_validates_settings_stream(self):
        launcher = PiHubLauncher("jiating@192.0.2.10")
        messages = []
        launcher.synced.connect(messages.append)
        packet = b"[PETCARE_HUB_SYNC] " + json.dumps(self.snapshot()).encode() + b"\r\n"
        with patch.object(launcher.process, "readAllStandardOutput", side_effect=[packet[:25], packet[25:]]):
            launcher._read()
            self.assertEqual(messages, [])
            launcher._read()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["type"], "HUB_SYNC")

