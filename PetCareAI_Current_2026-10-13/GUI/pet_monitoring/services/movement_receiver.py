"""Bounded newline-delimited JSON receiver; never touches widgets or SQLite."""
import json
import math
import os
import socket
import threading
import time
from PySide6.QtCore import QObject, Signal
from pet_monitoring.security.edc_transport import EDCCodec, MAX_WIRE_BYTES, load_json
from pet_monitoring.security.edc_settings import has_saved_keys, load_codec

CLASSES = {"PASS_BY", "APPROACH_STAY", "AUTH_GESTURE"}


def parse_message(line):
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object")
    kind = message.get("type")
    if kind not in {"IDLE", "TRIGGERED", "RECORDING", "MOVEMENT_RESULT", "CONFIG_ACK", "SENSOR_READING", "ZONE_EVENT", "GESTURE_READING"}:
        raise ValueError("Unknown message type")
    result = {"type": kind}
    if kind == "GESTURE_READING":
        if str(message.get("sensor_id")) != "5":
            raise ValueError("Gesture telemetry must be Sensor 5")
        distance = message.get("distance_cm")
        if distance is not None and (isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isfinite(distance) or not 0 < distance <= 200):
            raise ValueError("Invalid gesture distance")
        state = message.get("state")
        if state not in {"WAITING", "RECORDING", "CLASSIFYING", "WAIT_CLEAR"}:
            raise ValueError("Invalid gesture state")
        samples, total = message.get("samples", 0), message.get("total", 60)
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (samples, total)) or not 2 <= total <= 10000 or not 0 <= samples <= total:
            raise ValueError("Invalid recording progress")
        result.update(sensor_id="5", distance_cm=distance, state=state, samples=samples, total=total)
        return result
    if kind in {"CONFIG_ACK", "SENSOR_READING", "ZONE_EVENT"}:
        revision = message.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("A positive configuration revision is required")
        result["revision"] = revision
        if kind == "CONFIG_ACK":
            return result
        sensor_id = str(message.get("sensor_id"))
        if sensor_id not in {"1", "2", "3", "4"}:
            raise ValueError("Pet zone sensor must be 1 to 4")
        result["sensor_id"] = sensor_id
        if kind == "SENSOR_READING":
            distance = message.get("distance_cm")
            if distance is not None and (isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isfinite(distance) or not 0 < distance <= 400):
                raise ValueError("Invalid distance")
            presence = message.get("presence")
            if not isinstance(presence, bool) or (distance is None and presence):
                raise ValueError("Valid proximity state required")
            result.update(distance_cm=distance, presence=presence)
        else:
            event_id = message.get("event_id")
            duration = message.get("duration_seconds")
            if not isinstance(event_id, str) or not 1 <= len(event_id) <= 100:
                raise ValueError("Zone event requires a unique event ID")
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 <= duration <= 86400:
                raise ValueError("Invalid zone duration")
            result.update(event_id=event_id, duration_seconds=float(duration))
        return result
    if "sensor_id" in message and str(message["sensor_id"]) != "5":
        raise ValueError("Gesture messages are reserved for Sensor 5")
    if kind == "MOVEMENT_RESULT":
        prediction = message.get("prediction", message.get("class"))
        confidence = message.get("confidence")
        if prediction not in CLASSES:
            raise ValueError("Unknown movement class")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("Confidence must be a number from 0 to 1")
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Confidence must be a number from 0 to 1")
        result.update(prediction=prediction, confidence=float(confidence))
    return result


