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


def _text(value, limit, name):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Invalid {name}")
    return value


def _number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Invalid {name}")
    return value


def parse_hub_sync(message):
    """Pet details and hardware setup pushed by the Pi hub (see services/hub_sync.py)."""
    pet, sensors = message.get("pet"), message.get("sensors")
    if not isinstance(pet, dict) or not isinstance(sensors, dict) or set(sensors) != {"1", "2", "3", "4", "5"}:
        raise ValueError("Invalid hub sync")
    clean_pet = {"name": _text(pet.get("name"), 40, "pet name"), "animal": _text(pet.get("animal"), 40, "animal"),
                 "breed": _text(pet.get("breed"), 40, "breed"), "avatar": _text(pet.get("avatar"), 20, "avatar"),
                 "birthday": _text(pet.get("birthday"), 10, "birthday"), "sex": _text(pet.get("sex"), 6, "sex"),
                 "weight_kg": None if pet.get("weight_kg") is None else _number(pet["weight_kg"], 0, 200, "weight"),
                 "baseline_sleep_hours": _number(pet.get("baseline_sleep_hours"), .1, 24, "usual sleep"),
                 "baseline_toilet_visits": _number(pet.get("baseline_toilet_visits"), .1, 100, "usual litter visits")}
    if not clean_pet["name"] or clean_pet["sex"] not in {"", "female", "male"}:
        raise ValueError("Invalid pet details")
    if clean_pet["birthday"]:
        from datetime import date
        date.fromisoformat(clean_pet["birthday"])
    clean_sensors = {}
    for key, value in sensors.items():
        if not isinstance(value, dict):
            raise ValueError("Invalid sensor sync")
        location, pins = value.get("location"), value.get("pins")
        if not isinstance(location, dict) or not isinstance(pins, list) or len(pins) != 2 or not all(type(p) is int for p in pins):
            raise ValueError("Invalid sensor sync")
        entry = {"pins": pins, "location": {"x": _number(location.get("x"), -1, 11, "x"), "z": _number(location.get("z"), -1, 16, "z"),
                                            "facing": _number(location.get("facing"), -180, 180, "facing"),
                                            "mount": location.get("mount") if location.get("mount") in {"floor", "raised"} else "raised",
                                            "note": _text(location.get("note"), 60, "location note")}}
        if key == "5":
            if value.get("role") != "entrance":
                raise ValueError("Sensor 5 must stay the entrance sensor")
            entry["role"] = "entrance"
        else:
            if value.get("role") not in {"eat", "sleep", "water", "toilet"}:
                raise ValueError("Invalid sensor role")
            calibration = value.get("calibration")
            if calibration is not None:
                if not isinstance(calibration, dict):
                    raise ValueError("Invalid calibration")
                full, empty = _number(calibration.get("full_cm"), 0, 400, "full"), _number(calibration.get("empty_cm"), 0, 400, "empty")
                if not 0 < full < empty:
                    raise ValueError("Invalid calibration")
                calibration = {"full_cm": float(full), "empty_cm": float(empty)}
            entry.update(role=value["role"], threshold_cm=_number(value.get("threshold_cm"), 1, 400, "threshold"), calibration=calibration)
        clean_sensors[key] = entry
    revision, total = message.get("revision"), message.get("pets_total")
    if type(revision) is not int or revision < 1 or type(total) is not int or total < 1:
        raise ValueError("Invalid hub sync")
    return {"revision": revision, "pets_total": total, "pet": clean_pet, "sensors": clean_sensors}


def parse_message(line):
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object")
    kind = message.get("type")
    if kind not in {"IDLE", "TRIGGERED", "RECORDING", "MOVEMENT_RESULT", "CONFIG_ACK", "SENSOR_READING", "ZONE_EVENT", "GESTURE_READING", "HUB_SYNC"}:
        raise ValueError("Unknown message type")
    result = {"type": kind}
    if kind == "HUB_SYNC":
        return {**result, **parse_hub_sync(message)}
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
