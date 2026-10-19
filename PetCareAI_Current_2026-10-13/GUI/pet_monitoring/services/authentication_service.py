"""Single admin: Argon2id password, then Telegram OTP or recovery code.

The native credential store is authoritative, independent of the monitoring DB.
A permanent installation marker prevents lost credentials reopening enrollment.
"""
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pyotp
from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import Argon2Error
from pet_monitoring.security.admin_store import AdminCredentialStore, CredentialStoreError

DEFAULT_MARKER = Path(__file__).resolve().parents[2] / "data" / "admin_setup.lock"
STORAGE_ERRORS = (CredentialStoreError, OSError, ValueError, TypeError, KeyError, Argon2Error)


@dataclass
class Session:
    username: str
    expires_at: float
    identifier: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    credential_version: str = field(default="", repr=False)


@dataclass(repr=False)
class Enrollment:
    password_hash: str
    secret: str
    recovery_codes: list[str]
    identifier: str = field(default_factory=lambda: secrets.token_hex(16))
    expires_at: float = field(default_factory=lambda: time.monotonic() + 900)
    verified_step: int = -1
    attempts: int = 0
    telegram_token: str = ""
    telegram_chat_id: str = ""
    last_request: float = -float("inf")

    @property
    def uri(self):
        return pyotp.TOTP(self.secret).provisioning_uri("admin", issuer_name="PetCare AI")


@dataclass(repr=False)
class TelegramChallenge:
    identifier: str
    digest: str
    expires_at: float
    delivered: bool = False


