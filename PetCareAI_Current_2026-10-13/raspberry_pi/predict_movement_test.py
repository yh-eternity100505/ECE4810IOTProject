"""One-Pi pet hub: four sequential zone inputs and one gesture KNN input.

Run: python3 predict_movement_test.py --gui-host LAPTOP_IPV4 --edc
EDC and HMAC are included in this file; no local EDC modules are required.
EDC mode uses the laptop bridge on port 5006; default plaintext on 5005 is insecure.
GPIO uses physical BOARD numbers. See PI_SETUP.md before connecting sensors.
"""
import argparse
import json
import math
import queue
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import base64
import hmac
import os
import re
import secrets
from collections import deque

# Original educational EDC transformations, embedded to keep the Pi script standalone.
# HMAC authenticates ciphertext; the codec checks timestamps and packet replay.
BASE_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
ROW_LABELS = ['ECE', 'FIT', 'TRC', 'MEC', 'CHE', 'CIV']
BLOCK_SIZE = 4
AUTH_CONTEXT = b'EDC-HMAC-v1\x00'

def heading(title):
    print('\n' + '=' * 70)
    print(title)
    print('=' * 70)

def print_matrix(matrix):
    prefix = 'Row / Label | '
    print('\n' + prefix + '    '.join((str(i) for i in range(6))))
    print('-' * (len(prefix) + 26))
    for row_index, row in enumerate(matrix):
        print(f'{row_index} / {ROW_LABELS[row_index]:<5}   | ' + '    '.join(row))
    print('\nCoordinates use 0-5; token column numbers use 1-6.')

def normalise_key(key):
    if not isinstance(key, str):
        raise TypeError('Key must be a string.')
    key = key.upper().replace(' ', '')
    if not key:
        raise ValueError('Key cannot be empty.')
    for character in key:
        if character not in BASE_ALPHABET:
            raise ValueError('Key may contain only A-Z and 0-9.')
    return key

def normalise_plaintext(message):
    if not isinstance(message, str):
        raise TypeError('Plaintext must be a string.')
    message = message.upper().replace(' ', '')
    if not message:
        raise ValueError('Plaintext cannot be empty.')
    for character in message:
        if character not in BASE_ALPHABET:
            raise ValueError('Plaintext may contain only A-Z, 0-9 and spaces.')
    return message

def build_keyed_alphabet(key):
    key = normalise_key(key)
    keyed_alphabet = []
    used = set()
    for character in key + BASE_ALPHABET:
        if character not in used:
            keyed_alphabet.append(character)
            used.add(character)
    return ''.join(keyed_alphabet)

def build_matrix(key):
    keyed_alphabet = build_keyed_alphabet(key)
    matrix = [list(keyed_alphabet[row * 6:(row + 1) * 6]) for row in range(6)]
    coordinates = {}
    for row in range(6):
        for column in range(6):
            coordinates[matrix[row][column]] = (row, column)
    return (matrix, coordinates)

