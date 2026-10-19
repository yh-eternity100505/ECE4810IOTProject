"""Test-only credentials; never read or change the user's native vault."""
import secrets
from pathlib import Path
import pyotp
from pet_monitoring.services.authentication_service import AuthenticationService

PASSWORD = "test-only-long-password-" + secrets.token_hex(8)


class MemoryStore:
    def __init__(self):
        self.value = None

    def read(self):
        return self.value

    def write(self, value):
        self.value = value


def configure_auth(controller, folder):
    auth = AuthenticationService(controller.db, controller._auth_event,
                                 store=MemoryStore(), marker_path=Path(folder) / "admin_setup.lock")
    controller.auth = auth
    enrollment = auth.begin_setup(PASSWORD, PASSWORD)
    auth._test_codes = list(enrollment.recovery_codes)
    auth.verify_setup_otp(pyotp.TOTP(enrollment.secret).now())
    auth.complete_setup(True)
    return auth


def login(auth):
    assert auth.authenticate("admin", PASSWORD)
    assert not auth.is_authenticated
    assert auth.verify_second_factor(auth._test_codes.pop(), recovery=True)


def login_page(page):
    page.username.setText("admin")
    page.password.setText(PASSWORD)
    page.login_button.click()
    assert not page.auth.is_authenticated
    page.recovery.setChecked(True)
    page.otp.setText(page.auth._test_codes.pop())
    page.verify_button.click()