class AuthenticationService:
    USERNAME = "admin"
    MIN_PASSWORD_LENGTH = 8
    MAX_PASSWORD_LENGTH = 1024
    hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, type=Type.ID)

    def __init__(self, database=None, event_sink=None, session_seconds=8 * 3600,
                 *, store=None, marker_path=None, telegram_sender=None):
        self.database, self.event_sink = database, event_sink
        self.session_seconds = session_seconds
        self.store = store if store is not None else AdminCredentialStore()
        # NOT derived from --database: a different monitoring DB must never
        # create another administrator or reset enrollment.
        self.marker = Path(marker_path) if marker_path is not None else DEFAULT_MARKER
        self.session = self._pending = self._enrollment = None
        self._setup_otp = self._login_otp = None
        self._reset_otp = self._reset_context = self._reset_ticket = None
        self._otp_key = secrets.token_bytes(32)
        self._telegram_sender = telegram_sender or self._send_telegram
        self.factor_mode = "telegram"
        self.error = self.last_error = ""
        self.state = "blocked"
        self.refresh()

    @contextmanager
    def _exclusive(self):
        """Serialize setup and OTP/recovery consumption across app processes."""
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        with self.marker.with_suffix(".mutex").open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _validate(record):
        if not isinstance(record, dict) or record.get("v") not in (1, 2) or record.get("username") != "admin":
            raise ValueError("Invalid administrator record")
        if not re.fullmatch(r"[0-9a-f]{32}", record["id"]):
            raise ValueError("Invalid enrollment identifier")
        params = extract_parameters(record["password_hash"])
        if (params.type != Type.ID or params.version != 19 or params.time_cost != 3
                or params.memory_cost != 65536 or params.parallelism != 4
                or params.hash_len != 32 or params.salt_len != 16):
            raise ValueError("Invalid Argon2id parameters")
        if record["v"] == 1:
            # Preserve access for administrators who completed the previous
            # authenticator enrollment; never silently reset their account.
            if not re.fullmatch(r"[A-Z2-7]{32}", record["secret"]):
                raise ValueError("Invalid authenticator secret")
        else:
            AuthenticationService._validate_telegram(record["telegram_token"], record["telegram_chat_id"])
            if not re.fullmatch(r"(?:[0-9a-f]{32})?", record["otp_id"]):
                raise ValueError("Invalid OTP identifier")
            if (type(record["last_sent"]) not in (int, float)
                    or not math.isfinite(record["last_sent"]) or record["last_sent"] < 0):
                raise ValueError("Invalid resend state")
        codes = record["recovery"]
        if (not isinstance(codes, list) or len(codes) > 8
                or any(not isinstance(code, str) or not re.fullmatch(r"[0-9a-f]{64}", code) for code in codes)
                or len(set(codes)) != len(codes)):
            raise ValueError("Invalid recovery hashes")
        for key in (("last_step", "failures") if record["v"] == 1 else ("failures",)):
            if type(record[key]) is not int or record[key] < (-1 if key == "last_step" else 0):
                raise ValueError("Invalid authentication counters")
        if (type(record["locked_until"]) not in (int, float)
                or not math.isfinite(record["locked_until"]) or record["locked_until"] < 0):
            raise ValueError("Invalid lockout state")
        return record

    def _load(self):
        raw = self.store.read()
        if raw is None and not self.marker.exists():
            return None
        if raw is None:
            raise CredentialStoreError("Credentials missing; setup remains disabled")
        record = self._validate(json.loads(raw))
        if self.marker.read_text(encoding="ascii").strip() != record["id"]:
            raise CredentialStoreError("Setup marker missing or invalid")
        return record

    def _save(self, record):
        self._validate(record)
        self.store.write(json.dumps(record, separators=(",", ":")))

    def _blocked(self):
        self.state = "blocked"
        self.error = "Administrator credentials are unavailable or damaged. Setup is disabled; restore the original credentials and setup marker."
        self.last_error = self.error
        self.session = self._pending = None
        self._login_otp = None
        self.cancel_password_reset()

    def refresh(self):
        try:
            record = self._load()
            self.state = "ready" if record else "unconfigured"
            self.factor_mode = "authenticator" if record and record["v"] == 1 else "telegram"
            self.error = "" if record else "Administrator setup required. Close this app and run main.py --setup-admin locally."
        except STORAGE_ERRORS:
            self._blocked()
        return self.state

    @property
    def is_authenticated(self):
        if self.session is None or time.monotonic() >= self.session.expires_at:
            return False
        try:
            record = self._load()
            valid = record is not None and self.session.credential_version == self._credential_version(record)
        except STORAGE_ERRORS:
            valid = False
        if not valid:
            # Retain the expired session until the controller emits its normal
            # session-expiry signal and returns an open dashboard to login.
            self.session.expires_at = 0
        return valid

    @staticmethod
    def _credential_version(record):
        return hashlib.sha256(record["password_hash"].encode("ascii")).hexdigest()

    @property
    def awaiting_otp(self):
        return self._pending is not None and time.monotonic() < self._pending[1]

    def _event(self, name):
        logging.info("Authentication event: %s", name)
        if self.event_sink:
            self.event_sink(name)

    def begin_setup(self, password, confirmation, *, telegram_token=None, telegram_chat_id=None):
        self.cancel_setup()
        with self._exclusive():
            if self._load() is not None:
                raise ValueError("Administrator setup is already complete and cannot be reopened.")
        if password != confirmation:
            raise ValueError("Passwords do not match.")
        if not self.MIN_PASSWORD_LENGTH <= len(password) <= self.MAX_PASSWORD_LENGTH:
            raise ValueError(f"Choose a password between {self.MIN_PASSWORD_LENGTH} and {self.MAX_PASSWORD_LENGTH} characters.")
        telegram = telegram_token is not None or telegram_chat_id is not None
        if telegram:
            telegram_token = (telegram_token or "").strip()
            telegram_chat_id = (telegram_chat_id or "").strip()
            self._validate_telegram(telegram_token, telegram_chat_id)
        codes = []
        for _ in range(8):
            code = secrets.token_hex(16).upper()
            codes.append("-".join(code[i:i + 8] for i in range(0, 32, 8)))
        self._enrollment = Enrollment(self.hasher.hash(password), pyotp.random_base32(), codes)
        if telegram:
            self._enrollment.secret = ""
            self._enrollment.telegram_token = telegram_token
            self._enrollment.telegram_chat_id = telegram_chat_id
        return self._enrollment

    def telegram_defaults(self):
        """Called only on the UI thread; never expose secrets in logs."""
        if self.database is None:
            return "", ""
        from pet_monitoring.services.credential_service import CredentialService
        try:
            token = CredentialService(self.database).load()
            chat_id = str(self.database.get("settings", {}).get("telegram_chat_id", ""))
            return token, chat_id
        except (OSError, ValueError, TypeError):
            return "", ""

    @staticmethod
    def _validate_telegram(token, chat_id):
        if not isinstance(token, str) or not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,100}", token):
            raise ValueError("Enter a valid Telegram bot token.")
        if not isinstance(chat_id, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", chat_id):
            raise ValueError("Enter your private Telegram chat ID (positive digits, not a group or channel).")

    @staticmethod
    def _send_telegram(token, chat_id, message):
        from pet_monitoring.services.telegram_service import TelegramService
        # Always REAL delivery. Alert mock mode must never expose a login OTP.
        return TelegramService("Real", token, chat_id, True).send_message(message)

    def _new_otp(self, expires_at):
        code = f"{secrets.randbelow(1000000):06d}"
        identifier = secrets.token_hex(16)
        digest = hmac.new(self._otp_key, (identifier + code).encode("ascii"), "sha256").hexdigest()
        return code, TelegramChallenge(identifier, digest, expires_at)

    def _check_otp(self, challenge, code):
        if (challenge is None or not challenge.delivered
                or time.monotonic() >= challenge.expires_at or not re.fullmatch(r"[0-9]{6}", code.strip())):
            return False
        digest = hmac.new(self._otp_key, (challenge.identifier + code.strip()).encode("ascii"), "sha256").hexdigest()
        return hmac.compare_digest(challenge.digest, digest)

    def request_setup_otp(self):
        enrollment = self._enrollment
        if (enrollment is None or not enrollment.telegram_token
                or time.monotonic() >= enrollment.expires_at or enrollment.attempts >= 5):
            return False, "Start local setup again before requesting a code."
        now = time.monotonic()
        if now - enrollment.last_request < 30:
            return False, "Wait 30 seconds before requesting another code."
        enrollment.last_request = now
        enrollment.verified_step = -1
        code, challenge = self._new_otp(min(now + 120, enrollment.expires_at))
        self._setup_otp = challenge
        try:
            success, _ = self._telegram_sender(enrollment.telegram_token, enrollment.telegram_chat_id,
                f"PetCare AI administrator SETUP code: {code}\nExpires in 2 minutes. Do not share this code.")
        except Exception:
            success = False
        if self._enrollment is not enrollment or self._setup_otp is not challenge:
            return False, "Setup request cancelled."
        challenge.delivered = success is True and time.monotonic() < challenge.expires_at
        return (True, "Code sent to Telegram. Enter it within 2 minutes.") if challenge.delivered else (False, "Telegram delivery failed. Check the bot token, private chat ID, internet, and that you pressed Start in your bot chat.")

    def request_login_otp(self):
        """Background-safe: only native storage/network, no SQLite or UI access."""
        pending = self._pending
        if not self.awaiting_otp:
            return False, "Enter your password again before requesting a code."
        try:
            with self._exclusive():
                record = self._load()
                if record is None or record["id"] != pending[0] or record["v"] != 2:
                    return False, "Telegram OTP is unavailable for this sign-in."
                if self._credential_version(record) != pending[2]:
                    self.cancel_login()
                    return False, "The password changed. Sign in again with the new password."
                if time.time() < record["locked_until"]:
                    return False, "Sign-in temporarily locked. Try again shortly."
                if time.time() - record["last_sent"] < 30:
                    return False, "Wait 30 seconds before requesting another code."
                code, challenge = self._new_otp(min(time.monotonic() + 120, pending[1]))
                record["otp_id"] = challenge.identifier
                record["last_sent"] = time.time()
                self._save(record)
                self._login_otp = challenge
                token, chat_id = record["telegram_token"], record["telegram_chat_id"]
            try:
                success, _ = self._telegram_sender(token, chat_id,
                    f"PetCare AI administrator LOGIN code: {code}\nExpires in 2 minutes. Do not share this code.")
            except Exception:
                success = False
            with self._exclusive():
                record = self._load()
                if (record is None or record["otp_id"] != challenge.identifier
                        or self._pending != pending or self._login_otp is not challenge):
                    return False, "OTP request cancelled or replaced."
                challenge.delivered = success is True and self.awaiting_otp and time.monotonic() < challenge.expires_at
            return (True, "Code sent to Telegram. Enter it within 2 minutes.") if challenge.delivered else (False, "Telegram delivery failed. Check your internet and bot chat, then retry or use a recovery code.")
        except STORAGE_ERRORS:
            self._blocked()
            return False, self.last_error

    def verify_setup_otp(self, code):
        enrollment = self._enrollment
        if enrollment is None or time.monotonic() >= enrollment.expires_at or enrollment.attempts >= 5:
            self.cancel_setup()
            raise ValueError("Enrollment expired. Start local setup again.")
        enrollment.attempts += 1
        if enrollment.telegram_token:
            if not self._check_otp(self._setup_otp, code):
                raise ValueError("Invalid, expired or undelivered Telegram code. Request a code and try again.")
            enrollment.verified_step = 0
            self._setup_otp = None
            return
        step = self._matching_step(enrollment.secret, code, -1)
        if step is None:
            raise ValueError("Invalid authenticator code. Check the phone's time and try again.")
        enrollment.verified_step = step

    @staticmethod
    def _recovery_hash(code):
        return hashlib.sha256(code.replace("-", "").replace(" ", "").upper().encode("utf-8")).hexdigest()

    def complete_setup(self, recovery_saved=False):
        enrollment = self._enrollment
        if (enrollment is None or enrollment.verified_step < 0
                or time.monotonic() >= enrollment.expires_at or not recovery_saved):
            raise ValueError("Verify your OTP and save recovery codes before completing setup.")
        try:
            with self._exclusive():
                if self._load() is not None:
                    raise ValueError("Administrator setup is already complete.")
                record = {"v": 1, "id": enrollment.identifier, "username": "admin",
                          "password_hash": enrollment.password_hash, "secret": enrollment.secret,
                          "recovery": [self._recovery_hash(code) for code in enrollment.recovery_codes],
                          "last_step": enrollment.verified_step, "failures": 0, "locked_until": 0}
                if enrollment.telegram_token:
                    record.update(v=2, telegram_token=enrollment.telegram_token,
                                  telegram_chat_id=enrollment.telegram_chat_id, otp_id="", last_sent=0)
                    del record["secret"]
                    del record["last_step"]
                # Reserve permanently first. An interrupted commit locks setup
                # instead of silently offering a new enrollment.
                with self.marker.open("x", encoding="ascii") as handle:
                    handle.write(enrollment.identifier)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._save(record)
                if self.database is not None:
                    self.database.execute("DELETE FROM config WHERE key='authentication_users'")
            self.refresh()
            self._event("ADMIN_SETUP_COMPLETED")
        except Exception:
            self.refresh()
            raise
        finally:
            self.cancel_setup()

    def cancel_setup(self):
        self._enrollment = None
        self._setup_otp = None

    @staticmethod
    def _matching_step(secret, code, last_step):
        code = code.strip()
        if not re.fullmatch(r"[0-9]{6}", code):
            return None
        current = int(time.time() // 30)
        totp = pyotp.TOTP(secret)
        for step in (current, current - 1, current + 1):
            if step > last_step and hmac.compare_digest(totp.at(step * 30), code):
                return step
        return None

    def _failure(self, record, *, emit_event=True):
        record["failures"] += 1
        repeated = record["failures"] == 3
        if record["failures"] >= 5:
            record["locked_until"] = time.time() + 30
            record["failures"] = 0
            self._pending = None
            self.cancel_password_reset()
        self._save(record)
        self.last_error = "Sign-in failed or temporarily locked. Try again shortly."
        event = self._event if emit_event else lambda name: logging.info("Authentication event: %s", name)
        event("LOGIN_FAILURE")
        if repeated:
            event("REPEATED_AUTHENTICATION_FAILURE")

    def authenticate(self, username, password):
        """Verify only the password. Never create a session here."""
        self.session = self._pending = None
        self._login_otp = None
        self.cancel_password_reset()
        try:
            with self._exclusive():
                record = self._load()
                if record is None:
                    self.refresh()
                    self.last_error = self.error
                    return False
                if time.time() < record["locked_until"]:
                    self.last_error = "Sign-in temporarily locked. Try again shortly."
                    return False
                valid = False
                if isinstance(password, str) and len(password) <= self.MAX_PASSWORD_LENGTH:
                    try:
                        valid = self.hasher.verify(record["password_hash"], password)
                    except Argon2Error:
                        pass
                if username != "admin" or not valid:
                    self._failure(record)
                    return False
                self._pending = (record["id"], time.monotonic() + 120, self._credential_version(record))
                self.factor_mode = "telegram" if record["v"] == 2 else "authenticator"
                self.last_error = ""
                self._event("PASSWORD_VERIFIED")
                return True
        except STORAGE_ERRORS:
            self._blocked()
            return False

    def verify_second_factor(self, code, *, recovery=False):
        self.session = None
        if not self.awaiting_otp:
            self._pending = None
            self.last_error = "Enter your password again to start sign-in."
            return False
        try:
            with self._exclusive():
                record = self._load()
                if record is None or record["id"] != self._pending[0]:
                    self._blocked()
                    return False
                if self._credential_version(record) != self._pending[2]:
                    self.cancel_login()
                    self.last_error = "The password changed. Sign in again with the new password."
                    return False
                if time.time() < record["locked_until"]:
                    self._pending = None
                    self.last_error = "Sign-in temporarily locked. Try again shortly."
                    return False
                if recovery:
                    digest = self._recovery_hash(code)
                    matched = next((item for item in record["recovery"] if hmac.compare_digest(item, digest)), None)
                    if matched is None:
                        self._failure(record)
                        return False
                    record["recovery"].remove(matched)
                elif record["v"] == 2:
                    if (self._login_otp is None or record["otp_id"] != self._login_otp.identifier
                            or not self._check_otp(self._login_otp, code)):
                        self._failure(record)
                        return False
                else:
                    step = self._matching_step(record["secret"], code, record["last_step"])
                    if step is None:
                        self._failure(record)
                        return False
                    record["last_step"] = step
                record["failures"] = 0
                record["locked_until"] = 0
                if record["v"] == 2:
                    record["otp_id"] = ""
                # Persist consumption BEFORE creating the session.
                self._save(record)
                self._pending = None
                self._login_otp = None
                self.session = Session("admin", time.monotonic() + self.session_seconds,
                                       credential_version=self._credential_version(record))
                self.last_error = ""
                self._event("LOGIN_SUCCESS")
                return True
        except STORAGE_ERRORS:
            self._blocked()
            return False

    def cancel_login(self):
        self._pending = None
        self._login_otp = None

    def logout(self):
        self.session = self._pending = None
        self._login_otp = None
        self.cancel_password_reset()
        self._event("LOGOUT")

    def expire_session(self):
        self.session = self._pending = None
        self._login_otp = None
        self.cancel_password_reset()
        self._event("SESSION_EXPIRED")

    def cancel_password_reset(self):
        self._reset_otp = self._reset_context = self._reset_ticket = None

    def prepare_password_reset_request(self):
        """Reserve on the UI thread, so closing before a worker starts is safe."""
        self._reset_ticket = secrets.token_hex(16)
        return self._reset_ticket

    def request_password_reset_otp(self, recovery_code, request_id=None):
        """Require an unused recovery code before sending a RESET-purpose OTP.

        Uses only native storage and the network, so the UI may run it in a
        worker thread. No credential or recovery code changes until completion.
        """
        if request_id is None:
            request_id = self.prepare_password_reset_request()
        if self._reset_ticket != request_id:
            return False, "Password reset request cancelled."
        self.session = None
        self.cancel_login()
        try:
            with self._exclusive():
                if self._reset_ticket != request_id:
                    return False, "Password reset request cancelled."
                record = self._load()
                if record is None or record["v"] != 2:
                    return False, "Password recovery requires an existing Telegram-enrolled administrator. Setup remains disabled."
                if time.time() < record["locked_until"]:
                    return False, "Recovery temporarily locked. Wait 30 seconds and try again."
                digest = self._recovery_hash(recovery_code)
                if not any(hmac.compare_digest(item, digest) for item in record["recovery"]):
                    self._failure(record, emit_event=False)
                    return False, "Recovery code invalid or already used. Try an unused saved code."
                if time.time() - record["last_sent"] < 30:
                    return False, "Wait 30 seconds before requesting another code."
                code, challenge = self._new_otp(time.monotonic() + 120)
                context = (record["id"], self._credential_version(record), digest)
                if self._reset_ticket != request_id:
                    return False, "Password reset request cancelled."
                self._reset_context, self._reset_otp = context, challenge
                record["otp_id"] = challenge.identifier
                record["last_sent"] = time.time()
                self._save(record)
                token, chat = record["telegram_token"], record["telegram_chat_id"]
            try:
                success, _ = self._telegram_sender(token, chat,
                    f"PetCare AI administrator PASSWORD RESET code: {code}\nExpires in 2 minutes. Do not share this code. If you did not request a reset, ignore it.")
            except Exception:
                success = False
            with self._exclusive():
                record = self._load()
                if (record is None or record["otp_id"] != challenge.identifier
                        or self._reset_context is not context or self._reset_otp is not challenge
                        or self._reset_ticket != request_id
                        or self._credential_version(record) != context[1]):
                    return False, "Password reset request cancelled or replaced."
                challenge.delivered = success is True and time.monotonic() < challenge.expires_at
            return (True, "Reset code sent to your registered Telegram chat. Enter it and choose a new password within 2 minutes.") if challenge.delivered else (False, "Telegram delivery failed. Check your connection and bot chat, then request another code.")
        except STORAGE_ERRORS:
            self._blocked()
            return False, self.last_error

    def reset_password(self, otp, password, confirmation):
        """Reset this account only after Telegram AND unused recovery proof."""
        self.session = None
        if password != confirmation:
            self.last_error = "Passwords do not match."
            return False
        if not self.MIN_PASSWORD_LENGTH <= len(password) <= self.MAX_PASSWORD_LENGTH:
            self.last_error = f"Choose a password between {self.MIN_PASSWORD_LENGTH} and {self.MAX_PASSWORD_LENGTH} characters."
            return False
        context, challenge = self._reset_context, self._reset_otp
        if context is None or challenge is None:
            self.last_error = "Enter an unused recovery code and request a Telegram reset code first."
            return False
        try:
            with self._exclusive():
                record = self._load()
                if record is None or record["v"] != 2:
                    self._blocked()
                    return False
                if time.time() < record["locked_until"]:
                    self.cancel_password_reset()
                    self.last_error = "Recovery temporarily locked. Wait 30 seconds and try again."
                    return False
                if (record["id"] != context[0] or self._credential_version(record) != context[1]
                        or record["otp_id"] != challenge.identifier
                        or context[2] not in record["recovery"]):
                    self.cancel_password_reset()
                    self.last_error = "Recovery request is no longer valid. Request a new code with an unused recovery code."
                    return False
                if not self._check_otp(challenge, otp):
                    self._failure(record)
                    self.last_error = "Reset code invalid, expired, undelivered or temporarily locked. Request a new code if needed."
                    return False
                record["password_hash"] = self.hasher.hash(password)
                record["recovery"].remove(context[2])
                record.update(otp_id="", failures=0, locked_until=0)
                self._save(record)
            self.cancel_password_reset()
            self.cancel_login()
            self.last_error = ""
            self._event("PASSWORD_RESET_SUCCESS")
            return True
        except STORAGE_ERRORS:
            self._blocked()
            return False
