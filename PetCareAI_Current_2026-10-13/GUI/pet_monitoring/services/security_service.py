"""Encrypt-then-MAC transport. Every decrypt entry point verifies first."""
import base64
import hashlib
import hmac
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from uuid import uuid4
from pet_monitoring.models.secure_packet import SecurePacket, SecurityEvent, PacketResult, SecurityMetrics
from pet_monitoring.models.records import AREAS
from pet_monitoring.security.edc_adapter import DemoCipherBackend


class SecurityUnavailable(RuntimeError):
    pass


class SecurityService:
    def __init__(self, backend=None, event_sink=None, authentication_key=None):
        self.event_sink = event_sink
        self.metrics = SecurityMetrics()
        if authentication_key is not None and (not isinstance(authentication_key, bytes) or len(authentication_key) < 32):
            raise ValueError("A separately provisioned HMAC key must contain at least 32 bytes")
        self._authentication_key = authentication_key if authentication_key is not None else os.urandom(32)
        self._seen = {}
        self.available = True
        try:
            self.backend = backend if backend is not None else DemoCipherBackend()
        except Exception:
            self.backend = None
            self.available = False
        self.last_verification = ""
        self.last_reason = "Waiting for packets"
        self.last_encryption_ms = 0
        self.last_result = None

    @property
    def algorithm(self):
        return self.backend.algorithm if self.backend else "Security Module Offline"

    def _event(self, kind, packet=None, status="", **latency):
        if self.event_sink:
            self.event_sink(SecurityEvent(kind, datetime.now().isoformat(timespec="seconds"),
                packet.packet_id if packet else "", packet.source_id if packet else "", status, **latency))

    @staticmethod
    def serialize(data):
        return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def encrypt_message(self, data, nonce):
        if not self.available or self.backend is None:
            raise SecurityUnavailable("Security Module Offline")
        plain = self.serialize(data)
        if len(plain) > 32_768:
            raise ValueError("Sensor payload too large")
        cipher = self.backend.encrypt(plain, nonce)
        if not isinstance(cipher, bytes) or not 0 < len(cipher) <= 65_536:
            raise ValueError("Invalid cipher backend output")
        return cipher

    def create_authentication_tag(self, packet):
        return hmac.new(self._authentication_key, self.serialize(packet.authenticated_fields()), hashlib.sha256).hexdigest()

    def verify_authentication_tag(self, packet):
        return hmac.compare_digest(self.create_authentication_tag(packet), packet.authentication_tag)

    def create_secure_packet(self, data, source_id):
        started = time.perf_counter()
        nonce = os.urandom(16)
        cipher = self.encrypt_message(data, nonce)
        packet = SecurePacket(str(uuid4()), source_id, datetime.now(timezone.utc).isoformat(),
                              base64.b64encode(cipher).decode(), "", self.algorithm,
                              base64.b64encode(nonce).decode())
        packet.authentication_tag = self.create_authentication_tag(packet)
        self.last_encryption_ms = (time.perf_counter() - started) * 1000
        self.metrics.encrypted_packets += 1
        self.metrics.encryption_total_ms += self.last_encryption_ms
        return packet

    def _parse_packet(self, wire):
        # Use serialised network bytes in the controller, even for local simulation.
        if isinstance(wire, SecurePacket):
            wire = wire.to_wire()
        if isinstance(wire, bytes):
            if len(wire) > 100_000:
                raise ValueError("Packet too large")
            fields = json.loads(wire.decode("utf-8"))
        elif isinstance(wire, dict):
            fields = wire.copy()
        else:
            raise ValueError("Invalid packet structure")
        required = {"packet_id", "source_id", "timestamp", "ciphertext", "authentication_tag", "algorithm", "nonce", "version"}
        if not isinstance(fields, dict) or set(fields) != required:
            raise ValueError("Invalid packet fields")
        if any(not isinstance(fields[k], str) for k in required - {"version"}):
            raise ValueError("Invalid packet field types")
        if type(fields["version"]) is not int or fields["version"] != 1:
            raise ValueError("Unsupported packet version")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", fields["packet_id"]):
            raise ValueError("Invalid packet identifier")
        if fields["source_id"] not in {"SENSOR_" + key.upper() for key in AREAS}:
            raise ValueError("Unknown source")
        if len(fields["timestamp"]) > 50 or fields["algorithm"] != self.algorithm:
            raise ValueError("Invalid packet metadata")
        if not re.fullmatch(r"[0-9a-f]{64}", fields["authentication_tag"]):
            raise ValueError("Missing or invalid authentication tag")
        if len(fields["ciphertext"]) > 90_000 or not fields["ciphertext"]:
            raise ValueError("Invalid ciphertext")
        if len(fields["nonce"]) > 32:
            raise ValueError("Invalid nonce")
        cipher = base64.b64decode(fields["ciphertext"], validate=True)
        nonce = base64.b64decode(fields["nonce"], validate=True)
        if not cipher or len(cipher) > 65_536 or len(nonce) != 16:
            raise ValueError("Invalid encrypted packet")
        packet = SecurePacket(**fields)
        return packet

    @staticmethod
    def validate_payload(data, source_id):
        if not isinstance(data, dict) or set(data) != {"schema", "pet_id", "pet_name", "reading"}:
            raise ValueError("Invalid recovered sensor payload")
        if type(data["schema"]) is not int or data["schema"] != 1:
            raise ValueError("Invalid payload version")
        if not isinstance(data["pet_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", data["pet_id"]):
            raise ValueError("Invalid pet identifier")
        if not isinstance(data["pet_name"], str) or not 0 < len(data["pet_name"]) <= 80:
            raise ValueError("Invalid pet name")
        r = data["reading"]
        keys = {"sensor_id", "sensor_name", "distance_cm", "detected", "timestamp", "connection_status", "level_low"}
        if not isinstance(r, dict) or set(r) != keys:
            raise ValueError("Invalid reading fields")
        if not isinstance(r["sensor_id"], str) or r["sensor_id"] not in AREAS:
            raise ValueError("Unknown sensor")
        if r["sensor_name"] != AREAS[r["sensor_id"]] or source_id != "SENSOR_" + r["sensor_id"].upper():
            raise ValueError("Sensor source mismatch")
        if type(r["detected"]) is not bool or (r["level_low"] is not None and type(r["level_low"]) is not bool):
            raise ValueError("Invalid reading flags")
        if not isinstance(r["timestamp"], str) or len(r["timestamp"]) > 50:
            raise ValueError("Invalid sample timestamp")
        datetime.fromisoformat(r["timestamp"])
        if r["connection_status"] not in ("Connected", "Offline", "Invalid data"):
            raise ValueError("Invalid connection status")
        distance = r["distance_cm"]
        if distance is not None and (type(distance) not in (float, int) or not math.isfinite(distance) or not 0 <= distance <= 500):
            raise ValueError("Invalid sensor distance")
        if r["connection_status"] == "Connected" and distance is None:
            raise ValueError("Missing sensor distance")
        return data

    def process_secure_packet(self, wire):
        """Reject malformed/HMAC/replay packets without calling backend.decrypt."""
        started = time.perf_counter()
        self.metrics.total_packets += 1
        verification_ms = decryption_ms = 0.0
        packet = None
        reason = "PACKET_REJECTED_MALFORMED"
        try:
            if not self.available or self.backend is None:
                reason = "SECURITY_MODULE_OFFLINE"
                raise SecurityUnavailable()
            packet = self._parse_packet(wire)
            verification_start = time.perf_counter()
            valid = self.verify_authentication_tag(packet)
            verification_ms = (time.perf_counter() - verification_start) * 1000
            if not valid:
                reason = "PACKET_REJECTED_HMAC_FAILURE"
                raise ValueError()
            packet.verified = True
            self.metrics.verified_packets += 1
            reason = "PACKET_REJECTED_REPLAY"
            timestamp = datetime.fromisoformat(packet.timestamp)
            if timestamp.tzinfo is None:
                raise ValueError()
            now = time.time()
            self._seen = {key: expiry for key, expiry in self._seen.items() if expiry >= now}
            age = now - timestamp.timestamp()
            if age > 300 or age < -30 or packet.packet_id in self._seen:
                raise ValueError()
            # Remember authenticated packet IDs even if later decryption/data validation fails.
            self._seen[packet.packet_id] = timestamp.timestamp() + 300
            self.last_verification = datetime.now().isoformat(timespec="seconds")
            self._event("PACKET_VERIFIED", packet, "PASSED", verification_ms=verification_ms)
            reason = "PACKET_REJECTED_DECRYPTION_FAILURE"
            decrypt_started = time.perf_counter()
            try:
                plain = self.backend.decrypt(base64.b64decode(packet.ciphertext), base64.b64decode(packet.nonce))
            finally:
                decryption_ms = (time.perf_counter() - decrypt_started) * 1000
                self.metrics.decrypted_packets += 1
                self.metrics.decryption_total_ms += decryption_ms
            reason = "PACKET_REJECTED_INVALID_PAYLOAD"
            data = self.validate_payload(json.loads(plain.decode("utf-8")), packet.source_id)
            total_ms = (time.perf_counter() - started) * 1000
            self.metrics.accepted_packets += 1
            self.metrics.verification_total_ms += verification_ms
            self.last_reason = "PASSED"
            self._event("PACKET_DECRYPTED", packet, "PASSED", encryption_ms=self.last_encryption_ms,
                        verification_ms=verification_ms, decryption_ms=decryption_ms, total_ms=total_ms)
            self.last_result = PacketResult(True, "PASSED", data, packet, verification_ms, decryption_ms, total_ms)
        except Exception:
            # Never log exception text from a custom cipher: it might contain a key.
            self.metrics.rejected_packets += 1
            self.metrics.verification_total_ms += verification_ms
            self.last_reason = reason
            total_ms = (time.perf_counter() - started) * 1000
            self._event(reason, packet, "REJECTED", verification_ms=verification_ms,
                        decryption_ms=decryption_ms, total_ms=total_ms)
            self.last_result = PacketResult(False, reason, None, packet, verification_ms, decryption_ms, total_ms)
        return self.last_result

    def decrypt_message(self, packet):
        # This public convenience method also obeys verify-before-decrypt.
        return self.process_secure_packet(packet)
