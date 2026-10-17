# Raspberry Pi movement integration

Launch with `Launch PetCare AI.vbs`. Open **Raspberry Pi movement access** on the login page, or **Movement & Access** in the sidebar. Set the listen address (normally `0.0.0.0`) and port (default `5005`), then start the receiver. It remains active while logged out so a gesture can start authentication. Stop closes the receiver and invalidates any challenge. The receiver is off until explicitly started.

On the Pi, keep GPIO acquisition, the six-second recording, feature extraction and KNN prediction in the existing long-running program. Load the model once. Connect to the laptop's LAN IPv4 address, not `0.0.0.0`. Allow inbound TCP on the selected port in Windows Firewall if needed. Reconnect after a dropped connection. Do not import the Pi's GPIO script in the GUI.

Messages are UTF-8 JSON objects, each followed by a newline. Several messages can share a connection; TCP fragments are assembled before parsing. Maximum message size is 4096 bytes. Supported examples:

```json
{"type":"IDLE"}
{"type":"TRIGGERED"}
{"type":"RECORDING"}
{"type":"MOVEMENT_RESULT","prediction":"AUTH_GESTURE","confidence":0.94}
```

`class` is also accepted as an alias for `prediction`. Confidence must be a finite number between 0 and 1. Only `PASS_BY`, `APPROACH_STAY` and `AUTH_GESTURE` are accepted. Invalid clients are disconnected; the listener remains available. No raw distances or features are needed.

Pi sender snippet (insert into the existing program; this is not a replacement for GPIO acquisition):

```python
import json
import socket

with socket.create_connection(("YOUR_LAPTOP_IPV4", 5005), timeout=5) as connection:
    def send(message):
        connection.sendall((json.dumps(message) + "\n").encode("utf-8"))

    send({"type": "TRIGGERED"})
    # sequence = record_movement(distance)
    # prediction, confidence = classify_movement(sequence)
    # Return both values from classify_movement in the Pi script.
    send({"type": "MOVEMENT_RESULT", "prediction": "AUTH_GESTURE", "confidence": 0.94})
    send({"type": "IDLE"})
```

The GUI records up to 100 movement results with laptop receipt timestamps separately from pet behaviour history. Pass-by only records an event. Approach-and-stay raises a security warning in Alerts and uses the existing Telegram notification settings. An authentication gesture opens the movement panel and allows a registered username to request an OTP within two minutes. A gesture alone never creates a session.

OTPs expire after two minutes, allow five verification attempts, are single-use, and have a 30-second request cooldown. In Real Telegram mode, access requires successful delivery and verification. Configure the bot token and chat ID in Settings → Connections after normal password login. This prototype uses the configured Telegram account for registered users; it does not provide individual per-user Telegram enrollment. In Mock mode, a clearly labeled demo code appears in the movement panel. Mock OTPs are never sent to the Telegram logger. Codes are not stored in SQLite or event history. Logout, stop and a new authentication gesture invalidate pending codes.

This receiver uses plain TCP on a trusted lab network. Movement messages do not inherit the existing simulated sensor encryption/HMAC verification. Production use requires authenticated transport and per-user Telegram enrollment. Pet monitoring and its demo KNN remain separate from the real Pi movement classifier.

## Local check without Raspberry Pi

Start the receiver, then run this from a second PowerShell window:

```powershell
.\.venv-local\Scripts\python.exe -c 'import socket,json; s=socket.create_connection(("127.0.0.1",5005)); s.sendall((json.dumps({"type":"MOVEMENT_RESULT","prediction":"AUTH_GESTURE","confidence":0.94})+"\n").encode()); s.close()'
```

Enter `admin`, request an OTP, and verify the displayed mock code. You can send the other two predictions with the same command to verify logging and alerts.

Run automated checks with `.\.venv-local\Scripts\python.exe -m unittest discover -s tests -v`.
