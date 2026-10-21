"""Shared live telemetry and GUI-managed hub startup, without SSH or Pi hardware."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import types
import urllib.request
import urllib.error
from unittest.mock import patch, Mock
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from pet_monitoring.security.edc_transport import EDCCodec
from pet_monitoring.services.movement_receiver import MovementReceiver
from pet_monitoring.services.pi_hub_bridge import PiHubBridge
from pet_monitoring.services.pi_hub_launcher import PiHubLauncher

APP = QApplication.instance() or QApplication([])
spec = importlib.util.spec_from_file_location("phone_hub", Path(__file__).resolve().parents[2] / "pi_hub" / "pi_hub.py")
phone = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phone)


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        QTest.qWait(10)
    return predicate()


class DesktopHub(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.mapping = dict(phone.DEFAULT_MAPPING)
        self.thresholds = {key: phone.MODE_THRESHOLDS[mode] for key, mode in self.mapping.items()}
        self.revision = 1
        self.acknowledged = 1


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        folder = Path(self.temp.name)
        self.settings = phone.SettingsStore(folder / "settings.json")
        self.db = phone.Database(folder / "hub.db")
        self.hub = phone.Hub(self.settings, self.db, phone.Telegram(self.settings), dashboard_source=True)
        self.server = phone.SensorLink(self.hub, "127.0.0.1", 0)
        self.server.start()
        self.assertTrue(self.server.ready.wait(2))
        self.assertIsNone(self.server.startup_error)
        self.desktop = DesktopHub()
        self.codec = EDCCodec("RELAYTEST", b"R" * 32)
        self.receiver = MovementReceiver(codec=EDCCodec("RELAYTEST", b"R" * 32))
        self.bridge = PiHubBridge(self.receiver, self.desktop)
        self.receiver.status.emit("Raspberry Pi connected · test")
        self.bridge.start(self.server.port)
        self.assertTrue(wait_for(lambda: self.hub.connected and self.hub.acknowledged == 1))

    def tearDown(self):
        self.bridge.stop()
        self.server.stop.set()
        self.server.join(timeout=2)
        self.hub.executor.shutdown(wait=True)
        self.db.conn.close()
        self.temp.cleanup()

    def reading(self, distance, revision=1):
        return {"type": "SENSOR_READING", "sensor_id": 2, "revision": revision,
                "distance_cm": distance, "presence": distance < 14}

    def send(self, message):
        self.receiver._receive_secure(json.dumps(self.codec.encode(message)).encode())

    def test_one_decode_updates_both_apps_and_rejected_packet_is_not_forwarded(self):
        received = []
        self.receiver.received.connect(received.append)
        with patch.object(self.receiver._codec, "decode", wraps=self.receiver._codec.decode) as decode:
            self.send(self.reading(10.5))
            self.assertTrue(wait_for(lambda: self.hub.states["2"]["distance"] == 10.5))
            self.assertEqual(received[-1]["distance_cm"], 10.5)
            self.assertEqual(decode.call_count, 1)
            tampered = {**self.codec.encode(self.reading(70)), "tag": "0" * 64}
            with self.assertRaisesRegex(ValueError, "HMAC"):
                self.receiver._receive_secure(json.dumps(tampered).encode())
            QTest.qWait(40)
            self.assertEqual(len(received), 1)
            self.assertEqual(self.hub.states["2"]["distance"], 10.5)

    def test_configuration_changes_are_runtime_only_and_acknowledgment_is_required(self):
        saved = json.loads(json.dumps(self.settings.data["sensors"]))
        self.desktop.mapping["2"] = "toilet"
        self.desktop.revision = 2
        self.desktop.acknowledged = None
        self.desktop.changed.emit()
        self.assertTrue(wait_for(lambda: self.hub.sensors["revision"] == 2))
        self.send(self.reading(9, revision=2))
        QTest.qWait(40)
        self.assertIsNone(self.hub.states["2"]["distance"])
        self.desktop.acknowledged = 2
        self.desktop.changed.emit()
        self.send(self.reading(8, revision=1))
        self.send(self.reading(10, revision=2))
        self.assertTrue(wait_for(lambda: self.hub.states["2"]["distance"] == 10))
        self.assertEqual(self.hub.sensors["mapping"]["2"], "toilet")
        self.assertEqual(self.settings.data["sensors"], saved)
        self.hub.save_sensor_config(self.desktop.mapping, self.desktop.thresholds)
        self.assertEqual(self.settings.data["sensors"]["mapping"], self.desktop.mapping)
        self.assertEqual(self.settings.data["settings_version"], 1)

    def test_pi_disconnect_is_reported_even_while_tunnel_is_connected(self):
        self.send(self.reading(10))
        self.assertTrue(wait_for(lambda: self.hub.states["2"]["distance"] == 10))
        self.receiver.status.emit("Listening · Raspberry Pi disconnected")
        self.assertTrue(wait_for(lambda: not self.hub.connected))
        self.assertIsNone(self.hub.states["2"]["distance"])
        self.assertIsNone(self.hub.acknowledged)

    def test_tunnel_reconnect_does_not_replay_old_readings(self):
        self.send(self.reading(10))
        self.assertTrue(wait_for(lambda: self.hub.states["2"]["distance"] == 10))
        self.bridge.socket.abort()
        self.assertTrue(wait_for(lambda: not self.hub.connected))
        self.bridge._connect()
        self.assertTrue(wait_for(lambda: self.hub.connected))
        self.assertIsNone(self.hub.states["2"]["distance"])
        self.send(self.reading(11))
        self.assertTrue(wait_for(lambda: self.hub.states["2"]["distance"] == 11))

    def test_phone_http_api_serves_shared_readings_and_accepts_sensor_settings(self):
        sessions = phone.Sessions()
        handler = type("TestHandler", (phone.Handler,), {"hub": self.hub, "sessions": sessions})
        httpd = phone.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        headers = {"Cookie": "petcare_session=" + sessions.create("admin"), "X-PetCare": "1"}
        try:
            self.send(self.reading(10.5))
            self.assertTrue(wait_for(lambda: self.hub.states["2"]["distance"] == 10.5))
            with urllib.request.urlopen(base, timeout=2) as response:
                self.assertIn(b"<title>PetCare AI</title>", response.read())
            request = urllib.request.Request(base + "/api/status", headers=headers)
            with urllib.request.urlopen(request, timeout=2) as response:
                status = json.load(response)
            self.assertTrue(status["dashboard_source"])
            self.assertEqual(status["zones"][1]["distance_cm"], 10.5)
            request = urllib.request.Request(base + "/api/settings", headers=headers)
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertTrue(json.load(response)["dashboard_source"])
            payload = {"mapping": dict(self.desktop.mapping), "thresholds": {**self.desktop.thresholds, "2": 12}}
            request = urllib.request.Request(base + "/api/settings/sensors", headers=headers, data=json.dumps(payload).encode())
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertTrue(json.load(response)["ok"])
            self.assertEqual(self.settings.data["sensors"]["thresholds"]["2"], 12)
            self.assertEqual(self.settings.data["settings_version"], 1)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_existing_hub_handshake_translates_revision_without_changing_settings(self):
        self.bridge.stop()
        self.assertTrue(wait_for(lambda: not self.hub.connected))
        self.hub.dashboard_source = False
        self.hub._source_sensors = None
        self.settings.data["sensors"]["revision"] = 9
        self.bridge.start(self.server.port, legacy=True)
        self.assertTrue(wait_for(lambda: self.hub.acknowledged == 9))
        self.send(self.reading(10.5))
        self.assertTrue(wait_for(lambda: self.hub.states["2"]["distance"] == 10.5))
        self.assertEqual(self.settings.data["sensors"]["revision"], 9)
        self.receiver.status.emit("Listening · Raspberry Pi disconnected")
        self.assertTrue(wait_for(lambda: not self.hub.connected))

    def test_existing_hub_mismatched_assignments_do_not_receive_mislabelled_zone_data(self):
        self.bridge.stop()
        self.assertTrue(wait_for(lambda: not self.hub.connected))
        self.hub.dashboard_source = False
        self.hub._source_sensors = None
        self.settings.data["sensors"]["mapping"]["2"] = "water"
        self.bridge.start(self.server.port, legacy=True)
        self.assertTrue(wait_for(lambda: self.bridge._remote_config is not None))
        self.send(self.reading(10.5))
        self.send({"type": "GESTURE_READING", "sensor_id": 5, "distance_cm": 15,
                   "state": "WAITING", "samples": 0, "total": 60})
        self.assertTrue(wait_for(lambda: self.hub.gesture["distance"] == 15))
        self.assertIsNone(self.hub.states["2"]["distance"])
        self.assertIsNone(self.hub.acknowledged)


class LauncherTests(unittest.TestCase):
    def test_deployment_uses_existing_identity_and_only_launches_the_phone_hub(self):
        launcher = PiHubLauncher("jiating@192.0.2.10")
        with tempfile.TemporaryDirectory() as directory:
            launcher.key_path = Path(directory) / "test_identity"
            launcher.key_path.touch()
            with patch("pet_monitoring.services.pi_hub_launcher.shutil.which", side_effect=lambda program: program), \
                    patch.object(launcher.process, "start") as start, patch.object(launcher, "_read"):
                launcher.start()
                self.assertEqual(launcher.phase, "prepare")
                launcher.start()
                self.assertEqual(start.call_count, 1)
                for expected in ("upload hub", "upload web", "run"):
                    launcher._finished(0, None)
                    self.assertEqual(launcher.phase, expected)
                program, args = start.call_args.args
                self.assertEqual(program, "ssh")
                self.assertIn("BatchMode=yes", args)
                self.assertIn("StrictHostKeyChecking=yes", args)
                self.assertIn("ExitOnForwardFailure=yes", args)
                self.assertIn(f"127.0.0.1:{launcher.local_port}:127.0.0.1:5007", args)
                self.assertIn("gui_session.py", args[-1])
                self.assertIn(launcher.DATA_DIR, args[-1])
                self.assertNotIn("predict_movement", args[-1])
                self.assertNotIn("sudo", args[-1])
                self.assertNotIn("edc.env", args[-1])
        launcher.stop()


class SessionOwnershipTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("gui_session_test", Path(__file__).resolve().parents[2] / "pi_hub" / "gui_session.py")
        self.session = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.session)
        # The helper runs on Linux; emulate its signals without installing Windows handlers.
        self.session.signal = types.SimpleNamespace(SIGINT=2, SIGTERM=15, SIGHUP=1, signal=Mock())

    def test_reuses_existing_service_without_starting_or_stopping_system_services(self):
        with patch.object(self.session, "service_active", side_effect=lambda name: name == "petcare-hub"), \
                patch.object(self.session, "reuse_hub") as reuse, \
                patch.object(self.session.subprocess, "Popen") as popen:
            self.assertEqual(self.session.run_session("/tmp/test-data", 5007), 0)
            reuse.assert_called_once()
            popen.assert_not_called()

    def test_starts_one_hub_if_no_system_services_are_active(self):
        process = Mock()
        process.poll.return_value = 0
        with patch.object(self.session, "service_active", return_value=False), \
                patch.object(self.session.subprocess, "Popen", return_value=process) as popen:
            self.assertEqual(self.session.run_session("/tmp/test-data", 5007), 0)
            self.assertIn("--dashboard-source", popen.call_args.args[0])
            self.assertNotIn("predict_movement", " ".join(popen.call_args.args[0]))

    def test_active_sensor_service_prevents_a_duplicate_gpio_sender(self):
        with patch.object(self.session, "service_active", return_value=True), \
                patch.object(self.session.subprocess, "Popen") as popen:
            self.assertEqual(self.session.run_session("/tmp/test-data", 5007), 1)
            popen.assert_not_called()

    def test_ready_marker_is_buffered_and_emitted_only_once(self):
        launcher = PiHubLauncher("jiating@192.0.2.10")
        launcher.phase, launcher.local_port, launcher.web_port = "run", 23456, 23457
        ready = []
        launcher.ready.connect(lambda port, url: ready.append((port, url)))
        with patch.object(launcher.process, "readAllStandardOutput", side_effect=[b"[PETCARE_", b"HUB_READY]\r\n", b"more output"]):
            for _ in range(3):
                launcher._read()
        self.assertEqual(ready, [(23456, "http://127.0.0.1:23457")])
        launcher.stop()


if __name__ == "__main__":
    unittest.main()