def key_coordinate(character):
    value = BASE_ALPHABET.index(character)
    return (value // 6, value % 6)

def initial_feedback(key):
    key = normalise_key(key)
    total = sum((BASE_ALPHABET.index(character) for character in key)) % 36
    return (total // 6, total % 6)

def coordinate_to_token(row, column):
    return f'{ROW_LABELS[row]}{column + 1}'

def token_to_coordinate(token):
    if not isinstance(token, str) or len(token) != 4:
        raise ValueError(f'Invalid ciphertext token: {token}')
    row_name = token[:3]
    column_text = token[3]
    if row_name not in ROW_LABELS:
        raise ValueError(f'Unknown engineering discipline: {row_name}')
    if column_text not in '123456':
        raise ValueError(f'Invalid column number: {token}')
    return (ROW_LABELS.index(row_name), int(column_text) - 1)

def generate_block_permutation(key, block_number, block_length):
    key = normalise_key(key)
    key_values = [BASE_ALPHABET.index(character) for character in key]
    scores = []
    for position in range(block_length):
        key_index = (block_number * BLOCK_SIZE + position) % len(key_values)
        key_value = key_values[key_index]
        score = (key_value + 11 * position + 7 * block_number) % 97
        scores.append(score)
    return sorted(range(block_length), key=lambda position: (scores[position], position))

def permute_tokens(tokens, key):
    output = []
    for start in range(0, len(tokens), BLOCK_SIZE):
        block = tokens[start:start + BLOCK_SIZE]
        block_number = start // BLOCK_SIZE
        permutation = generate_block_permutation(key, block_number, len(block))
        output.extend((block[position] for position in permutation))
    return output

def restore_token_order(tokens, key):
    output = []
    for start in range(0, len(tokens), BLOCK_SIZE):
        block = tokens[start:start + BLOCK_SIZE]
        block_number = start // BLOCK_SIZE
        permutation = generate_block_permutation(key, block_number, len(block))
        restored = [None] * len(block)
        for transmitted_position, original_position in enumerate(permutation):
            restored[original_position] = block[transmitted_position]
        output.extend(restored)
    return output

def encrypt(message, key, return_debug=False):
    key = normalise_key(key)
    message = normalise_plaintext(message)
    matrix, coordinates = build_matrix(key)
    previous_row, previous_column = initial_feedback(key)
    raw_tokens = []
    steps = []
    for index, plaintext_character in enumerate(message):
        plain_row, plain_column = coordinates[plaintext_character]
        key_character = key[index % len(key)]
        key_row, key_column = key_coordinate(key_character)
        feedback_before = (previous_row, previous_column)
        encrypted_row = (plain_row + key_row + index + previous_row) % 6
        encrypted_column = (plain_column + key_column + 2 * index + previous_column) % 6
        token = coordinate_to_token(encrypted_row, encrypted_column)
        raw_tokens.append(token)
        steps.append({'position': index + 1, 'plaintext': plaintext_character, 'plain_coordinate': (plain_row, plain_column), 'key_character': key_character, 'key_coordinate': (key_row, key_column), 'feedback': feedback_before, 'cipher_coordinate': (encrypted_row, encrypted_column), 'token': token})
        previous_row = encrypted_row
        previous_column = encrypted_column
    final_tokens = permute_tokens(raw_tokens, key)
    ciphertext = '-'.join(final_tokens)
    if return_debug:
        return (ciphertext, {'matrix': matrix, 'raw_tokens': raw_tokens, 'final_tokens': final_tokens, 'steps': steps})
    return ciphertext

def decrypt(ciphertext, key):
    key = normalise_key(key)
    if not isinstance(ciphertext, str):
        raise TypeError('Ciphertext must be a string.')
    if not ciphertext.strip():
        raise ValueError('Ciphertext cannot be empty.')
    transmitted_tokens = ciphertext.upper().split('-')
    for token in transmitted_tokens:
        token_to_coordinate(token)
    tokens = restore_token_order(transmitted_tokens, key)
    matrix, _ = build_matrix(key)
    previous_row, previous_column = initial_feedback(key)
    recovered_plaintext = []
    for index, token in enumerate(tokens):
        encrypted_row, encrypted_column = token_to_coordinate(token)
        key_character = key[index % len(key)]
        key_row, key_column = key_coordinate(key_character)
        plain_row = (encrypted_row - key_row - index - previous_row) % 6
        plain_column = (encrypted_column - key_column - 2 * index - previous_column) % 6
        recovered_plaintext.append(matrix[plain_row][plain_column])
        previous_row = encrypted_row
        previous_column = encrypted_column
    return ''.join(recovered_plaintext)

def validate_auth_key(auth_key):
    if not isinstance(auth_key, bytes) or len(auth_key) != 32:
        raise ValueError('Authentication key must contain exactly 32 bytes.')

def generate_authentication_tag(ciphertext, auth_key):
    validate_auth_key(auth_key)
    if not isinstance(ciphertext, str) or not ciphertext:
        raise ValueError('Ciphertext must be a nonempty string.')
    authenticated_data = AUTH_CONTEXT + ciphertext.encode('utf-8')
    return hmac.new(auth_key, authenticated_data, digestmod='sha256').hexdigest()

def verify_authentication_tag(ciphertext, received_tag, auth_key):
    validate_auth_key(auth_key)
    if not isinstance(ciphertext, str) or not ciphertext:
        return False
    if not isinstance(received_tag, str):
        return False
    if len(received_tag) != 64:
        return False
    if any((character not in '0123456789abcdef' for character in received_tag)):
        return False
    expected_tag = generate_authentication_tag(ciphertext, auth_key)
    return hmac.compare_digest(received_tag, expected_tag)

def encrypt_authenticated(message, key, auth_key, return_debug=False):
    validate_auth_key(auth_key)
    if return_debug:
        ciphertext, debug = encrypt(message, key, return_debug=True)
        tag = generate_authentication_tag(ciphertext, auth_key)
        return (ciphertext, tag, debug)
    ciphertext = encrypt(message, key)
    tag = generate_authentication_tag(ciphertext, auth_key)
    return (ciphertext, tag)

def decrypt_authenticated(ciphertext, received_tag, key, auth_key):
    if not verify_authentication_tag(ciphertext, received_tag, auth_key):
        raise ValueError('Authentication failed: message rejected before decryption.')
    return decrypt(ciphertext, key)


MAX_JSON_BYTES = 4096
MAX_WIRE_BYTES = 131072
MAX_SKEW_SECONDS = 120
REPLAY_CACHE_SECONDS = 300
BASE32_PATTERN = re.compile(r"[A-Z2-7]+\Z")


class EDCCodec:
    """Bidirectional codec: encrypt+HMAC on send, verify+decrypt on receive."""

    def __init__(self, edc_key, hmac_key):
        self.edc_key = normalise_key(edc_key)
        if not isinstance(hmac_key, bytes) or len(hmac_key) != 32:
            raise ValueError("The separate HMAC key must contain 32 bytes")
        self.hmac_key = hmac_key
        # Old packet IDs are retained for a bounded period after reconnects.
        self.seen = set()
        self.recent = deque()

    @classmethod
    def from_environment(cls):
        """Read matching pre-shared secrets set independently on both devices."""
        try:
            edc_key = os.environ["EDC_KEY"]
            hmac_key = bytes.fromhex(os.environ["EDC_HMAC_KEY_HEX"])
        except (KeyError, ValueError) as exc:
            raise ValueError("Set EDC_KEY and a 64-hex-character EDC_HMAC_KEY_HEX") from exc
        return cls(edc_key, hmac_key)

    def encode(self, message):
        """Build one authenticated wire packet from an original GUI JSON dict."""
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise ValueError("Expected original GUI message with a string type")
        inner = {
            "v": 1,
            "sent_at": int(time.time()),
            "nonce": secrets.token_hex(16).upper(),
            "message": message,
        }
        raw = json.dumps(inner, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("Message too large for EDC transport")
        plaintext = base64.b32encode(raw).decode("ascii").rstrip("=")
        ciphertext, tag = encrypt_authenticated(plaintext, self.edc_key, self.hmac_key)
        return {"type": "EDC_PACKET", "v": 1, "ciphertext": ciphertext, "tag": tag}

    def decode(self, packet):
        """Authenticate before decryption; validate timestamp and reject replay."""
        if (not isinstance(packet, dict)
                or set(packet) != {"type", "v", "ciphertext", "tag"}
                or packet.get("type") != "EDC_PACKET"
                or type(packet.get("v")) is not int or packet["v"] != 1):
            raise ValueError("Expected an EDC_PACKET; plaintext packets are rejected")
        ciphertext, tag = packet["ciphertext"], packet["tag"]
        if not isinstance(ciphertext, str) or len(ciphertext) > MAX_WIRE_BYTES:
            raise ValueError("Invalid or oversized ciphertext")
        # This existing EDC function verifies HMAC before attempting decryption.
        plaintext = decrypt_authenticated(ciphertext, tag, self.edc_key, self.hmac_key)
        if not BASE32_PATTERN.fullmatch(plaintext):
            raise ValueError("Decrypted data is not valid Base32")
        try:
            raw = base64.b32decode(plaintext + "=" * (-len(plaintext) % 8))
            if len(raw) > MAX_JSON_BYTES:
                raise ValueError("Decrypted JSON too large")
            inner = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid EDC packet payload") from exc
        if not isinstance(inner, dict) or set(inner) != {"v", "sent_at", "nonce", "message"}:
            raise ValueError("Invalid authenticated packet fields")
        if type(inner["v"]) is not int or inner["v"] != 1:
            raise ValueError("Unsupported packet version")
        timestamp = inner["sent_at"]
        nonce = inner["nonce"]
        message = inner["message"]
        now = int(time.time())
        if type(timestamp) is not int or abs(now - timestamp) > MAX_SKEW_SECONDS:
            raise ValueError("Expired or future packet; check clock synchronization")
        if not isinstance(nonce, str) or not re.fullmatch(r"[0-9A-F]{32}", nonce):
            raise ValueError("Invalid packet nonce")
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise ValueError("Malformed GUI application message")
        # Evict old entries and reject duplicates, even if the TCP link reconnects.
        while self.recent and now - self.recent[0][0] > REPLAY_CACHE_SECONDS:
            _, old_nonce = self.recent.popleft()
            self.seen.discard(old_nonce)
        if nonce in self.seen:
            raise ValueError("Replay detected: packet already received")
        self.seen.add(nonce)
        self.recent.append((now, nonce))
        return message


ROOT = Path(__file__).resolve().parent
MODEL_FILE = ROOT / "movement_knn_model.joblib"
PINS = {"1": (13, 15), "2": (16, 18), "3": (22, 24), "4": (29, 31), "5": (7, 11)}
TRIGGER_DISTANCE_CM = RESET_DISTANCE_CM = 20.0
RESET_CONFIRM_READINGS = 3
CLASSES = {"PASS_BY", "APPROACH_STAY", "AUTH_GESTURE"}
DOOR_PINS = {"green": 33, "red": 35, "buzzer": 37}
GPIO = None


def validate_configuration(message):
    if not isinstance(message, dict) or message.get("type") != "SET_CONFIG":
        raise ValueError("Expected SET_CONFIG")
    revision = message.get("revision")
    mapping, thresholds = message.get("mapping"), message.get("thresholds_cm")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("Invalid revision")
    if message.get("gesture_sensor") != 5:
        raise ValueError("Sensor 5 must remain dedicated to gesture KNN")
    if not isinstance(mapping, dict) or set(mapping) != {"1", "2", "3", "4"} or any(v not in {"eat", "sleep", "water", "toilet"} for v in mapping.values()):
        raise ValueError("Invalid zone assignments")
    if not isinstance(thresholds, dict) or set(thresholds) != set(mapping):
        raise ValueError("Invalid thresholds")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 1 <= v <= 400 for v in thresholds.values()):
        raise ValueError("Thresholds must be 1–400 cm")
    return {"type": "SET_CONFIG", "revision": revision, "mapping": mapping.copy(),
            "thresholds_cm": thresholds.copy(), "gesture_sensor": 5}


class GuiConnection:
    """Persistent bidirectional transport. Disconnected/stale events are dropped."""
    def __init__(self, host, port, codec=None):
        self.host, self.port = host, port
        self.codec = codec
        self.events = queue.Queue(maxsize=128)
        self.configurations = queue.Queue(maxsize=8)
        self.commands = queue.Queue(maxsize=32)
        self.stop = threading.Event()
        self.connected = threading.Event()
        self.ready = threading.Event()
        self.generation = 0
        self.worker = threading.Thread(target=self._run, daemon=True, name="gui-tcp")

    def start(self):
        self.worker.start()

    def _drain(self):
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break

    def send(self, kind, **data):
        if not self.connected.is_set() or (kind != "CONFIG_ACK" and not self.ready.is_set()):
            return False
        message = {"type": kind, "timestamp": datetime.now(timezone.utc).isoformat(), **data}
        try:
            self.events.put_nowait((time.monotonic(), message))
            return True
        except queue.Full:
            print("[TCP] Queue full; event dropped")
            return False

    def _run(self):
        while not self.stop.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=1) as connection:
                    connection.settimeout(.05)
                    self._drain()
                    self.ready.clear()
                    self.generation += 1
                    self.connected.set()
                    print(f"[TCP] Connected to {self.host}:{self.port}; waiting for configuration")
                    buffer = b""
                    max_line = MAX_WIRE_BYTES if self.codec else 4096
                    while not self.stop.is_set():
                        try:
                            data = connection.recv(4096)
                            if not data:
                                raise ConnectionError("GUI disconnected")
                            buffer += data
                            while b"\n" in buffer:
                                line, buffer = buffer.split(b"\n", 1)
                                if len(line) > max_line:
                                    raise ValueError("Configuration too large")
                                message = json.loads(line)
                                # Verify HMAC, freshness and replay before handling commands.
                                if self.codec is not None:
                                    message = self.codec.decode(message)
                                if isinstance(message, dict) and message.get("type") == "DOOR_COMMAND":
                                    if message.get("action") not in {"AUTH_OK", "OTP_OK", "RESET"}:
                                        raise ValueError("Unknown door command")
                                    self.commands.put_nowait((self.generation, time.monotonic(), message["action"]))
                                    continue
                                config = validate_configuration(message)
                                self.ready.clear()
                                self._drain()
                                self.configurations.put_nowait(config)
                            if len(buffer) > max_line:
                                raise ValueError("Configuration too large")
                        except socket.timeout:
                            pass
                        for _ in range(16):
                            try:
                                emitted, message = self.events.get_nowait()
                            except queue.Empty:
                                break
                            if time.monotonic() - emitted > 3:
                                continue
                            # Encrypt off the GPIO acquisition loop.
                            packet = self.codec.encode(message) if self.codec else message
                            print("[1 ORIGINAL]", json.dumps(message))
                            print("[2 ENCRYPTED]", json.dumps(packet))
                            connection.sendall((json.dumps(packet, allow_nan=False) + "\n").encode())
                            if message["type"] == "CONFIG_ACK":
                                self.ready.set()
            except (OSError, ValueError, queue.Full) as exc:
                print(f"[TCP] Connection unavailable: {exc}; retrying")
            finally:
                self.connected.clear()
                self.ready.clear()
                self._drain()
                while True:
                    try:
                        self.configurations.get_nowait()
                    except queue.Empty:
                        break
            self.stop.wait(1)

    def close(self):
        self.stop.set()
        self.worker.join(timeout=2)


class DoorFeedback:
    """Nonblocking lights and beep patterns; called only by the acquisition loop."""
    def __init__(self, gpio=None):
        self.gpio = gpio
        self.state = "IDLE"
        self.near = False
        self.near_since = None
        self.last_reading = None
        self.warned = False
        self.until = 0
        self.notes = []
        self.levels = None
        self.tone_hz = 0
        self.buzzer_pwm = None
        if gpio is not None:
            for pin in DOOR_PINS.values():
                gpio.setup(pin, gpio.OUT, initial=gpio.LOW)
            self.buzzer_pwm = gpio.PWM(DOOR_PINS["buzzer"], 1000)
            self.buzzer_pwm.start(0)

    def sound(self, now, pattern):
        self.notes = []
        cursor = now
        for frequency, duration in pattern:
            self.notes.append((cursor, cursor + duration, frequency))
            cursor += duration

    def reset(self, now):
        self.state = "IDLE"
        self.until = 0
        self.notes = []
        self.near_since = now if self.near else None
        self.warned = False

    def command(self, action, now):
        if action == "RESET":
            self.reset(now)
        elif action == "AUTH_OK" and self.state != "GRANTED":
            self.state, self.until = "AUTH", now + 120
            self.sound(now, [(880, .12), (0, .10), (1320, .12)])
        elif action == "OTP_OK" and self.state == "AUTH":
            self.state, self.until = "GRANTED", now + 10
            self.sound(now, [(1047, .12), (0, .10), (1319, .12), (0, .10), (1568, .30)])

    def read(self, distance, now):
        self.last_reading = now
        # A missing echo breaks the continuous-presence warning timer.
        self.near = distance is not None and distance < TRIGGER_DISTANCE_CM
        if self.near:
            if self.near_since is None:
                self.near_since = now
        else:
            self.near_since = None
            self.warned = False

    def tick(self, now):
        if self.last_reading is not None and now - self.last_reading > 1:
            self.read(None, now)
        if self.state != "IDLE" and now >= self.until:
            self.reset(now)
        if (self.state == "IDLE" and self.near_since is not None
                and now - self.near_since >= 10 and not self.warned):
            self.warned = True
            self.sound(now, [(440, .5), (0, .2)] * 3)
        green = self.state == "GRANTED" or (self.state == "AUTH" and int(now * 4) % 2 == 0)
        red = self.state == "IDLE" and self.near
        frequency = next((hz for start, end, hz in self.notes if start <= now < end), 0)
        if frequency != self.tone_hz:
            if self.buzzer_pwm is not None:
                if frequency:
                    self.buzzer_pwm.ChangeFrequency(frequency)
                self.buzzer_pwm.ChangeDutyCycle(50 if frequency else 0)
            self.tone_hz = frequency
        buzzer = bool(frequency)
        levels = (green, red, buzzer)
        if levels != self.levels:
            if self.gpio is not None:
                for pin, value in ((DOOR_PINS["green"], green), (DOOR_PINS["red"], red)):
                    self.gpio.output(pin, value)
            self.levels = levels

    def close(self):
        if self.buzzer_pwm is not None:
            self.buzzer_pwm.stop()
        if self.gpio is not None:
            for pin in DOOR_PINS.values():
                self.gpio.output(pin, self.gpio.LOW)


class ZoneTracker:
    def __init__(self):
        self.config = None
        self.started = {key: None for key in ("1", "2", "3", "4")}

    def apply(self, config, path):
        config = validate_configuration(config)
        # Save before acknowledgement. Discard partial visits on reassignment.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
        temporary.replace(path)
        self.config = config
        self.started = dict.fromkeys(self.started)

    def reset(self):
        self.started = dict.fromkeys(self.started)

    def read(self, sensor_id, distance, now, sender):
        if not self.config or not sender.ready.is_set():
            self.started[sensor_id] = None
            return
        revision = self.config["revision"]
        presence = distance is not None and distance < self.config["thresholds_cm"][sensor_id]
        sender.send("SENSOR_READING", sensor_id=int(sensor_id), revision=revision,
                    distance_cm=distance, presence=presence)
        if distance is None:
            # Missing echo is not evidence of departure; discard partial visit.
            self.started[sensor_id] = None
        elif presence and self.started[sensor_id] is None:
            self.started[sensor_id] = now
        elif not presence and self.started[sensor_id] is not None:
            duration = now - self.started[sensor_id]
            if 0 <= duration <= 86400:
                sender.send("ZONE_EVENT", sensor_id=int(sensor_id), revision=revision,
                    event_id=str(uuid.uuid4()), duration_seconds=round(duration, 3))
            self.started[sensor_id] = None


def setup_sensors():
    global GPIO
    import RPi.GPIO as gpio
    GPIO = gpio
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD)
    for trigger, echo in PINS.values():
        GPIO.setup(trigger, GPIO.OUT)
        GPIO.setup(echo, GPIO.IN)
        GPIO.output(trigger, GPIO.LOW)
    time.sleep(1)


