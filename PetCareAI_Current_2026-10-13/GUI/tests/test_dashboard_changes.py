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


    def test_local_calibration_survives_phone_snapshots_and_restart(self):
        from tests.auth_support import configure_auth, login
        from pet_monitoring.services.hub_sync import HubSync
        configure_auth(self.c, self.temp.name)
        login(self.c.auth)
        message = self.snapshot()
        message['sensors']['1']['calibration'] = None
        self.window.hub_sync.apply(message)
        saved = {'1': {'full_cm': 5.8, 'empty_cm': 16.8, 'capacity_l': 1.0, 'linear_volume': True}}
        self.assertTrue(self.window.hub.save_level_calibration(saved)[0])
        for _ in range(3):
            self.window.hub_sync.apply(message)
        self.assertEqual(self.window.hub.level_calibration, saved)
        self.assertEqual(self.db.get('level_calibration'), saved)
        # New synchroniser instance uses persisted conflict tracking.
        sync = HubSync(self.c, self.window.hub, self.window.movement.receiver)
        sync.apply(message)
        self.assertEqual(self.window.hub.level_calibration, saved)
        confirmation = copy.deepcopy(message)
        confirmation['settings_version'] = 1
        confirmation['sensors']['1']['calibration'] = {'full_cm': 5.8, 'empty_cm': 16.8}
        operation_id = sync.pending[0]['operation_id']
        sync.acknowledge(operation_id, confirmation)
        changed = copy.deepcopy(message)
        changed['settings_version'] = 2
        changed['sensors']['1']['calibration'] = {'full_cm': 6.0, 'empty_cm': 18.0}
        sync.apply(changed)
        self.assertEqual(self.window.hub.level_calibration['1']['full_cm'], 6.0)
        self.assertEqual(self.window.hub.level_calibration['1']['capacity_l'], 1.0)
        self.assertTrue(self.window.hub.level_calibration['1']['linear_volume'])

    def test_repeated_phone_updates_do_not_erase_calibration_drafts(self):
        from tests.auth_support import configure_auth, login
        configure_auth(self.c, self.temp.name)
        login(self.c.auth)
        message = self.snapshot()
        message['sensors']['1']['calibration'] = None
        self.window.hub_sync.apply(message)
        page = self.window.pages[6].calibration
        _, full, empty, capacity, linear = page.fields['1']
        full.setValue(5.8)
        empty.setValue(16.8)
        capacity.setValue(1)
        linear.setChecked(True)
        self.window.hub_sync.apply(message)
        self.assertEqual((full.value(), empty.value(), capacity.value(), linear.isChecked()), (5.8, 16.8, 1, True))
        page.save()
        self.window.hub_sync.apply(message)
        self.assertEqual((full.value(), empty.value(), capacity.value(), linear.isChecked()), (5.8, 16.8, 1, True))
        self.assertIn('saved', page.feedback.text())

    def test_save_before_first_phone_snapshot_preserves_calibration(self):
        from tests.auth_support import configure_auth, login
        configure_auth(self.c, self.temp.name)
        login(self.c.auth)
        saved = {'1': {'full_cm': 4.0, 'empty_cm': 12.0, 'capacity_l': 0.0, 'linear_volume': False}}
        self.assertTrue(self.window.hub.save_level_calibration(saved)[0])
        message = self.snapshot()
        message['sensors']['1']['calibration'] = {'full_cm': 2.0, 'empty_cm': 10.0}
        self.window.hub_sync.apply(message)
        self.window.hub_sync.apply(message)
        self.assertEqual(self.window.hub.level_calibration['1'], saved['1'])

    def test_two_way_calibration_over_real_tunnel_socket(self):
        from tests.auth_support import configure_auth, login
        from tests.test_pi_hub_startup import wait_for
        from pet_monitoring.services.pi_hub_bridge import PiHubBridge
        configure_auth(self.c, self.temp.name)
        login(self.c.auth)
        settings = phone.SettingsStore(Path(self.temp.name) / 'phone-settings.json')
        db = phone.Database(Path(self.temp.name) / 'phone.db')
        hub = phone.Hub(settings, db, phone.Telegram(settings), dashboard_source=True)
        server = phone.SensorLink(hub, '127.0.0.1', 0)
        server.start()
        self.assertTrue(server.ready.wait(2))
        sync = self.window.hub_sync
        bridge = PiHubBridge(self.window.movement.receiver, self.window.hub, settings_sync=sync)
        bridge.start(server.port)
        try:
            self.assertTrue(wait_for(lambda: bridge.socket.state() == bridge.socket.SocketState.ConnectedState))
            local = {'1': {'full_cm': 5.8, 'empty_cm': 16.8, 'capacity_l': 1, 'linear_volume': True}}
            self.assertTrue(self.window.hub.save_level_calibration(local)[0])
            self.assertTrue(wait_for(lambda: not sync.pending))
            self.assertEqual(settings.data['calibration']['1'], {'full_cm': 5.8, 'empty_cm': 16.8})
            self.assertEqual(self.window.hub.level_calibration['1'], local['1'])
            version = settings.data['settings_version']
            stale = hub.sync_message()
            # Simulate the same persisted change made by the authenticated phone endpoint.
            with settings.lock:
                settings.data['calibration']['1'] = {'full_cm': 6.5, 'empty_cm': 20.0}
                settings.data['settings_version'] += 1
                settings.save()
            from pet_monitoring.services.movement_receiver import parse_message
            sync.apply(parse_message(json.dumps(hub.sync_message())))
            self.assertEqual(self.window.hub.level_calibration['1']['full_cm'], 6.5)
            sync.apply(parse_message(json.dumps(stale)))
            self.assertEqual(self.window.hub.level_calibration['1']['full_cm'], 6.5)
            next_local = {'1': {**local['1'], 'full_cm': 7.0}}
            self.assertTrue(self.window.hub.save_level_calibration(next_local)[0])
            self.assertTrue(wait_for(lambda: not sync.pending))
            self.assertEqual(settings.data['calibration']['1']['full_cm'], 7.0)
            self.assertGreater(settings.data['settings_version'], version)
            # Retrying an old operation must never undo that later save.
            old_id = settings.data['desktop_settings_operations'][0]
            hub.apply_desktop_settings({'type': 'DASHBOARD_SETTINGS', 'operation_id': old_id,
                                       'calibration': {'1': {'full_cm': 5.8, 'empty_cm': 16.8}}})
            self.assertEqual(settings.data['calibration']['1']['full_cm'], 7.0)
            # The same ordered path synchronises assignments and thresholds.
            mapping = dict(self.window.hub.mapping)
            thresholds = {**self.window.hub.thresholds, '2': 12.0}
            self.assertTrue(self.window.hub.save_configuration(mapping, thresholds))
            self.assertTrue(wait_for(lambda: not sync.pending))
            self.assertEqual(settings.data['sensors']['thresholds']['2'], 12.0)
            hub.save_sensor_config(mapping, {**thresholds, '2': 11.0})
            sync.apply(parse_message(json.dumps(hub.sync_message())))
            self.assertEqual(self.window.hub.thresholds['2'], 11.0)
        finally:
            bridge.stop()
            server.stop.set()
            server.join(timeout=2)
            hub.executor.shutdown(wait=True)
            db.conn.close()