class MovementReceiver(QObject):
    inspection = Signal(dict)
    received = Signal(dict)
    status = Signal(str)

    def __init__(self, parent=None, codec=None):
        super().__init__(parent)
        self._thread = None
        self._stop = threading.Event()
        self.port = None
        self._config_lock = threading.Lock()
        self._config = None
        self._codec = codec
        self._secure = codec is not None
        self._last_inspection = -float("inf")

    def set_configuration(self, configuration):
        with self._config_lock:
            self._config = json.dumps(configuration, allow_nan=False).encode("utf-8") + b"\n"

    def start(self, host="0.0.0.0", port=5005):
        self.stop()
        # Saved keys enable EDC on the existing port without per-launch flags.
        saved_keys = has_saved_keys()
        self._secure = saved_keys or self._codec is not None or str(port) == "5006" or os.environ.get("PETCARE_EDC") == "1"
        if self._secure and self._codec is None:
            try:
                self._codec = load_codec() if saved_keys else EDCCodec.from_environment()
            except ValueError:
                self.status.emit("Cannot enable EDC: run main.py --setup-edc or check matching environment keys")
                return False
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.bind((host, int(port)))
            server.listen(4)
            server.settimeout(.2)
        except (OSError, ValueError, OverflowError):
            server.close()
            self.status.emit("Cannot listen: check the address and port")
            return False
        self.port = server.getsockname()[1]
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(server,), daemon=True,
                                        name="movement-tcp")
        self._thread.start()
        mode = "experimental EDC + HMAC" if self._secure else "insecure plaintext"
        self.status.emit(f"Listening on {host}:{self.port} · {mode} · waiting for Raspberry Pi")
        return True

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None
        self.status.emit("Receiver stopped")

    def _receive_secure(self, line):
        # Sampling bounds queued Qt snapshots; every packet is still decoded normally.
        capture = time.monotonic() - self._last_inspection >= 1.0
        trace = {} if capture else None
        if capture:
            self._last_inspection = time.monotonic()
        try:
            packet = load_json(line)
            message = self._codec.decode(packet, trace=trace) if capture else self._codec.decode(packet)
            validated = parse_message(json.dumps(message, allow_nan=False))
            accepted = {**message, **validated}
            if trace is not None:
                trace["checks"]["application"] = "Passed: application validation; emitted for delivery"
                trace["accepted_message"] = accepted
                trace["outcome"] = "Accepted"
            self.received.emit(accepted)
        except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
            if trace is not None:
                trace.setdefault("stage", "packet")
                trace.setdefault("checks", {})
                trace.setdefault("received_at", time.time())
                trace["outcome"] = "Rejected"
                trace.setdefault("failed_stage", trace["stage"])
                trace.setdefault("error", str(exc)[:240])
                trace["checks"].setdefault(trace["stage"], "Rejected: " + str(exc)[:240])
            raise
        finally:
            if trace is not None:
                # Never mutate a snapshot after emitting it across threads.
                self.inspection.emit(trace)

    def _run(self, server):
        with server:
            while not self._stop.is_set():
                try:
                    client, address = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self.status.emit(f"Raspberry Pi connected · {address[0]}")
                with client:
                    client.settimeout(.2)
                    buffer = b""
                    max_line = MAX_WIRE_BYTES if self._secure else 4096
                    sent_configuration = None
                    while not self._stop.is_set():
                        try:
                            with self._config_lock:
                                configuration = self._config
                            if configuration is not None and configuration != sent_configuration:
                                outgoing = configuration
                                if self._secure:
                                    packet = self._codec.encode(load_json(configuration))
                                    outgoing = json.dumps(packet, allow_nan=False).encode("utf-8") + b"\n"
                                client.sendall(outgoing)
                                sent_configuration = configuration
                            data = client.recv(4096)
                            if not data:
                                break
                            buffer += data
                            while b"\n" in buffer:
                                line, buffer = buffer.split(b"\n", 1)
                                if len(line) > max_line:
                                    raise ValueError("Message too large")
                                if line.strip():
                                    if self._secure:
                                        self._receive_secure(line)
                                    else:
                                        self.received.emit(parse_message(line.decode("utf-8")))
                            if len(buffer) > max_line:
                                raise ValueError("Message too large")
                        except socket.timeout:
                            continue
                        except (ValueError, UnicodeError, OSError, TypeError, RecursionError) as exc:
                            print(f"[RECEIVER ERROR] {type(exc).__name__}: {exc}", flush=True)
                            self.status.emit(f"Invalid message: {exc} · waiting for reconnect")
                            break
                if not self._stop.is_set():
                    self.status.emit("Listening · Raspberry Pi disconnected")