def calculate_distance(sensor_id="5"):
    """Only called by the main acquisition loop, never concurrently."""
    trigger, echo = PINS[sensor_id]
    GPIO.output(trigger, GPIO.LOW)
    time.sleep(.000002)
    GPIO.output(trigger, GPIO.HIGH)
    time.sleep(.00001)
    GPIO.output(trigger, GPIO.LOW)
    # Total timeout bounds each read, leaving time for the next scheduled sensor.
    deadline = time.monotonic() + .03
    while GPIO.input(echo) == GPIO.LOW:
        if time.monotonic() >= deadline:
            return None
    start = time.monotonic()
    while GPIO.input(echo) == GPIO.HIGH:
        if time.monotonic() >= deadline:
            return None
    distance = round((time.monotonic() - start) * 17150, 2)
    maximum = 200 if sensor_id == "5" else 400
    return distance if 0 < distance <= maximum else None


class GestureRecorder:
    """Nonblocking collection/rearm state machine; hardware stays in one loop."""
    def __init__(self, window, interval):
        self.window, self.interval = window, interval
        self.sequence = None
        self.last_valid = None
        self.last_sample = None
        self.waiting_clear = False
        self.clear_readings = 0

    def sample(self, distance, now, sender):
        if self.waiting_clear:
            self.clear_readings = self.clear_readings + 1 if distance is not None and distance >= RESET_DISTANCE_CM else 0
            if self.clear_readings >= RESET_CONFIRM_READINGS:
                self.waiting_clear = False
                self.clear_readings = 0
                sender.send("IDLE", sensor_id=5)
            return None
        if self.sequence is None:
            if distance is None or distance >= TRIGGER_DISTANCE_CM:
                return None
            self.sequence = [distance]
            self.last_valid = distance
            self.last_sample = now
            sender.send("TRIGGERED", sensor_id=5)
            print("[GESTURE] Recording movement")
            return None
        if now - self.last_sample > self.interval * 1.5:
            print("[GESTURE] Sampling overrun; discarded window")
            self.sequence = None
            self.waiting_clear = True
            sender.send("IDLE", sensor_id=5)
            return None
        self.last_sample = now
        if distance is not None:
            self.last_valid = distance
        self.sequence.append(self.last_valid)  # Original forward-fill policy.
        if len(self.sequence) == self.window:
            sequence, self.sequence = self.sequence, None
            self.waiting_clear = True
            return sequence
        return None


