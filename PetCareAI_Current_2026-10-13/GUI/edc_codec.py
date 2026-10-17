
"""
EDC encryption/decryption codec for the laptop.

Compatible with the Raspberry Pi's EDC_PACKET v1 format.
Includes the original EDC cipher, HMAC-SHA256 and replay detection.
"""

import base64
import hmac
import json
import os
import re
import secrets
import time
from collections import deque

# EDC algorithm constants
BASE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
ROW_LABELS = ["ECE", "FIT", "TRC", "MEC", "CHE", "CIV"]
BLOCK_SIZE = 4
AUTH_CONTEXT = b"EDC-HMAC-v1\x00"

# Packet security limits
MAX_JSON_BYTES = 4096
MAX_WIRE_BYTES = 131072
MAX_SKEW_SECONDS = 120
REPLAY_CACHE_SECONDS = 300
BASE32_PATTERN = re.compile(r"[A-Z2-7]+\Z")


# ------------------------------------------------------------
# 1. KEY AND PLAINTEXT VALIDATION
# ------------------------------------------------------------

def normalise_key(key):
    if not isinstance(key, str):
        raise TypeError("Key must be a string")

    key = key.upper().replace(" ", "")

    if not key or any(c not in BASE_ALPHABET for c in key):
        raise ValueError("EDC key must contain only A-Z and 0-9")

    return key


def normalise_plaintext(message):
    if not isinstance(message, str):
        raise TypeError("Plaintext must be a string")

    message = message.upper().replace(" ", "")

    if not message or any(c not in BASE_ALPHABET for c in message):
        raise ValueError("Plaintext must contain only A-Z and 0-9")

    return message


# ------------------------------------------------------------
# 2. KEYED 6x6 ENGINEERING MATRIX
# ------------------------------------------------------------

def build_matrix(key):
    keyed = "".join(dict.fromkeys(
        normalise_key(key) + BASE_ALPHABET
    ))

    matrix = [
        list(keyed[i:i + 6])
        for i in range(0, 36, 6)
    ]

    coordinates = {
        char: (row, col)
        for row in range(6)
        for col, char in enumerate(matrix[row])
    }

    return matrix, coordinates


def key_coordinate(char):
    position = BASE_ALPHABET.index(char)
    return divmod(position, 6)


def initial_feedback(key):
    total = sum(
        BASE_ALPHABET.index(char)
        for char in normalise_key(key)
    ) % 36

    return divmod(total, 6)


# ------------------------------------------------------------
# 3. ENGINEERING TOKEN CONVERSION
# ------------------------------------------------------------

def coordinate_to_token(row, col):
    return f"{ROW_LABELS[row]}{col + 1}"


def token_to_coordinate(token):
    if not isinstance(token, str) or len(token) != 4:
        raise ValueError("Invalid EDC token")

    if token[:3] not in ROW_LABELS or token[3] not in "123456":
        raise ValueError("Invalid EDC token")

    return ROW_LABELS.index(token[:3]), int(token[3]) - 1


# ------------------------------------------------------------
# 4. BLOCK PERMUTATION
# ------------------------------------------------------------

def block_permutation(key, block_number, length):
    values = [
        BASE_ALPHABET.index(c)
        for c in normalise_key(key)
    ]

    scores = []

    for position in range(length):
        index = (
            block_number * BLOCK_SIZE + position
        ) % len(values)

        score = (
            values[index] + 11 * position + 7 * block_number
        ) % 97

        scores.append(score)

    return sorted(
        range(length),
        key=lambda position: (scores[position], position)
    )


def permute_tokens(tokens, key):
    result = []

    for start in range(0, len(tokens), BLOCK_SIZE):
        block = tokens[start:start + BLOCK_SIZE]
        order = block_permutation(
            key, start // BLOCK_SIZE, len(block)
        )

        result.extend(block[position] for position in order)

    return result


