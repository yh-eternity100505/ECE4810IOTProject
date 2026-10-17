"""Network service: always called through the controller's background executor."""
import logging
import requests


class TelegramService:
    def __init__(self, mode="Mock", token="", chat_id="", enabled=True):
        self.mode, self.token, self.chat_id, self.enabled = mode, token, chat_id, enabled
        self.network_available = True

    def send_message(self, message: str) -> tuple[bool, str]:
        if not self.enabled:
            return False, "Disabled"
        if not self.network_available:
            logging.warning("Telegram network offline")
            return False, "Failed · network offline"
        if self.mode == "Mock":
            logging.info("[MOCK TELEGRAM] %s", message)
            return True, "Sent · Mock"
        if not self.token or not self.chat_id:
            return False, "Not configured"
        try:
            response = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": message}, timeout=(3, 5))
            success = response.status_code == 200 and response.json().get("ok", False)
            logging.info("Telegram request: %s", "sent" if success else "failed")
            return success, "Sent" if success else "Failed · check credentials"
        except (requests.RequestException, ValueError):
            # Exception text can contain the URL/token. Do not log it.
            logging.warning("Telegram request failed (network or invalid response)")
            return False, "Failed · network unavailable"

    def send_alert(self, alert):
        return self.send_message(f"[{alert.severity}] {alert.title}\n{alert.message}\n{alert.timestamp}")

    def test_connection(self):
        return self.send_message("PetCare AI connection test. Behaviour monitoring notifications are ready.")
