"""Wire-compatible with the supplied Pi codec. EDC is educational, not production security."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import deque

BASE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
ROW_LABELS = ("ECE", "FIT", "TRC", "MEC", "CHE", "CIV")
AUTH_CONTEXT = b"EDC-HMAC-v1\x00"
MAX_JSON_BYTES = 4096
MAX_WIRE_BYTES = 131072
MAX_SKEW_SECONDS = 120
REPLAY_CACHE_SECONDS = 300
MAX_REPLAY_ENTRIES = 100000


def normalise_key(key):
    if not isinstance(key, str):
        raise ValueError("EDC key must be a string")
    key = key.upper().replace(" ", "")
    if not key or any(character not in BASE_ALPHABET for character in key):
        raise ValueError("EDC key must contain A-Z or 0-9")
    return key


def _matrix(key):
    alphabet = "".join(dict.fromkeys(key + BASE_ALPHABET))
    return alphabet, {character: divmod(index, 6) for index, character in enumerate(alphabet)}


def _permutation(key, block_number, length):
    scores = [(BASE_ALPHABET.index(key[(block_number * 4 + position) % len(key)])
               + 11 * position + 7 * block_number) % 97 for position in range(length)]
    return sorted(range(length), key=lambda position: (scores[position], position))


def _coordinate(token):
    if len(token) != 4 or token[:3] not in ROW_LABELS or token[3] not in "123456":
        raise ValueError("Invalid EDC token")
    return ROW_LABELS.index(token[:3]), int(token[3]) - 1


def encrypt(message, key):
    """Original keyed matrix, coordinate feedback, and four-token permutation."""
    key = normalise_key(key)
    message = message.upper().replace(" ", "")
    if not message or any(character not in BASE_ALPHABET for character in message):
        raise ValueError("Invalid EDC plaintext")
    _, coordinates = _matrix(key)
    previous_row, previous_column = divmod(sum(BASE_ALPHABET.index(c) for c in key) % 36, 6)
    tokens = []
    for index, character in enumerate(message):
        row, column = coordinates[character]
        key_row, key_column = divmod(BASE_ALPHABET.index(key[index % len(key)]), 6)
        previous_row = (row + key_row + index + previous_row) % 6
        previous_column = (column + key_column + 2 * index + previous_column) % 6
        tokens.append(f"{ROW_LABELS[previous_row]}{previous_column + 1}")
    output = []
    for start in range(0, len(tokens), 4):
        block = tokens[start:start + 4]
        output.extend(block[position] for position in _permutation(key, start // 4, len(block)))
    return "-".join(output)


def decrypt(ciphertext, key, trace=None):
    key = normalise_key(key)
    transmitted = ciphertext.upper().split("-")
    tokens = []
    for start in range(0, len(transmitted), 4):
        block = transmitted[start:start + 4]
        restored = [None] * len(block)
        for position, original in enumerate(_permutation(key, start // 4, len(block))):
            restored[original] = block[position]
        tokens.extend(restored)
    if trace is not None:
        trace["blocks"] = [
            {"block": start // 4 + 1, "transmitted": transmitted[start:start + 4],
             "restored": tokens[start:start + 4]}
            for start in range(0, min(len(tokens), 64), 4)]
        trace["token_count"] = len(tokens)
        trace["checks"]["permutation"] = "Passed: reversed four-token blocks"
        trace["stage"] = "transformations"
        trace["samples"] = []
    alphabet, _ = _matrix(key)
    previous_row, previous_column = divmod(sum(BASE_ALPHABET.index(c) for c in key) % 36, 6)
    output = []
    for index, token in enumerate(tokens):
        encrypted_row, encrypted_column = _coordinate(token)
        key_row, key_column = divmod(BASE_ALPHABET.index(key[index % len(key)]), 6)
        row = (encrypted_row - key_row - index - previous_row) % 6
        column = (encrypted_column - key_column - 2 * index - previous_column) % 6
        output.append(alphabet[row * 6 + column])
        if trace is not None and index < 12:
            trace["samples"].append({"position": index + 1, "token": token,
                                     "character": output[-1]})
        previous_row, previous_column = encrypted_row, encrypted_column
    return "".join(output)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite JSON number")


def load_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("Invalid JSON") from exc


class EDCCodec:
    """Verify before decrypting; retain replay IDs across TCP reconnects and stops."""

    def __init__(self, edc_key, hmac_key):
        self.edc_key = normalise_key(edc_key)
        if not isinstance(hmac_key, bytes) or len(hmac_key) != 32:
            raise ValueError("The separate HMAC key must contain 32 bytes")
        self.hmac_key = hmac_key
        self._seen = set()
        self._recent = deque()

    @classmethod
    def from_environment(cls):
        try:
            return cls(os.environ["EDC_KEY"], bytes.fromhex(os.environ["EDC_HMAC_KEY_HEX"]))
        except (KeyError, ValueError) as exc:
            raise ValueError("Set EDC_KEY and a 64-hex-character EDC_HMAC_KEY_HEX") from exc

    def _tag(self, ciphertext):
        return hmac.new(self.hmac_key, AUTH_CONTEXT + ciphertext.encode("utf-8"), "sha256").hexdigest()

    def encode(self, message):
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise ValueError("Expected a GUI message")
        inner = {"v": 1, "sent_at": int(time.time()), "nonce": secrets.token_hex(16).upper(),
                 "message": message}
        raw = json.dumps(inner, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("Message too large")
        ciphertext = encrypt(base64.b32encode(raw).decode("ascii").rstrip("="), self.edc_key)
        return {"type": "EDC_PACKET", "v": 1, "ciphertext": ciphertext, "tag": self._tag(ciphertext)}

    def decode(self, packet, trace=None):
        """Decode once, optionally filling a bounded snapshot without key material.

        The caller owns trace until decoding and application validation finish.
        Tracing never changes authentication or replay decisions.
        """
        if trace is not None:
            trace.clear()
            trace.update(stage="packet", outcome="Processing", checks={},
                         received_at=time.time(), decrypted=False)
            if isinstance(packet, dict):
                for field, limit in (("ciphertext", MAX_WIRE_BYTES), ("tag", 256)):
                    value = packet.get(field)
                    if isinstance(value, str):
                        trace[field] = value[:limit]
                        if len(value) > limit:
                            trace[field + "_truncated"] = True
        try:
            return self._decode(packet, trace)
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            if trace is not None:
                trace["outcome"] = "Rejected"
                trace["failed_stage"] = trace["stage"]
                trace["error"] = str(exc)[:240]
                trace["checks"][trace["stage"]] = "Rejected: " + str(exc)[:240]
                if trace["stage"] == "hmac":
                    trace["error"] = "Rejected before decryption: HMAC verification failed"
            raise

    def _decode(self, packet, trace):
        if (not isinstance(packet, dict) or set(packet) != {"type", "v", "ciphertext", "tag"}
                or packet["type"] != "EDC_PACKET" or type(packet["v"]) is not int or packet["v"] != 1):
            raise ValueError("Expected EDC_PACKET; plaintext rejected")
        ciphertext, tag = packet["ciphertext"], packet["tag"]
        if not isinstance(ciphertext, str) or not 0 < len(ciphertext) <= MAX_WIRE_BYTES:
            raise ValueError("Invalid ciphertext size")
        if trace is not None:
            trace["checks"]["packet"] = "Passed: packet structure and ciphertext size"
            trace["stage"] = "hmac"
        if (not isinstance(tag, str) or not re.fullmatch(r"[0-9a-f]{64}", tag)
                or not hmac.compare_digest(self._tag(ciphertext), tag)):
            raise ValueError("HMAC verification failed")
        if trace is not None:
            trace["checks"]["hmac"] = "Passed: HMAC-SHA256 verified before decryption"
            trace["stage"] = "ciphertext_replay"
        # Cache the authenticated ciphertext too, so exact replays never reach decrypt.
        now = time.monotonic()
        while self._recent and now - self._recent[0][0] > REPLAY_CACHE_SECONDS:
            _, identifiers = self._recent.popleft()
            self._seen.difference_update(identifiers)
        fingerprint = hashlib.sha256(ciphertext.encode("utf-8")).digest()
        if fingerprint in self._seen:
            raise ValueError("Replay rejected")
        if len(self._recent) >= MAX_REPLAY_ENTRIES:
            raise ValueError("Replay cache full")
        self._seen.add(fingerprint)
        self._recent.append((now, {fingerprint}))
        if trace is not None:
            trace["checks"]["ciphertext_replay"] = "Passed: ciphertext not previously seen"
            trace["stage"] = "permutation"
        plaintext = decrypt(ciphertext, self.edc_key, trace=trace) if trace is not None else decrypt(ciphertext, self.edc_key)
        if trace is not None:
            trace["decrypted"] = True
            trace["checks"]["transformations"] = "Passed: coordinate shifts and ciphertext feedback reversed"
            trace["stage"] = "base32"
            trace["base32"] = plaintext[:MAX_JSON_BYTES * 2]
            trace["base32_truncated"] = len(plaintext) > MAX_JSON_BYTES * 2
        if not re.fullmatch(r"[A-Z2-7]+", plaintext):
            raise ValueError("Invalid decrypted Base32")
        raw = base64.b32decode(plaintext + "=" * (-len(plaintext) % 8))
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("Decrypted JSON too large")
        if trace is not None:
            trace["json_text"] = raw.decode(json.detect_encoding(raw))
        inner = load_json(raw)
        if trace is not None:
            trace["checks"]["base32"] = "Passed: Base32 decoded and JSON parsed"
            trace["stage"] = "metadata"
        if (not isinstance(inner, dict) or set(inner) != {"v", "sent_at", "nonce", "message"}
                or type(inner["v"]) is not int or inner["v"] != 1):
            raise ValueError("Invalid authenticated metadata")
        checked_at = int(time.time())
        if trace is not None:
            trace["checks"]["metadata"] = "Passed: envelope fields and version"
            trace["stage"] = "timestamp"
            trace["sent_at"] = inner["sent_at"]
            trace["checked_at"] = checked_at
        if type(inner["sent_at"]) is not int or abs(checked_at - inner["sent_at"]) > MAX_SKEW_SECONDS:
            raise ValueError("Expired or future packet; check clocks")
        if trace is not None:
            trace["checks"]["timestamp"] = "Passed: within 120 seconds of laptop clock"
            trace["stage"] = "nonce_metadata"
        nonce, message = inner["nonce"], inner["message"]
        if not isinstance(nonce, str) or not re.fullmatch(r"[0-9A-F]{32}", nonce):
            raise ValueError("Invalid replay nonce")
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise ValueError("Invalid GUI message")
        if trace is not None:
            trace["checks"]["nonce_metadata"] = "Passed: nonce format and message structure"
            trace["stage"] = "nonce_replay"
        if nonce in self._seen:
            raise ValueError("Replay nonce rejected")
        self._seen.add(nonce)
        self._recent[-1][1].add(nonce)
        if trace is not None:
            trace["checks"]["nonce_replay"] = "Passed: nonce not previously accepted"
            trace["stage"] = "application"
            trace["outcome"] = "Transport passed"
        return message