def restore_token_order(tokens, key):
    result = []

    for start in range(0, len(tokens), BLOCK_SIZE):
        block = tokens[start:start + BLOCK_SIZE]
        order = block_permutation(
            key, start // BLOCK_SIZE, len(block)
        )

        restored = [None] * len(block)

        for transmitted, original in enumerate(order):
            restored[original] = block[transmitted]

        result.extend(restored)

    return result


# ------------------------------------------------------------
# 5. EDC ENCRYPTION WITH CIPHERTEXT FEEDBACK
# ------------------------------------------------------------

def encrypt(message, key):
    key = normalise_key(key)
    message = normalise_plaintext(message)

    _, coordinates = build_matrix(key)
    previous_row, previous_col = initial_feedback(key)

    tokens = []

    for index, char in enumerate(message):
        plain_row, plain_col = coordinates[char]
        key_row, key_col = key_coordinate(
            key[index % len(key)]
        )

        # Dynamic shifting and previous-ciphertext feedback.
        row = (
            plain_row + key_row + index + previous_row
        ) % 6

        col = (
            plain_col + key_col + 2 * index + previous_col
        ) % 6

        tokens.append(coordinate_to_token(row, col))

        previous_row, previous_col = row, col

    return "-".join(permute_tokens(tokens, key))


# ------------------------------------------------------------
# 6. EDC DECRYPTION
# ------------------------------------------------------------

def decrypt(ciphertext, key):
    key = normalise_key(key)

    if not isinstance(ciphertext, str) or not ciphertext.strip():
        raise ValueError("Ciphertext cannot be empty")

    transmitted = ciphertext.upper().split("-")

    for token in transmitted:
        token_to_coordinate(token)

    # Reverse block permutation.
    tokens = restore_token_order(transmitted, key)

    matrix, _ = build_matrix(key)
    previous_row, previous_col = initial_feedback(key)

    recovered = []

    for index, token in enumerate(tokens):
        row, col = token_to_coordinate(token)

        key_row, key_col = key_coordinate(
            key[index % len(key)]
        )

        # Reverse dynamic shifts and ciphertext feedback.
        plain_row = (
            row - key_row - index - previous_row
        ) % 6

        plain_col = (
            col - key_col - 2 * index - previous_col
        ) % 6

        recovered.append(matrix[plain_row][plain_col])

        previous_row, previous_col = row, col

    return "".join(recovered)


# ------------------------------------------------------------
# 7. HMAC-SHA256 AUTHENTICATION
# ------------------------------------------------------------

def generate_authentication_tag(ciphertext, auth_key):
    if not isinstance(auth_key, bytes) or len(auth_key) != 32:
        raise ValueError("HMAC key must be exactly 32 bytes")

    if not isinstance(ciphertext, str) or not ciphertext:
        raise ValueError("Ciphertext cannot be empty")

    return hmac.new(
        auth_key,
        AUTH_CONTEXT + ciphertext.encode("utf-8"),
        digestmod="sha256"
    ).hexdigest()


def decrypt_authenticated(ciphertext, tag, key, auth_key):
    # Verify authenticity BEFORE decryption.
    if not isinstance(tag, str) or not re.fullmatch(
        r"[0-9a-f]{64}", tag
    ):
        raise ValueError("Invalid authentication tag")

    expected = generate_authentication_tag(
        ciphertext, auth_key
    )

    if not hmac.compare_digest(tag, expected):
        raise ValueError(
            "HMAC failed: packet rejected before decryption"
        )

    return decrypt(ciphertext, key)


def encrypt_authenticated(message, key, auth_key):
    ciphertext = encrypt(message, key)

    tag = generate_authentication_tag(
        ciphertext, auth_key
    )

    return ciphertext, tag


# ------------------------------------------------------------
# 8. TCP PACKET ENCODER AND DECODER
# ------------------------------------------------------------