def main(argv=None):
    global MODEL_FILE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-host", required=True, help="Laptop IPv4 address")
    parser.add_argument("--gui-port", type=int, default=None,
                        help="5005 plaintext, or 5006 with --edc")
    parser.add_argument("--edc", action="store_true",
                        help="Enable EDC/HMAC; requires matching environment keys and laptop bridge")
    parser.add_argument("--model", type=Path, default=MODEL_FILE)
    parser.add_argument("--config-file", type=Path, default=ROOT / "hub_configuration.json")
    parser.add_argument("--simulate", action="store_true", help="Test TCP and synthetic distances without GPIO/model")
    parser.add_argument("--demo-class", choices=sorted(CLASSES), default="PASS_BY")
    args = parser.parse_args(argv)
    if args.gui_port is None:
        args.gui_port = 5005
    if not 1 <= args.gui_port <= 65535:
        parser.error("Port must be 1–65535")
    MODEL_FILE = args.model
    if args.simulate:
        bundle, model, names, window, interval = None, None, None, 60, .1
        print("SIMULATION: no GPIO or trained-model inference; synthetic results")
    else:
        bundle, model, names, window, interval = load_model_bundle()
    if interval < .08:
        raise ValueError("This sequential five-sensor scheduler requires a trained interval >= 0.08 seconds")
    codec = EDCCodec.from_environment() if args.edc else None
    sender = GuiConnection(args.gui_host, args.gui_port, codec=codec)
    print("[SECURITY] EDC + HMAC enabled" if args.edc else
          "[SECURITY] INSECURE plaintext TCP; use --edc and laptop bridge")
    zones = ZoneTracker()
    gesture = GestureRecorder(window, interval)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="knn")
    future = None
    future_epoch = None
    started = time.monotonic()
    next_gesture, next_zone = started, started + interval / 2
    zone_index = 0
    last_trigger = -1.0
    online = False
    last_telemetry = -1.0
    door = None
    door_epoch = None
    try:
        if not args.simulate:
            setup_sensors()
        door = DoorFeedback(GPIO)
        sender.start()
        print("BOARD trigger/echo pins:", PINS)
        while True:
            now = time.monotonic()
            epoch = (sender.generation, sender.ready.is_set())
            if epoch != door_epoch:
                door.reset(now)
                door_epoch = epoch
            for _ in range(32):
                try:
                    generation, emitted, action = sender.commands.get_nowait()
                except queue.Empty:
                    break
                if sender.ready.is_set() and generation == sender.generation and now - emitted < 3:
                    door.command(action, now)
            door.tick(now)
            try:
                config = sender.configurations.get_nowait()
            except queue.Empty:
                config = None
            if config is not None:
                try:
                    door.reset(time.monotonic())
                    zones.apply(config, args.config_file)
                    gesture = GestureRecorder(window, interval)
                    sender.send("CONFIG_ACK", revision=config["revision"])
                    print("[CONFIG] Applied", config["revision"], config["mapping"])
                except (OSError, ValueError) as exc:
                    print("[CONFIG] Not applied:", exc)
            if online and not sender.ready.is_set():
                zones.reset()
            online = sender.ready.is_set()
            if future is not None and future.done():
                try:
                    prediction, confidence = future.result()
                    if sender.ready.is_set() and future_epoch == (sender.generation, zones.config["revision"]):
                        sender.send("MOVEMENT_RESULT", sensor_id=5, prediction=prediction, confidence=round(confidence, 4))
                except Exception as exc:
                    print("[ML] Inference failed:", exc)
                future = None
            is_gesture = next_gesture <= next_zone
            target = next_gesture if is_gesture else next_zone
            now = time.monotonic()
            if now < target or now - last_trigger < interval * .4:
                time.sleep(min(.005, max(.001, target - now)))
                continue
            sensor_id = "5" if is_gesture else str(zone_index + 1)
            last_trigger = time.monotonic()
            if args.simulate:
                elapsed = last_trigger - started
                near = (elapsed % 16 < 7) if is_gesture else (int(elapsed / 4) % 4 == zone_index)
                distance = 10.0 if near else 90.0
            else:
                distance = calculate_distance(sensor_id)
            if is_gesture:
                door.read(distance, last_trigger)
                door.tick(time.monotonic())
                # Sample timestamps are trigger times, independent of echo duration.
                if sender.ready.is_set():
                    sequence = gesture.sample(distance, last_trigger, sender)
                else:
                    gesture = GestureRecorder(window, interval)
                    sequence = None
                if sequence is not None and future is None:
                    if args.simulate:
                        sender.send("MOVEMENT_RESULT", sensor_id=5, prediction=args.demo_class, confidence=.94)
                    else:
                        future = executor.submit(classify_movement, sequence, bundle, model, names, window, interval)
                        future_epoch = (sender.generation, zones.config["revision"])
                if last_trigger - last_telemetry >= .2:
                    state = "CLASSIFYING" if future is not None else "RECORDING" if gesture.sequence is not None else "WAIT_CLEAR" if gesture.waiting_clear else "WAITING"
                    samples = len(gesture.sequence) if gesture.sequence is not None else window if gesture.waiting_clear else 0
                    sender.send("GESTURE_READING", sensor_id=5, distance_cm=distance,
                                state=state, samples=samples, total=window)
                    last_telemetry = last_trigger
                next_gesture += interval
                if next_gesture < time.monotonic():
                    next_gesture = time.monotonic() + interval
            else:
                zones.read(sensor_id, distance, last_trigger, sender)
                zone_index = (zone_index + 1) % 4
                next_zone += interval
                if next_zone < time.monotonic():
                    next_zone = time.monotonic() + interval
    except KeyboardInterrupt:
        print("Stopping hub")
    finally:
        if sender.worker.is_alive():
            sender.close()
        executor.shutdown(wait=True, cancel_futures=True)
        if door is not None:
            door.close()
        if GPIO is not None:
            GPIO.cleanup()


