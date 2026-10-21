"""Network service: always called through the controller's background executor."""
import logging
import requests

DELIVERY_ERRORS = {
    "proxy": "Telegram proxy connection failed. Check your proxy or VPN settings.",
    "tls": "Telegram TLS certificate verification failed. Check Windows date/time, certificates, or HTTPS inspection settings.",
    "connect_timeout": "Connection to Telegram timed out. Check internet access and retry.",
    "read_timeout": "Telegram did not respond in time. Check the chat before requesting another code.",
    "connection": "Cannot connect to Telegram. Check Wi-Fi, DNS, firewall, or VPN settings.",
    "network": "Telegram network request failed. Retry when internet access is available.",
    "response": "Telegram returned an invalid response. Check your network or proxy.",
    "token": "Telegram rejected the saved bot token. It may have been revoked.",
    "forbidden": "Telegram refused delivery. Open your bot chat, unblock it, and press Start.",
    "chat": "Telegram could not deliver to the saved chat. Check that your bot chat is started.",
    "rate": "Telegram rate limit reached. Wait before requesting another code.",
    "server": "Telegram is temporarily unavailable. Try again shortly.",
}


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
                json={"chat_id": self.chat_id, "text": message}, timeout=(5, 15))
            success = response.status_code == 200 and response.json().get("ok", False) is True
            logging.info("Telegram request: %s", "sent" if success else "failed")
            if success:
                return True, "Sent"
            reason = {400: "chat", 401: "token", 404: "token", 403: "forbidden", 429: "rate"}.get(response.status_code, "server")
        except requests.exceptions.ProxyError:
            reason = "proxy"
        except requests.exceptions.SSLError:
            reason = "tls"
        except requests.exceptions.ConnectTimeout:
            reason = "connect_timeout"
        except requests.exceptions.ReadTimeout:
            reason = "read_timeout"
        except requests.ConnectionError:
            reason = "connection"
        except requests.RequestException:
            reason = "network"
        except ValueError:
            reason = "response"
        # Never log exception strings: requests URLs contain the bot token.
        logging.warning("Telegram delivery error category: %s", reason)
        return False, DELIVERY_ERRORS[reason]

    def send_alert(self, alert):
        return self.send_message(f"[{alert.severity}] {alert.title}\n{alert.message}\n{alert.timestamp}")

    def test_connection(self):
        return self.send_message("PetCare AI connection test. Behaviour monitoring notifications are ready.")
