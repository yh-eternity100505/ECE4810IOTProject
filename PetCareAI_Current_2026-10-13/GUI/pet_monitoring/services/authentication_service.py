"""Password-hash verification and expiring local sessions; no plaintext storage."""
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    username: str
    expires_at: float
    identifier: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)


class AuthenticationService:
    # Initial demo account is stored as a hash, not a recoverable password.
    DEMO_USERNAME = "admin"
    DEMO_SALT = "7e9f1f8022160f64b98bdf246c69477a"
    DEMO_HASH = "4af4c8c40849a084eaf1a8a1b5c386050532bf7eb8e857883506c4584b1d6acb"
    ITERATIONS = 600000

    def __init__(self, database=None, event_sink=None, session_seconds=8 * 3600):
        self.database, self.event_sink = database, event_sink
        self.session_seconds = session_seconds
        self.session = None
        self.failed_attempts = 0
        self._locked_until = 0
        default = {self.DEMO_USERNAME: {"salt": self.DEMO_SALT, "hash": self.DEMO_HASH, "iterations": self.ITERATIONS}}
        self.users = database.get("authentication_users", default) if database else default
        if database and database.get("authentication_users") is None:
            database.set("authentication_users", default)

    @property
    def is_authenticated(self):
        return self.session is not None and time.monotonic() < self.session.expires_at

    def _event(self, name):
        logging.info("Authentication event: %s", name)
        if self.event_sink:
            self.event_sink(name)

    def authenticate(self, username: str, password: str) -> bool:
        self.session = None
        if time.monotonic() < self._locked_until:
            self._event("LOGIN_LOCKED")
            return False
        record = self.users.get(username.strip(), {"salt": self.DEMO_SALT, "hash": self.DEMO_HASH, "iterations": self.ITERATIONS})
        try:
            candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(record["salt"]), record["iterations"])
            valid = username.strip() in self.users and hmac.compare_digest(candidate.hex(), record["hash"])
        except (ValueError, TypeError, KeyError):
            valid = False
        if valid:
            self.session = Session(username.strip(), time.monotonic() + self.session_seconds)
            self.failed_attempts = 0
            self._event("LOGIN_SUCCESS")
        else:
            self.failed_attempts += 1
            self._event("LOGIN_FAILURE")
            if self.failed_attempts == 3:
                self._event("REPEATED_AUTHENTICATION_FAILURE")
            if self.failed_attempts >= 5:
                self._locked_until = time.monotonic() + 30
                self.failed_attempts = 0
                self._event("LOGIN_LOCKED")
        return valid

    def logout(self):
        self.session = None
        self._event("LOGOUT")

    def expire_session(self):
        self.session = None
        self._event("SESSION_EXPIRED")

    def set_credentials(self, username, password):
        """Provision from a trusted local setup flow; never log the arguments."""
        if not username.strip() or len(password) < 6:
            raise ValueError("A username and a password of at least six characters are required")
        salt = secrets.token_bytes(16)
        password_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, self.ITERATIONS).hex()
        self.users = {username.strip(): {"salt": salt.hex(), "hash": password_hash, "iterations": self.ITERATIONS}}
        if self.database:
            self.database.set("authentication_users", self.users)
        self.session = None