class EDCCodec:
    """Encode and decode EDC_PACKET messages."""

    def __init__(self, edc_key, hmac_key):
        self.edc_key = normalise_key(edc_key)

        if not isinstance(hmac_key, bytes) or len(hmac_key) != 32:
            raise ValueError("HMAC key must contain 32 bytes")

        self.hmac_key = hmac_key

        # Track received packet nonces to reject replay attacks.
        self.seen = set()
        self.recent = deque()

    @classmethod
    def from_environment(cls):
        """Load the pre-shared keys from environment variables."""
        try:
            key = os.environ["EDC_KEY"]
            auth_key = bytes.fromhex(
                os.environ["EDC_HMAC_KEY_HEX"]
            )
        except (KeyError, ValueError) as error:
            raise ValueError(
                "Set EDC_KEY and EDC_HMAC_KEY_HEX"
            ) from error

        return cls(key, auth_key)

    def encode(self, message):
        """Encrypt a JSON message for the Raspberry Pi."""
        if not isinstance(message, dict) or not isinstance(
            message.get("type"), str
        ):
            raise ValueError("Expected message dictionary with type")

        inner = {
            "v": 1,
            "sent_at": int(time.time()),
            "nonce": secrets.token_hex(16).upper(),
            "message": message
        }

        raw = json.dumps(
            inner,
            separators=(",", ":"),
            allow_nan=False
        ).encode("utf-8")

        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("Message too large")

        # EDC supports A-Z and 0-9, so encode JSON as Base32.
        plaintext = base64.b32encode(
            raw
        ).decode("ascii").rstrip("=")

        ciphertext, tag = encrypt_authenticated(
            plaintext,
            self.edc_key,
            self.hmac_key
        )

        return {
            "type": "EDC_PACKET",
            "v": 1,
            "ciphertext": ciphertext,
            "tag": tag
        }

    def decode(self, packet):
        """Verify HMAC, decrypt, and reject replayed packets."""

        if (
            not isinstance(packet, dict)
            or set(packet) != {"type", "v", "ciphertext", "tag"}
            or packet.get("type") != "EDC_PACKET"
            or type(packet.get("v")) is not int
            or packet["v"] != 1
        ):
            raise ValueError("Expected EDC_PACKET")

        ciphertext = packet["ciphertext"]
        tag = packet["tag"]

        if (
            not isinstance(ciphertext, str)
            or len(ciphertext) > MAX_WIRE_BYTES
        ):
            raise ValueError("Invalid or oversized ciphertext")

        # HMAC is checked before plaintext recovery.
        plaintext = decrypt_authenticated(
            ciphertext,
            tag,
            self.edc_key,
            self.hmac_key
        )

        if not BASE32_PATTERN.fullmatch(plaintext):
            raise ValueError("Invalid Base32 data")

        try:
            raw = base64.b32decode(
                plaintext + "=" * (-len(plaintext) % 8)
            )

            if len(raw) > MAX_JSON_BYTES:
                raise ValueError("Decoded message too large")

            inner = json.loads(raw.decode("utf-8"))

        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("Invalid decrypted JSON") from error

        if (
            not isinstance(inner, dict)
            or set(inner) != {"v", "sent_at", "nonce", "message"}
        ):
            raise ValueError("Invalid authenticated fields")

        if type(inner["v"]) is not int or inner["v"] != 1:
            raise ValueError("Unsupported packet version")

        timestamp = inner["sent_at"]
        nonce = inner["nonce"]
        message = inner["message"]
        now = int(time.time())

        # Reject packets outside the allowed time window.
        if (
            type(timestamp) is not int
            or abs(now - timestamp) > MAX_SKEW_SECONDS
        ):
            raise ValueError(
                "Expired packet or unsynchronized clocks"
            )

        if (
            not isinstance(nonce, str)
            or not re.fullmatch(r"[0-9A-F]{32}", nonce)
        ):
            raise ValueError("Invalid nonce")

        if (
            not isinstance(message, dict)
            or not isinstance(message.get("type"), str)
        ):
            raise ValueError("Invalid GUI message")

        # Remove expired nonce records.
        while (
            self.recent
            and now - self.recent[0][0] > REPLAY_CACHE_SECONDS
        ):
            _, old_nonce = self.recent.popleft()
            self.seen.discard(old_nonce)

        if nonce in self.seen:
            raise ValueError("Replay detected")

        self.seen.add(nonce)
        self.recent.append((now, nonce))

        return message