# Original model loading and feature/classification functions are inserted below.


def load_model_bundle():
    import joblib
    bundle = joblib.load(MODEL_FILE)
    required = (
        "model", "feature_names", "window_size", "sample_interval",
        "close_cm", "mid_max_cm", "crossing_cm",
    )
    missing = [key for key in required if key not in bundle]
    if missing:
        raise ValueError(f"Model bundle is missing keys: {missing}")

    model = bundle["model"]
    feature_names = list(bundle["feature_names"])
    window_size = int(bundle["window_size"])
    sample_interval = float(bundle["sample_interval"])

    if len(feature_names) != 14:
        raise ValueError("This detector expects the existing 14-feature model.")
    if window_size < 2 or not math.isfinite(sample_interval) or sample_interval <= 0:
        raise ValueError("Invalid window_size or sample_interval in model bundle.")

    print(f"Model: {MODEL_FILE}")
    print(f"Classes: {list(model.classes_)}")
    print(f"Features: {len(feature_names)}")
    print(f"Window: {window_size} samples at {1 / sample_interval:.1f} Hz")
    return bundle, model, feature_names, window_size, sample_interval

def extract_features(sequence, feature_names, window_size, sample_interval,
                     close_cm, mid_max_cm, crossing_cm):
    import numpy as np
    distances = np.asarray(sequence, dtype=float)
    if distances.shape != (window_size,):
        raise ValueError(f"Expected {window_size} distances; got {distances.shape}")

    time_axis = np.arange(window_size) * sample_interval
    close = distances < close_cm
    mid = (distances >= close_cm) & (distances <= mid_max_cm)
    far = distances > mid_max_cm
    below_threshold = distances < crossing_cm

    start_mean = np.mean(distances[:5])
    end_mean = np.mean(distances[-5:])
    feature_dict = {
        "mean_distance": np.mean(distances),
        "min_distance": np.min(distances),
        "max_distance": np.max(distances),
        "std_distance": np.std(distances),
        "q1": np.percentile(distances, 25),
        "q3": np.percentile(distances, 75),
        "start_mean": start_mean,
        "end_mean": end_mean,
        "end_minus_start": end_mean - start_mean,
        "slope": np.polyfit(time_axis, distances, 1)[0],
        "close_ratio": np.mean(close),
        "mid_ratio": np.mean(mid),
        "far_ratio": np.mean(far),
        "threshold_crossings": np.count_nonzero(
            below_threshold[1:] != below_threshold[:-1]
        ),
    }
    # IMPORTANT: the order is loaded from the training bundle.
    return np.array([feature_dict[name] for name in feature_names], dtype=float)

def classify_movement(sequence, bundle, model, feature_names,
                      window_size, sample_interval):
    features = extract_features(
        sequence, feature_names, window_size, sample_interval,
        float(bundle["close_cm"]), float(bundle["mid_max_cm"]),
        float(bundle["crossing_cm"]),
    )
    x = features.reshape(1, -1)
    prediction = str(model.predict(x)[0])
    probabilities = model.predict_proba(x)[0]
    classes = [str(label) for label in model.classes_]
    confidence = float(probabilities[classes.index(prediction)])
    if prediction not in CLASSES or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("Model returned an unsupported class or invalid confidence")
    print(f"[ML] {prediction} | confidence: {confidence:.1%}")
    return prediction, confidence

if __name__ == "__main__":
    main()
