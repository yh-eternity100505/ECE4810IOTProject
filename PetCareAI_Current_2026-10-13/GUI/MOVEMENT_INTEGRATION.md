# Raspberry Pi movement integration

Launch with `Launch PetCare AI.vbs`. Open **Raspberry Pi movement access** on the login page, or **Movement & Access** in the sidebar. Set the listen address (normally `0.0.0.0`) and port (default `5005`), then start the receiver. It remains active while logged out for monitoring and connection diagnostics. Normal GUI startup also starts the receiver and the existing SSH-managed sender. Stop closes the receiver. Movement cannot authenticate the administrator.

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

The GUI records up to 100 movement results with laptop receipt timestamps separately from pet behaviour history. Pass-by records an event. Approach-and-stay raises a security warning in Alerts and uses the existing Telegram notification settings. An authentication gesture opens the movement panel, where a notice directs the user to the normal administrator login.

**Gestures and movement-panel codes cannot create administrator sessions.** There is no mock movement-login fallback. The administrator must use the existing login page: password first, then request and verify a Telegram OTP or use a single-use recovery code. Set up the administrator once with `main.py --setup-admin`; see [README.md](README.md).

Messages shown below are protocol examples for plaintext tests only. Live encrypted connections retain the existing EDC + HMAC verification; see [EDC_INTEGRATION.md](EDC_INTEGRATION.md). Pi monitoring, configuration acknowledgments, encrypted transport and SSH startup are unchanged.

## Local check without Raspberry Pi

Start the receiver, then run this from a second PowerShell window:

```powershell
.\.venv-local\Scripts\python.exe -c 'import socket,json; s=socket.create_connection(("127.0.0.1",5005)); s.sendall((json.dumps({"type":"MOVEMENT_RESULT","prediction":"AUTH_GESTURE","confidence":0.94})+"\n").encode()); s.close()'
```

For this plaintext-only test use an isolated receiver without saved EDC keys or EDC requirements. A movement event must not change the login state. Send the other two predictions to verify logging and alerts. Use the encrypted sender for the normal live receiver.

Run automated checks with `.\.venv-local\Scripts\python.exe -m unittest discover -s tests -v`.
