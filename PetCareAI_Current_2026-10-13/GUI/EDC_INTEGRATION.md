# Raspberry Pi EDC transport

The existing TCP receiver now supports the standalone EDC sender supplied on
15 October 2026. No bridge, extra GUI, database, or Raspberry Pi changes are needed.
EDC is an experimental educational cipher and does not provide production-grade security.
The existing simulated AES security demonstration remains separate from live Pi transport.

## Launch

On Windows, run this **once from the workspace root**:

```powershell
.\GUI\.venv-local\Scripts\python.exe .\GUI\main.py --setup-edc
```

Enter the same EDC key and HMAC key as the encrypted Pi sender. Input is hidden.
They are saved using the existing Windows DPAPI helper, protected for your Windows
account, in `GUI/data/edc_keys.dpapi`. This does not create another database.
Setup does not change or configure the Pi. The Pi must run the supplied encrypted
sender with matching keys and `--edc --gui-port 5005`.

After setup, launch normally:

```powershell
.\GUI\.venv-local\Scripts\python.exe .\GUI\main.py
```

Select **Live Pi** and start the existing receiver on **5005**. Saved keys enable
verification and decryption automatically, even if an old terminal still has
`PETCARE_EDC=0`. You do not need per-launch environment variables. An unreadable
saved key file prevents reception instead of falling back to plaintext. Run setup
again to replace the saved keys, then restart the GUI. The original cipher and GUI
handlers are unchanged.

### Environment-only setup (optional)

Set matching secrets independently on the Pi and laptop. `EDC_KEY` is the original
EDC key (A-Z and 0-9; spaces removed and letters uppercased).
`EDC_HMAC_KEY_HEX` is a separate 32-byte HMAC key encoded as 64 hexadecimal characters.
Do not use the disposable keys in the test fixtures for your devices.

In the laptop PowerShell session, replace the placeholders with your provisioned keys:

```powershell
$env:EDC_KEY = '<your matching EDC key>'
$env:EDC_HMAC_KEY_HEX = '<your matching 64 hexadecimal characters>'
$env:PETCARE_EDC = '1'
cd GUI
.\.venv-local\Scripts\python.exe main.py
```

In the existing GUI, select **Live Pi**, open **Connect Pi / Gesture access**, set
the port to **5006**, then start the receiver. Run the supplied standalone sender
on the Pi with its matching environment variables and `--gui-host LAPTOP_IPV4 --edc`.
Use the filename under which you saved that sender. Both clocks must be synchronized
within 120 seconds. Environment variables must be set before launching the GUI;
launching from this terminal ensures the Python process inherits them.

When no saved key file exists, port 5006 always requires EDC and valid keys. `PETCARE_EDC=1` requires EDC on any
selected port, including 5005. Missing keys prevent listening; invalid packets never
trigger plaintext fallback. Without that flag, other ports retain the existing insecure
plaintext lab mode for compatibility. An EDC codec, once loaded, stays active for
that receiver's lifetime. Restart the application to change provisioned keys.

## Packet compatibility

The newline-delimited outer JSON has exactly these fields:

```text
{"type":"EDC_PACKET","v":1,"ciphertext":"ECE1-...","tag":"64 lowercase hex characters"}
```

HMAC-SHA256 authenticates the exact UTF-8 ciphertext prefixed by
`b"EDC-HMAC-v1\x00"`. The receiver uses `hmac.compare_digest` before calling
the original EDC inverse transformations: restore four-token block order, reverse
coordinate feedback and key shifts, then recover characters from the keyed matrix.
The recovered text is unpadded Base32. Decoding it restores the UTF-8 JSON envelope:

```text
{"v":1,"sent_at":<Unix seconds>,"nonce":"32 uppercase hex characters","message":{...}}
```

The inner timestamp, nonce, and complete message are protected through the authenticated
ciphertext. They are validated after decryption because the Pi places them inside it.
The outer type and version are fixed protocol constants checked strictly; the Pi does
not include them separately in its HMAC input. No unauthenticated field selects a cipher.

The authenticated nonce serves as the packet's unique event identifier, including for
sensor readings and KNN results that have no application event ID. Exact ciphertext
replays are rejected before decryption; repeated inner nonces are also rejected.
Replay state survives TCP reconnects and receiver stop/start in the same process.
Entries are retained for 300 seconds using a monotonic clock, longer than the complete
240-second timestamp acceptance interval. A full cache rejects further packets rather
than evicting live identifiers. Replay state is in memory: restarting the application
resets it, and a recent captured packet may be accepted again within the timestamp
window. Persistent restart protection would require additional replay-state storage.

After verification, recovered messages pass through the existing application validator
and `received` signal to the same hub and movement handlers. Fields such as timestamps,
sensor measurements, prediction, confidence, and event IDs are retained; zone sensor
IDs are normalized to strings as required by the existing hub. Configuration messages
are encrypted on the TCP worker at send time, including after reconnect, with fresh
nonces and timestamps. Invalid packets close that client and leave the listener running;
safe status messages never include packet contents or secret keys.

## Tests

From `GUI`:

```powershell
.\.venv-local\Scripts\python.exe -m unittest tests.test_edc_transport -v
.\.venv-local\Scripts\python.exe -m unittest discover -s tests -v
```

The fixtures were produced by the supplied Pi implementation with disposable test
keys and fixed timestamp/nonces. Tests check exact ciphertext/tag compatibility in
both directions, verification before decryption, tampering, incorrect HMAC/EDC keys,
authenticated invalid ciphertext/metadata, expiry, replay, missing configuration,
TCP fragmentation, reconnects, encrypted configuration, and the existing GUI handlers.
Hardware integration still requires a live Pi using your independently provisioned keys.
