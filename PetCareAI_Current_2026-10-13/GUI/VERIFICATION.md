# Verification record

Verified on Windows with Python 3.12.6, PySide6 6.11.2 and PyQtGraph 0.14.0 on 4 October 2026.

## Checks

- The desktop app launches through `main.py`; smoke launches close cleanly.
- All modules compile and dependency resolution passes `pip check`.
- Login accepts the demo account, rejects incorrect credentials, masks the password, remembers only the username, and clears the password after login.
- All seven sidebar pages and logout work through Qt widget interactions.
- The timer updates all five sensors; scheduled sleep, entrance, and food periods produce meaningful observations.
- Every Demo Mode button changes the shared system state. Abnormal movement changes classification, lowers wellness, generates an alert, sends mock Telegram, and records sensor/classification/summary data.
- Alert severity filters, read status, individual clearing, and clearing all work.
- Today, 7 Days, and 30 Days charts contain the expected points for all five metrics.
- Profile/baseline edits and settings persist when a second controller reopens the database.
- Switching Mock Mode preserves live daily totals separately from demo totals. The initial date-only database schema migrates without losing live summaries.
- Both 1440 × 900 and 1280 × 720 accommodate all page widths. Longer pages scroll vertically. Light and dark themes apply.
- Invalid sensor values, offline sensors, unavailable ML, corrupt storage, and failed Telegram requests do not crash the app.
- A slow Telegram task runs while a GUI heartbeat timer continues, verifying that network waits do not block the UI.
- Notification category toggles and sensor thresholds affect their services.
- Blank profile names, impossible daily baselines, and all-zero wellness weights are rejected.
- Windows DPAPI encrypt/decrypt round-trip succeeds outside the sandbox. Inside a restricted environment, secure-storage failure retains the synthetic token only in memory and writes no plaintext token.

Original monitoring coverage: **19 unittest cases**, using Qt offscreen and temporary databases. Expected failure-path messages in the test output are intentional (such as an injected unavailable ML model and corrupt SQLite file).

## Integrated security verification · 5 October 2026

Added 18 security/account cases, for **37 cases in total**. The combined 35-case suite passed before the final two adapter/provisioning checks were added; those two checks also passed separately. The security scenarios execute actual AES-CTR encryption (explicitly a demo backend, EDC not connected) and HMAC-SHA256 verification.

- Encrypted wire bytes contain neither plaintext pet fields nor a trusted verification flag.
- Correct HMAC verifies, valid plaintext is recovered, and the food-station GUI updates.
- One changed ciphertext byte fails HMAC and calls no decrypt function.
- Metadata tampering, malformed packets, missing tags, invalid ciphertext, wrong receiver keys, replay, and expired packets are rejected before decrypt.
- Decryption exceptions and invalid recovered schemas yield no application update.
- Security alert, rejected counter, user-facing log, SQLite metadata, and mock Telegram delivery all record the attack.
- Automatic sample batches preserve all previous application readings when a packet fails.
- Login gates sensor acquisition; expired sessions return to login and stop monitoring.
- New accounts persist salted hashes; authentication lockout and repeated-failure alerts work.
- Secrets are absent from stored security records and config (except permitted password hashes and separately encrypted Telegram credentials).
- A Qt heartbeat runs during periodic secured packet processing.
- Existing monitoring, demo scenarios, persistence, chart ranges, and both target window sizes remain functional.
- The EDC adapter contract and separately provisioned HMAC keys work between separate service instances (using a test cipher only).
- The local account setup command uses hidden password prompts and writes only hashes.

The final build also passed compilation, dependency checks, and real GUI smoke launches. Security events persist across restart; session keys and session authentication deliberately do not. See [SECURITY.md](SECURITY.md) for the demonstration sequence, exact changed-file list, and integration limits.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q main.py pet_monitoring tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe main.py --smoke-test
```

## Integration limits

No physical ultrasonic hardware or trained ML model was connected. Those adapters deliberately report offline/unavailable in real mode. Mock confidence, latency, model comparison metrics, and seeded history are explicitly illustrative. Real Telegram success/failure responses are tested with mocked HTTP calls; no live bot credentials were supplied or messages sent. Authentication is a replaceable demo adapter.

The behavioural score and station interpretations provide observational information, not veterinary diagnoses.
