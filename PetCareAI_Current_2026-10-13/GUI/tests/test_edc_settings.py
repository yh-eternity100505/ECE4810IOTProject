import contextlib
import base64
import io
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from pet_monitoring.main import main
from pet_monitoring.security import edc_settings as settings
from pet_monitoring.services.movement_receiver import MovementReceiver

APP = QApplication.instance() or QApplication([])
KEY = "EDCSETUPTEST2026"
HMAC_HEX = bytes(range(32)).hex()


@unittest.skipUnless(os.name == "nt", "Saved keys use Windows DPAPI")
class SavedKeyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.key_file = Path(self.folder.name) / "keys.dpapi"
        self.path_patch = patch.object(settings, "KEY_FILE", self.key_file)
        self.path_patch.start()
        # Exercise persistence deterministically when the sandbox has no DPAPI profile.
        self.protection_patch = patch.object(settings.CredentialService, "_crypt", side_effect=self.protect)
        self.protection_patch.start()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.protection_patch.stop)

    @staticmethod
    def protect(raw, encrypt):
        if encrypt:
            return b"TEST-PROTECTED:" + base64.b64encode(raw)
        if not raw.startswith(b"TEST-PROTECTED:"):
            raise OSError("Invalid protected data")
        return base64.b64decode(raw.split(b":", 1)[1], validate=True)

    def test_saved_keys_decrypt_without_environment_flags(self):
        settings.save_keys(KEY, HMAC_HEX)
        stored = self.key_file.read_bytes()
        self.assertNotIn(KEY.encode(), stored)
        self.assertNotIn(HMAC_HEX.encode(), stored)
        codec = settings.load_codec()
        message = {"type": "MOVEMENT_RESULT", "sensor_id": 5, "prediction": "PASS_BY", "confidence": .9}
        with patch.dict(os.environ, {"PETCARE_EDC": "0"}, clear=True):
            receiver = MovementReceiver()
            try:
                self.assertTrue(receiver.start("127.0.0.1", 0))
                self.assertTrue(receiver._secure)
                self.assertEqual(receiver._codec.decode(codec.encode(message)), message)
            finally:
                receiver.stop()

    def test_real_windows_protection_roundtrip_when_available(self):
        self.protection_patch.stop()
        try:
            settings.save_keys(KEY, HMAC_HEX)
        except OSError:
            self.skipTest("Windows DPAPI profile unavailable in this execution environment")
        self.assertEqual(settings.load_codec().edc_key, KEY)

    def test_unreadable_saved_keys_do_not_fall_back_to_plaintext(self):
        self.key_file.write_bytes(b"damaged protected keys")
        with patch.dict(os.environ, {}, clear=True):
            receiver = MovementReceiver()
            self.assertFalse(receiver.start("127.0.0.1", 0))
            self.assertIsNone(receiver._thread)

    def test_invalid_or_failed_replacement_preserves_existing_keys(self):
        settings.save_keys(KEY, HMAC_HEX)
        original = self.key_file.read_bytes()
        with self.assertRaises(ValueError):
            settings.save_keys(KEY, "bad")
        with patch.object(settings.CredentialService, "_crypt", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                settings.save_keys("REPLACEMENT", HMAC_HEX)
        self.assertEqual(self.key_file.read_bytes(), original)
        self.assertEqual(settings.load_codec().edc_key, KEY)

    def test_one_time_setup_command_saves_keys_without_launching_gui(self):
        output = io.StringIO()
        with patch("getpass.getpass", side_effect=[KEY, HMAC_HEX]), contextlib.redirect_stdout(output):
            self.assertEqual(main(["--setup-edc"]), 0)
        self.assertTrue(settings.has_saved_keys())
        self.assertEqual(settings.load_codec().edc_key, KEY)
        self.assertNotIn(KEY, output.getvalue())
        self.assertNotIn(HMAC_HEX, output.getvalue())
