import unittest
from unittest.mock import Mock, patch
import requests
from pet_monitoring.services.telegram_service import TelegramService, DELIVERY_ERRORS
from pet_monitoring.services.authentication_service import AuthenticationService


class TelegramDeliveryTests(unittest.TestCase):
    def test_network_errors_have_safe_specific_messages(self):
        cases = [(requests.exceptions.ProxyError, "proxy"),
                 (requests.exceptions.SSLError, "tls"),
                 (requests.exceptions.ConnectTimeout, "connect_timeout"),
                 (requests.exceptions.ReadTimeout, "read_timeout"),
                 (requests.ConnectionError, "connection"),
                 (requests.RequestException, "network"), (ValueError, "response")]
        for exception, category in cases:
            with self.subTest(category=category), patch("requests.post", side_effect=exception("private-token")):
                with self.assertLogs(level="WARNING") as captured:
                    success, message = TelegramService("Real", "private-token", "123").send_message("secret OTP")
                self.assertFalse(success)
                self.assertEqual(message, DELIVERY_ERRORS[category])
                self.assertNotIn("private-token", " ".join(captured.output) + message)
                self.assertNotIn("secret OTP", " ".join(captured.output) + message)

    def test_api_errors_are_classified_without_echoing_response(self):
        for status, category in [(400, "chat"), (401, "token"), (403, "forbidden"), (429, "rate"), (500, "server")]:
            with self.subTest(status=status), patch("requests.post", return_value=Mock(status_code=status)):
                self.assertEqual(TelegramService("Real", "private-token", "123").send_message("secret"),
                                 (False, DELIVERY_ERRORS[category]))

    def test_success_uses_bounded_timeouts(self):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True}
        with patch("requests.post", return_value=response) as send:
            self.assertEqual(TelegramService("Real", "token", "123").send_message("secret"), (True, "Sent"))
        self.assertEqual(send.call_args.kwargs["timeout"], (5, 15))

    def test_authentication_only_displays_known_sanitized_delivery_errors(self):
        self.assertIn("TLS", AuthenticationService._delivery_error(DELIVERY_ERRORS["tls"]))
        self.assertNotIn("private-token", AuthenticationService._delivery_error("private-token"))
