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

The existing login page delegates credentials to `AuthenticationService` and receives a boolean outcome. Successful authentication creates an eight-hour in-memory session. The controller refuses acquisition and packet reception without a valid session. Expiry stops monitoring and returns the GUI to login; logout invalidates the session.

The initial demo account remains **admin / pet123**. The password is represented by a PBKDF2-HMAC-SHA256 hash with 600,000 iterations, never stored as a plaintext account value. New accounts use random 16-byte salts. Three consecutive failures create a local warning and security event; five failures temporarily block login attempts for 30 seconds. Those counters and sessions are local to this process and reset at restart; they are not a replacement for deployment authentication policy.

To change the account (including to `group5`), close the application and run:

```powershell
.\.venv\Scripts\python.exe -m pet_monitoring.security.provision_account --username group5
```

The command prompts twice for a hidden password and stores only its salt, hash, and iteration count. It replaces the single local account; the old demo credential will then stop working. Passwords are not passed in command-line arguments. This is a trusted local provisioning tool; protecting filesystem access and authorising account administration remain deployment responsibilities.

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
