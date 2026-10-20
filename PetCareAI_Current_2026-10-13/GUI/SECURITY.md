# Integrated sensor security

For live Raspberry Pi EDC + HMAC reception, use [EDC_INTEGRATION.md](EDC_INTEGRATION.md).
The remaining sections describe the separate simulated sensor security flow.

The current GUI, themes, corgi mascot, monitoring services, SQLite history, alerts, and Telegram integration are reused. Security runs in the actual sensor path; it is not a separate encryption demonstration.

## Backend and limits

The simulation backend is explicitly labelled **Demo AES-CTR + HMAC-SHA256 (EDC not connected)**. AES-CTR is supplied by the `cryptography` library, not a newly invented cipher. This demonstrates real encryption, authentication, verification, decryption, rejection, and measured processing latency. Live Pi transport separately uses the supplied original EDC algorithm in `security/edc_transport.py`; it is an experimental educational cipher, not production-grade security.

For this simulation, the sender, simulated network, and receiver are inside the same application. Cipher and HMAC keys are distinct, randomly generated, and kept only in memory for the run. The simulation does not provision device keys or attest hardware identity. Restarting creates new demo keys. Historical security events remain readable because SQLite stores event metadata, not old packets or cryptographic secrets.

The encrypt-then-MAC structure follows the [cryptography library's guidance](https://cryptography.io/en/latest/hazmat/primitives/symmetric-encryption/). HMAC comparisons use Python's [constant-time comparison API](https://docs.python.org/3/library/hmac.html#hmac.compare_digest). HMAC authenticates message contents under a shared secret; it is not encryption or proof of an individual device's identity.

## Automatic data flow

```text
Authenticated session
  → sensor adapter provides five samples
  → sender validates distances / constructs a versioned payload
  → JSON serialisation
  → cipher backend encrypts bytes with a fresh nonce
  → HMAC-SHA256 authenticates ciphertext + all transmitted metadata
  → SecurePacket.to_wire() produces network bytes
  → receiver validates packet structure
  → receiver verifies HMAC
  → receiver rejects replayed / expired packets
  → receiver decrypts ONLY if the preceding checks pass
  → recovered sensor payload is validated
  → behaviour interpretation, GUI, ML and normal SQLite history update
```

Wire packets include packet ID, sensor/source ID, UTC timestamp, algorithm, version, nonce, ciphertext, and HMAC. They contain no plaintext pet payload or local `verified` flag. Nonce, source, packet ID, timestamp, version, algorithm, and ciphertext are all bound by the HMAC. Received `verified` flags are rejected as unknown fields.

Each tick processes a five-sensor batch. If any packet fails, **the entire batch is withheld from application state**, retaining the last accepted readings, location, classification, daily totals, and behaviour history. Receiver metrics may still record earlier valid packets from the withheld batch; “accepted packets” is a transport statistic, not a count of GUI updates. Sensor outages are valid observations from a trusted sender and remain encrypted in transit.

Malformed packets, missing/invalid tags, invalid ciphertext, HMAC failures, replayed packets, old timestamps, bad recovered schemas, source mismatches, and decryption failures return a rejected result. HMAC failures, malformed packets, and replay failures never call the cipher's decrypt function. The service does not log exception text from a custom cipher, because that text may contain secrets.

Replay protection uses authenticated packet IDs and a five-minute timestamp window, with 30 seconds of future clock tolerance. Old IDs expire from memory; their old timestamps still prevent acceptance. Real devices will need clock synchronisation and persistent/device-specific replay policy. Sender-side hardware freshness is still the sensor adapter's responsibility.

## Login and sessions

There is exactly one administrator, `admin`. Legacy `authentication_users` records
are never read for login and are removed from the monitoring database when setup
completes. No demo password, password-only login, account creation or username
provisioning remains. The old provisioning module routes to the same local setup
wizard; it cannot replace credentials after enrollment.

Close the app and run `.\.venv-local\Scripts\python.exe main.py --setup-admin`
locally. This mode opens only enrollment; Pi, receiver, hub and SSH services do
not start. Choose and confirm an 8-1024 character password, enter your Telegram
bot token and private positive numeric chat ID, request a code, verify it, save
the eight recovery codes and confirm possession. Existing saved desktop alert
credentials may prefill the form, but cannot be changed on the login page.
Open the bot in Telegram and press Start before requesting the code. Enrollment
expires after 15 minutes. Cancel discards its in-memory credentials.

Passwords use Argon2id v19 with 64 MiB memory, three iterations, four lanes and a
random 16-byte salt (32-byte hash). The authoritative administrator record lives
in native OS storage: Windows Credential Manager (current user, local-machine
persistence), macOS Keychain, or Linux Secret Service. Backends are selected
explicitly, with no plaintext or file-keyring fallback. The bot token, fixed
private chat ID, recovery hashes and counters use the same protected record.
Windows replaces a single credential target, avoiding stale fallback records.

Each Telegram OTP contains six cryptographically random digits and expires after
at most 120 seconds. The app holds only an HMAC digest and delivery state in memory;
it neither saves nor displays the code. It uses real HTTPS Telegram delivery even
when monitoring alerts are in Mock mode. API failure, timeout or unsuccessful
delivery cannot enable verification. Requests run outside the UI thread and
callbacks from cancelled or replaced requests cannot restore a challenge.

Login requires a verified password before requesting or verifying the second
factor. Persistent 30-second resend throttling and shared password/OTP failure
counters survive restarts. Three failures raise a warning; five lock sign-in for
30 seconds. Resending and restarting the password step do not reset attempts.
A persisted challenge identifier invalidates codes from older requests, including
requests in another app process. Challenge consumption is committed before the
eight-hour in-memory session is issued. Logout and expiry invalidate challenges.
Only a successfully delivered current code can sign in.

Eight random 128-bit recovery codes are shown once and exported only on request.
The app stores SHA-256 hashes, never recoverable codes. Each recovery code replaces
one Telegram OTP after the password, is consumed before a session is issued, and
works when Telegram is unavailable. A recovery code alone cannot reset the password
or repair a lost vault record. The installation mutex serializes OTP and recovery consumption
across app processes; failed persistence never grants a session.

Existing version-1 records from completed authenticator enrollments remain usable
with their original TOTP and recovery codes; no record is silently replaced and
setup does not reopen. New enrollment from the local wizard creates a version-2
Telegram record at the same administrator credential target. It creates no second
account. Legacy TOTP verification preserves the previous interval/replay checks.

A permanent `data/admin_setup.lock` marker binds the installation to its vault
record. It is independent of the monitoring database, so `--database` cannot create
a second account. Completion reserves the marker before committing credentials;
a crash or failed commit leaves setup locked. A missing or corrupted credential,
missing/mismatched marker, or unavailable OS store blocks login and setup rather
than offering enrollment again. Restore the original credential-store entry and
matching marker through trusted OS/local backup recovery. There is intentionally
no reset for missing credentials or recovery bypass. Keep recovery-code exports private.

This protects the application login under the current OS account. It does not
protect against an attacker who controls that OS account, can edit application
code, or deliberately erases both the vault record and installation marker. Files,
OS account access and backups must retain their normal OS protections. A new
installation or OS account is a separate trust boundary, not account registration.

Movement events and movement-panel codes cannot create administrator sessions. The
movement panel remains available for Pi connection diagnostics and sensor history;
all administrator entry still requires the normal password and the enrolled
second factor (Telegram OTP or recovery code for new enrollment). Receiver, encrypted transport, SSH launch, and phone telemetry
continue through their existing paths. This change covers the desktop GUI's admin;
the standalone Pi phone hub retains its separate existing account mechanism.

Implementation references: [Argon2-cffi](https://argon2-cffi.readthedocs.io/en/stable/api.html),
[Telegram Bot API](https://core.telegram.org/bots/api#sendmessage),
[PyOTP for existing authenticator records](https://pyauth.github.io/pyotp/), and
[native credential backends](https://keyring.readthedocs.io/en/stable/).

## Forgotten password

The login card's **Forgot password?** dialog implements recovery for existing
Telegram-enrolled administrators. It does not require the old password, create
an account, modify the setup marker, or reopen enrollment. It requires two proofs:
an unused saved recovery code AND a successfully delivered Telegram reset OTP.
Neither proof alone can reset a password. Legacy authenticator-only records are
not silently migrated or offered a weaker recovery method.

An unused recovery-code digest must match before a reset message is sent. The bot
and destination come only from the OS-protected administrator record, never from
reset-form inputs or mutable monitoring settings. The reset OTP is generated
independently from login codes, explicitly labelled PASSWORD RESET, and valid for
at most two minutes. Request throttling and failed-attempt counters are shared
with login and persist across restarts. Failed delivery, expired codes, cancellation
(including before the worker starts), replaced requests, missing/corrupt credentials
and failed persistence do not permit reset. Network requests do not block Qt or
access the SQLite connection from the worker.

The reset challenge binds the enrollment ID, current password-hash version,
recovery-code digest and OTP request ID. Completion rechecks every binding under
the installation mutex, then atomically replaces the Argon2id password hash,
consumes the recovery code, invalidates the OTP and clears failed-attempt state in
the existing native credential record. The username, enrollment, remaining recovery
codes and Telegram configuration are preserved. Setup remains disabled. Success
returns to normal sign-in and never creates a session.

Sessions and password-stage challenges bind the password-hash version. Every
session authorization check reads the current native credential version, so a reset
also expires sessions in other GUI processes. Previously verified old passwords
cannot complete their pending second-factor step. Passwords, OTP digits, recovery
codes and bot tokens are not included in authentication events or error logs.

## Dashboard and settings


- The dashboard has a compact Security status card for authentication, active encryption/backend, integrity, accepted/received packets, rejected packets, and last verification.
- The global status bar shows Secure, Authentication Required, Verification Failed, or Security Module Offline.
- **Settings → Security** shows the backend, HMAC protection, session/run counters, HMAC success rate, mean encryption/tag, verification, and decryption timings, and the security log.
- **View Security Log** focuses the event table. Detailed ciphertext is restricted to the optional demo inspector.
- **Settings → Notifications → Security / tampered sensor packets** controls Telegram delivery of security alerts. Local recording remains enabled.

Packet counters and averages cover the current run. HMAC-verified counts include authenticated packets subsequently rejected by replay, decryption, or recovered-data validation; they are distinct from accepted packets. Mean decryption time includes attempted decrypt operations. The total time in the demo inspector includes encryption/tag generation and receiver processing. These are performance/integrity statistics, not proofs of cipher strength.

Warnings remain visible after the failed packet even when later legitimate ticks succeed. Select **Acknowledge security warning** after a valid packet, or demonstrate another successful secure packet, to clear the indicator. Alerts and historical events remain recorded. Reset Demo restores normal sensing and clears the transient warning without deleting history.

## Normal demonstration

1. Start `main.py`, sign in, and ensure Mock Mode is enabled.
2. Go to **Settings → Demo Mode → Simulate Secure Packet**.
3. The mock sensor visits the food station and reports **14.3 cm**. All five sensor messages traverse the secured path.
4. Dashboard shows Food Area / Eating. Live Monitoring shows the food observation. The security card shows HMAC Verified.
5. Expand **Show payload details** in the Security Inspector. It shows sender payload, shortened encrypted bytes, shortened HMAC, PASSED, recovered payload, and measured times.
6. Settings → Security records Sensor packet verified and Sensor packet decrypted. Normal sensor/behaviour/daily-summary storage continues.

The food scenario remains active for presentation until another scenario is selected. It does not assert that a physical food sensor or trained model is connected.

## Tampering demonstration

1. With the same authenticated session, select **Simulate Tampered Packet**.
2. The controller constructs a legitimate food reading, encrypts it, and computes a correct HMAC.
3. It flips **one ciphertext byte after the HMAC is generated**, keeping the original tag.
4. The receiver performs the actual HMAC comparison and rejects it. It does not call decrypt.
5. Readings, location, classification, and daily totals stay unchanged. The rejected count increases by one.
6. The global/card status warns of verification failure. Alerts records **IoT Packet Integrity Failure**, and the security log records **Tampered packet rejected**.
7. With security notifications enabled, Mock Telegram logs a safe alert, and delivery status is stored. Real mode uses the existing configured Telegram service.
8. The inspector shows FAILED, zero decryption latency, and **Not decrypted · packet rejected**.

Automatic legitimate timer ticks may subsequently update normal sensor timestamps; they never apply the corrupted packet. Tests pause the timer to verify exact before/after state preservation for the attack.

## Connect EDC

Connect your original functions in **`pet_monitoring/security/edc_adapter.py`**. `EDCAdapter` expects:

```python
encrypt(plaintext_bytes, authenticated_nonce_bytes) -> ciphertext_bytes
decrypt(ciphertext_bytes, authenticated_nonce_bytes) -> plaintext_bytes
```

Keep the original cipher implementation and its keys in your backend/key manager. Convert text-form ciphertext to bytes at the adapter boundary if needed. If your cipher requires its own IV, include it in the ciphertext or use the already-authenticated nonce. Do not discard IV/state required to recover a message correctly.

Inject it where the controller currently constructs `SecurityService`:

```python
from pet_monitoring.security.edc_adapter import EDCAdapter

backend = EDCAdapter(your_edc_encrypt_wrapper, your_edc_decrypt_wrapper)
self.security = SecurityService(
    backend=backend,
    event_sink=self.record_security_event,
    authentication_key=your_separately_provisioned_hmac_key,
)
```

The HMAC key must contain at least 32 bytes and be distinct from the encryption key. Separate device and receiver service instances need securely provisioned matching secrets; the demo's automatically generated keys only work within its shared in-process service. Do not put keys into the packet, settings, SQLite, source control, GUI, logs, or Telegram. The GUI, schema checks, verify-before-decrypt ordering, alerts, and counters remain unchanged.

If your cipher has costly processing, replace the synchronous demo transport with a bounded worker/queue that emits accepted results through Qt signals. The supplied demo cipher handles tiny packets quickly; the tests verify a GUI heartbeat continues while secured ticks run. Network and Telegram requests already use the existing background executor.

## Storage and files

SQLite adds a `security_events` table. It stores event type, packet ID, source ID, timestamp, verification outcome, and timing values. Application logs contain the same useful technical metadata. The latest 200 events appear in the GUI, with complete stored history retained. Existing profile, alert, and monitoring tables are preserved. Plaintext accepted sensor readings still belong to normal local monitoring history; this change protects the simulated transport, not all database contents at rest.

New files:

- `models/secure_packet.py`: wire model, security event, result, metrics.
- `services/security_service.py`: encryption, HMAC, validation, replay checks, receiver processing.
- `security/edc_adapter.py` and `security/__init__.py`: demo/backend contract and EDC wrapper.
- `security/provision_account.py`: hidden-password local account setup.
- `ui/security_panel.py`: compact card, settings panel/log, optional inspector.
- `tests/test_security.py`: service and end-to-end security checks.
- `SECURITY.md`: this guide.

Modified files: `controller.py`, `services/authentication_service.py`, `services/database_service.py`, `services/sensor_service.py`, `models/__init__.py`, `ui/main_window.py`, `ui/dashboard_page.py`, `ui/settings_page.py`, `requirements.txt`, `README.md`, `VERIFICATION.md`, and `tests/test_application.py`. The existing monitoring tests now explicitly create an authenticated session before exercising monitoring.

## Run and verify

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q main.py pet_monitoring tests
.\.venv\Scripts\python.exe main.py --smoke-test
```

Acceptance tests verify actual encrypted wire bytes, correct HMAC, normal decryption/application, ciphertext/metadata tampering, no decrypt on failed authentication, retained GUI state, counters, security alerts, persisted logs, mock Telegram, session expiry, bad login/lockout, invalid packets, unavailable service, and GUI responsiveness. Hardware transport, EDC correctness/strength, key provisioning, and live Telegram delivery await those integrations.
