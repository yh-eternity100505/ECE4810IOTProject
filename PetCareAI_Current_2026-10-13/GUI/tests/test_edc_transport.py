import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import base64
import json
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pet_monitoring.controller import MonitoringController
from pet_monitoring.hub_controller import HubController
from pet_monitoring.movement_controller import MovementController
from pet_monitoring.security import edc_transport as edc
from pet_monitoring.services.database_service import DatabaseService
from pet_monitoring.services.movement_receiver import MovementReceiver

APP = QApplication.instance() or QApplication([])
REFERENCE = json.loads((Path(__file__).parent / "fixtures" / "pi_edc_vectors.json").read_text())
KEY = REFERENCE["edc_key"]
AUTH = bytes.fromhex(REFERENCE["hmac_key_hex"])


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        QTest.qWait(10)
    return predicate()


def wire(packet):
    return json.dumps(packet, allow_nan=False).encode() + b"\n"


class CodecTests(unittest.TestCase):
    def test_supplied_pi_vectors_decode_and_encode_identically(self):
        receiver = edc.EDCCodec(KEY, AUTH)
        for vector in REFERENCE["vectors"]:
            with self.subTest(kind=vector["message"]["type"]), patch("time.time", return_value=REFERENCE["sent_at"]):
                self.assertEqual(receiver.decode(vector["packet"]), vector["message"])
                with patch("secrets.token_hex", return_value=vector["nonce"]):
                    self.assertEqual(edc.EDCCodec(KEY, AUTH).encode(vector["message"]), vector["packet"])

    def test_tampering_and_wrong_hmac_key_never_decrypt(self):
        packet = REFERENCE["vectors"][0]["packet"]
        cases = [(edc.EDCCodec(KEY, AUTH), {**packet, "ciphertext": "FIT1" + packet["ciphertext"][4:]}),
                 (edc.EDCCodec(KEY, AUTH), {**packet, "tag": "0" * 64}),
                 (edc.EDCCodec(KEY, b"X" * 32), packet),
                 (edc.EDCCodec(KEY, AUTH), {**packet, "v": 2}),
                 (edc.EDCCodec(KEY, AUTH), {"type": "CONFIG_ACK", "revision": 3})]
        for codec, candidate in cases:
            with patch.object(edc, "decrypt", side_effect=AssertionError("Must not decrypt")) as decrypt:
                with self.assertRaises(ValueError):
                    codec.decode(candidate)
                decrypt.assert_not_called()

    def test_wrong_edc_key_and_authenticated_invalid_ciphertext(self):
        packet = REFERENCE["vectors"][0]["packet"]
        with self.assertRaises(ValueError):
            edc.EDCCodec("WRONGKEY", AUTH).decode(packet)
        codec = edc.EDCCodec(KEY, AUTH)
        with self.assertRaises(ValueError):
            codec.decode({**packet, "ciphertext": "BAD1", "tag": codec._tag("BAD1")})

    def test_replay_and_expiry(self):
        codec = edc.EDCCodec(KEY, AUTH)
        packet = REFERENCE["vectors"][0]["packet"]
        with patch("time.time", return_value=REFERENCE["sent_at"]):
            codec.decode(packet)
            with patch.object(edc, "decrypt", side_effect=AssertionError("Replay reached decrypt")):
                with self.assertRaisesRegex(ValueError, "Replay"):
                    codec.decode(packet)
            # Different ciphertext carrying an already accepted authenticated nonce.
            with patch("secrets.token_hex", return_value=REFERENCE["vectors"][0]["nonce"]):
                duplicate_nonce = codec.encode({"type": "IDLE", "sensor_id": 5})
            with self.assertRaisesRegex(ValueError, "Replay nonce"):
                codec.decode(duplicate_nonce)
        for offset in (-121, 121):
            with patch("time.time", return_value=REFERENCE["sent_at"] + offset):
                with self.assertRaisesRegex(ValueError, "Expired or future"):
                    edc.EDCCodec(KEY, AUTH).decode(packet)

    def test_authenticated_metadata_and_json_validation(self):
        codec = edc.EDCCodec(KEY, AUTH)
        inner = {"v": 1, "sent_at": int(time.time()), "nonce": "A" * 32,
                 "message": {"type": "IDLE", "sensor_id": 5}}
        for changes in ({"v": True}, {"sent_at": True}, {"nonce": "bad"}, {"message": []}):
            raw = json.dumps({**inner, **changes}).encode()
            cipher = edc.encrypt(base64.b32encode(raw).decode().rstrip("="), KEY)
            with self.assertRaises(ValueError):
                codec.decode({"type": "EDC_PACKET", "v": 1, "ciphertext": cipher, "tag": codec._tag(cipher)})
        for raw in (b'{"v":1,"v":2}', b'{"x":NaN}'):
            with self.assertRaises(ValueError):
                edc.load_json(raw)

    def test_missing_keys_fail_closed(self):
        with patch.dict(os.environ, {"PETCARE_EDC": "1"}, clear=True), patch(
                "pet_monitoring.services.movement_receiver.has_saved_keys", return_value=False):
            receiver = MovementReceiver()
            messages = []
            receiver.status.connect(messages.append)
            self.assertFalse(receiver.start("127.0.0.1", 0))
            self.assertIsNone(receiver._thread)
            self.assertIn("Cannot enable EDC", messages[-1])


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseService(Path(self.tmp.name) / "test.db")
        self.controller = MonitoringController(self.db)
        self.movement = MovementController(self.controller)
        self.receiver = self.movement.receiver
        self.hub = HubController(self.controller, self.receiver)
        self.receiver._codec = edc.EDCCodec(KEY, AUTH)
        self.pi = edc.EDCCodec(KEY, AUTH)
        self.messages, self.statuses = [], []
        self.receiver.received.connect(self.messages.append)
        self.receiver.status.connect(self.statuses.append)
        self.hub.set_mode("Live Pi")
        self.assertTrue(self.receiver.start("127.0.0.1", 0))

    def tearDown(self):
        self.hub.close()
        self.movement.close()
        self.controller.close()
        self.controller.executor.shutdown(wait=True)
        APP.processEvents()
        self.db.close()
        self.tmp.cleanup()

    def connect_pi(self):
        client = socket.create_connection(("127.0.0.1", self.receiver.port), timeout=2)
        buffer = b""
        while b"\n" not in buffer:
            buffer += client.recv(4096)
        config = self.pi.decode(edc.load_json(buffer.split(b"\n", 1)[0]))
        self.assertEqual(config["type"], "SET_CONFIG")
        return client, config["revision"]

    def test_handshake_fragmentation_sensor_zone_and_knn_handlers(self):
        client, revision = self.connect_pi()
        with client:
            messages = [
                {"type": "CONFIG_ACK", "revision": revision},
                {"type": "SENSOR_READING", "sensor_id": 2, "revision": revision,
                 "distance_cm": 10.5, "presence": True, "timestamp": "original timestamp"},
                {"type": "ZONE_EVENT", "sensor_id": 2, "revision": revision,
                 "duration_seconds": 4.25, "event_id": "verified-visit"},
                {"type": "MOVEMENT_RESULT", "sensor_id": 5, "prediction": "PASS_BY", "confidence": .94}]
            data = b"".join(wire(self.pi.encode(message)) for message in messages)
            client.sendall(data[:17])
            client.sendall(data[17:])
            self.assertTrue(wait_for(lambda: len(self.messages) == 4))
            self.assertEqual(self.hub.states["2"]["distance"], 10.5)
            self.assertEqual(self.messages[1]["timestamp"], "original timestamp")
            self.assertEqual(self.messages[3]["sensor_id"], 5)
            self.assertEqual(self.movement.prediction, "PASS_BY")
            self.assertEqual(len(self.hub.history()), 1)

    def test_replay_reconnect_stop_and_malformed_connections(self):
        client, revision = self.connect_pi()
        packet = self.pi.encode({"type": "CONFIG_ACK", "revision": revision})
        with client:
            client.sendall(wire(packet))
            self.assertTrue(wait_for(lambda: len(self.messages) == 1))
        self.receiver.stop()
        self.assertTrue(self.receiver.start("127.0.0.1", 0))
        bad_packets = [wire(packet), b'{"type":"IDLE"}\n', b'not-json\n',
                       wire({**packet, "tag": "0" * 64}), b"x" * (edc.MAX_WIRE_BYTES + 1)]
        for bad in bad_packets:
            previous = sum("Invalid message" in value for value in self.statuses)
            client, _ = self.connect_pi()
            with client:
                client.sendall(bad)
                self.assertTrue(wait_for(lambda: sum("Invalid message" in value for value in self.statuses) > previous))
            self.assertEqual(len(self.messages), 1)
        client, revision = self.connect_pi()
        with client:
            client.sendall(wire(self.pi.encode({"type": "CONFIG_ACK", "revision": revision})))
            self.assertTrue(wait_for(lambda: len(self.messages) == 2))
