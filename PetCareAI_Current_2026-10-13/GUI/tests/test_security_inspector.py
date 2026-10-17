"""Actual decode snapshots, rejection boundaries, and read-only GUI navigation."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import base64
import json
import socket
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from pet_monitoring.security import edc_transport as edc
from pet_monitoring.services.movement_receiver import MovementReceiver
from pet_monitoring.ui.security_panel import SecurityInspector
from pet_monitoring.ui.theme import apply_theme

APP = QApplication.instance() or QApplication([])
KEY = "INSPECTORTEST"
AUTH = b"T" * 32
MESSAGE = {"type": "SENSOR_READING", "sensor_id": 2, "revision": 1,
           "distance_cm": 10.5, "presence": True}


class InspectorTests(unittest.TestCase):
    def setUp(self):
        self.sender = edc.EDCCodec(KEY, AUTH)
        self.receiver = MovementReceiver(codec=edc.EDCCodec(KEY, AUTH))
        self.snapshots, self.messages = [], []
        self.receiver.inspection.connect(self.snapshots.append)
        self.receiver.received.connect(self.messages.append)
        self.page = SecurityInspector(self.receiver)

    def tearDown(self):
        self.receiver.stop()
        APP.processEvents()
        self.page.close()
        self.page.deleteLater()

    def receive(self, packet):
        self.receiver._last_inspection = -float("inf")
        self.receiver._receive_secure(json.dumps(packet).encode())
        APP.processEvents()
        return self.snapshots[-1]

    def test_actual_intermediates_and_frozen_navigation_decode_once(self):
        packet = self.sender.encode(MESSAGE)
        with patch.object(self.receiver._codec, "decode", wraps=self.receiver._codec.decode) as decode:
            snapshot = self.receive(packet)
            original = json.dumps(snapshot, sort_keys=True)
            self.assertEqual(snapshot["outcome"], "Accepted")
            self.assertEqual(snapshot["ciphertext"], packet["ciphertext"])
            self.assertEqual(snapshot["tag"], packet["tag"])
            raw = base64.b32decode(snapshot["base32"] + "=" * (-len(snapshot["base32"]) % 8))
            self.assertEqual(raw.decode(), snapshot["json_text"])
            self.assertEqual(json.loads(raw)["message"], MESSAGE)
            transmitted = packet["ciphertext"].split("-")
            for block in snapshot["blocks"]:
                start = (block["block"] - 1) * 4
                self.assertEqual(block["transmitted"], transmitted[start:start + 4])
                self.assertCountEqual(block["transmitted"], block["restored"])
            restored = sum((b["restored"] for b in snapshot["blocks"]), [])
            for sample in snapshot["samples"]:
                self.assertEqual(sample["character"], snapshot["base32"][sample["position"] - 1])
                self.assertEqual(sample["token"], restored[sample["position"] - 1])
            for theme in ("Light", "Dark"):
                apply_theme(APP, theme)
                for stage in range(6):
                    self.page.select_stage(stage)
                    self.assertEqual(self.page.snapshot, snapshot)
            self.assertTrue(self.page.paused)
            self.assertEqual(decode.call_count, 1)
            self.assertEqual(json.dumps(snapshot, sort_keys=True), original)
            for forbidden in (KEY, AUTH.hex(), "key_row", "key_column", '"matrix"'):
                self.assertNotIn(forbidden, original)
            self.page.accept_snapshot({"outcome": "must not replace frozen packet"})
            self.assertEqual(self.page.snapshot, snapshot)
            self.page.toggle_capture()
            self.page._last_refresh = -float("inf")
            self.receive(self.sender.encode({"type": "IDLE"}))
            self.assertEqual(self.page.snapshot["accepted_message"]["type"], "IDLE")
            self.assertEqual(decode.call_count, 2)

    def test_hmac_failure_never_decrypts_or_delivers(self):
        packet = {**self.sender.encode(MESSAGE), "tag": "0" * 64}
        with patch.object(edc, "decrypt", side_effect=AssertionError("Must not decrypt")) as decrypt:
            with self.assertRaisesRegex(ValueError, "HMAC"):
                self.receive(packet)
            decrypt.assert_not_called()
        snapshot = self.snapshots[-1]
        self.assertEqual(snapshot["failed_stage"], "hmac")
        self.assertIn("Rejected before decryption", snapshot["error"])
        self.assertNotIn("base32", snapshot)
        self.assertFalse(self.messages)
        APP.processEvents()
        self.page.select_stage(1)
        self.assertIn("Rejected before decryption", self.page.details.toPlainText())

    def test_replay_rejected_before_second_decrypt(self):
        packet = self.sender.encode(MESSAGE)
        self.receive(packet)
        with patch.object(edc, "decrypt", side_effect=AssertionError("Replay reached decrypt")):
            with self.assertRaisesRegex(ValueError, "Replay"):
                self.receive(packet)
        snapshot = self.snapshots[-1]
        self.assertEqual(snapshot["failed_stage"], "ciphertext_replay")
        self.assertEqual(len(self.messages), 1)
        self.assertFalse(snapshot["decrypted"])

    def test_timestamp_and_application_failure_report_post_decryption(self):
        with patch.object(edc.time, "time", return_value=time.time() - 121):
            expired = self.sender.encode(MESSAGE)
        with self.assertRaisesRegex(ValueError, "Expired"):
            self.receive(expired)
        expired_snapshot = self.snapshots[-1]
        self.assertEqual(expired_snapshot["failed_stage"], "timestamp")
        self.assertTrue(expired_snapshot["decrypted"])
        self.assertIn("json_text", expired_snapshot)
        with self.assertRaisesRegex(ValueError, "Unknown message"):
            self.receive(self.sender.encode({"type": "INVALID"}))
        snapshot = self.snapshots[-1]
        self.assertEqual(snapshot["failed_stage"], "application")
        self.assertTrue(snapshot["decrypted"])
        self.assertFalse(self.messages)
        self.assertNotIn("accepted_message", snapshot)
        # Display the actual application rejection rather than a pre-decryption claim.
        self.page._last_refresh = -float("inf")
        APP.processEvents()
        self.page.select_stage(5)
        self.assertIn("application", self.page.details.toPlainText())
        self.assertNotIn("Rejected before decryption", self.page.details.toPlainText())

    def test_sampling_does_not_throttle_delivery(self):
        for _ in range(10):
            self.receiver._receive_secure(json.dumps(self.sender.encode(MESSAGE)).encode())
        self.assertEqual(len(self.messages), 10)
        self.assertEqual(len(self.snapshots), 1)

    def test_tcp_signal_delivers_snapshot_on_gui_thread(self):
        with patch("pet_monitoring.services.movement_receiver.has_saved_keys", return_value=False):
            self.assertTrue(self.receiver.start("127.0.0.1", 0))
        seen_threads = []
        original_render = self.page.render
        def render():
            seen_threads.append(QThread.currentThread())
            original_render()
        self.page.render = render
        with socket.create_connection(("127.0.0.1", self.receiver.port), timeout=2) as client:
            client.sendall(json.dumps(self.sender.encode(MESSAGE)).encode() + b"\n")
            deadline = time.monotonic() + 3
            while self.page.snapshot is None and time.monotonic() < deadline:
                APP.processEvents()
                QTest.qWait(10)
            self.assertIsNotNone(self.page.snapshot)
            self.assertEqual(self.page.snapshot["accepted_message"]["distance_cm"], 10.5)
            self.assertTrue(seen_threads)
            self.assertTrue(all(thread == APP.thread() for thread in seen_threads))


if __name__ == "__main__":
    unittest.main()
