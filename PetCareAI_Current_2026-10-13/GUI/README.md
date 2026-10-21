# PetCare AI

For the standalone encrypted Raspberry Pi sender, see [EDC_INTEGRATION.md](EDC_INTEGRATION.md).
Run `main.py --setup-edc` once to save matching Pi keys; the existing receiver then
verifies and decrypts experimental EDC + HMAC packets on port 5005 without a bridge.

The default dashboard is now **Sensor Hub**, with four configurable pet-zone inputs on one Raspberry Pi and a fixed fifth sensor for gesture KNN. Start with [HUB_INTEGRATION.md](HUB_INTEGRATION.md). The original fixed-zone pages remain labeled Legacy.

Raspberry Pi movement predictions connect through **Sensor Hub ? Pi connection settings**. See [MOVEMENT_INTEGRATION.md](MOVEMENT_INTEGRATION.md) for the TCP message contract, Pi sender example, and connection diagnostics. Administrator login uses a password and Telegram OTP.

A working PySide6 desktop application for the ECE4810 **AIoT Smart Pet Health and Behaviour Monitoring System**. It monitors five camera-free sensing zones, interprets station presence, compares daily routines with a pet's baseline, and records behavioural insights and alerts.

The application includes login, a dashboard, live monitoring, Behaviour AI, historical charts, alert management, an editable pet profile, light/dark themes, Telegram settings, and reliable presentation scenarios. Sensor data now crosses an encrypted and HMAC-authenticated simulated transport before it reaches the UI. See [SECURITY.md](SECURITY.md) for backend details, packet demonstrations, sessions, and the EDC integration point. It starts without hardware, a trained model, internet, or Telegram credentials.

**Behaviour monitoring only. The behavioural wellness score is not a veterinary diagnosis.** Ultrasonic presence suggests activity at a station; it does not establish actual eating, drinking, or sleep. Food/water level warnings require an independent level measurement rather than a presence sensor.

## Install and launch

Python 3.10 or newer is required; verified here with Python 3.12.6 on Windows.

From PowerShell:

```powershell
cd C:\Y4S1\ECE4810\ECE4810IOTProject\PetCareAI_Current_2026-10-13\GUI
python -m venv .venv-local
.\.venv-local\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-local\Scripts\python.exe main.py --setup-admin
.\.venv-local\Scripts\python.exe main.py
```

The virtual environment and requirements are already installed in this workspace. To launch now, use:

On Windows, you can also double-click **Launch PetCare AI.vbs** in the project folder to open the app without a terminal. You can create a desktop shortcut to that launcher. Keep the launcher in the project folder so it finds the bundled virtual environment; no login or security checks are bypassed.

```powershell
.\.venv-local\Scripts\python.exe main.py
```

On macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --setup-admin
.venv/bin/python main.py
```

No virtual environment activation is necessary. All application paths resolve relative to the project, so launching from another directory also works when you pass the absolute path to `main.py`.

## Administrator setup and login

The only administrator username is **admin**. There is no default password,
public registration, second account, or demo login fallback.

Close the app and run this **once**, locally under your normal Windows account:

```powershell
.\.venv-local\Scripts\python.exe main.py --setup-admin
```

Choose and confirm a password of at least 8 characters. Enter your Telegram bot
token and **private chat ID** (saved desktop alert details are filled in when
available). Open the bot in Telegram and press **Start** first. Continue, click
**Request OTP on Telegram**, enter the delivered six-digit code and verify it.
Save the eight recovery codes in a private location and confirm possession before
completing setup. No QR code or authenticator enrollment is required.

Then launch normally:

```powershell
.\.venv-local\Scripts\python.exe main.py
```

The existing login card asks for your password first. **Sign in** verifies the
password and automatically requests the OTP using the saved Telegram details.
Enter the code, then **Verify and sign in**. Use **Resend OTP on Telegram** if
needed; you do not need to enter the bot token or chat ID again. Codes expire in two
minutes and resend requests have a 30-second cooldown. Failed delivery cannot
sign you in; there is no mock code displayed in the app. **Use a recovery code
instead** replaces the Telegram step, still requires your password, and consumes
that recovery code permanently. A recovery code alone cannot reset a forgotten
password or recover a missing OS vault.

If you forgot your password, click **Forgot password?** on the login card:

1. Enter an unused recovery code from the file saved during setup.
2. Click **Request password reset OTP on Telegram**. The app uses your already
   enrolled bot and private chat; you cannot change the destination here.
3. Enter the delivered reset code and choose and confirm a new password of at
   least 8 characters, then click **Reset password** within two minutes.
4. Sign in normally with the new password and the normal login OTP.

Resetting consumes that one recovery code and invalidates previous sessions and
pending password logins, including other open GUI processes. It keeps the same
`admin` account, Telegram enrollment and permanent setup lock. Requests have a
30-second cooldown; failed recovery attempts share the existing lockout counter.
You need **both** Telegram access and an unused recovery code. This flow cannot
reset a legacy authenticator-only account, missing credentials, or a lost OS vault.

The password is stored as an Argon2id hash. The dedicated login bot token and
verified private chat ID, password hash, recovery hashes and login throttling
state live in the native OS credential store, independent of mutable alert
settings. Windows uses Credential Manager, macOS uses Keychain, and Linux
requires Secret Service. Unavailable native storage blocks authentication;
there is no file fallback. Telegram OTP delivery needs internet access.

Administrators who already completed the earlier authenticator setup keep their
existing password, authenticator and recovery codes; their account is never
silently reset or re-enrolled. The Telegram flow applies to new, incomplete setup.

Setup closes permanently on completion. Preserve `data/admin_setup.lock` and the
original OS credential entry; missing, unreadable, or corrupted state blocks login
and does not reopen setup. Interrupted credential commits also lock setup. Restore
the original OS credentials and matching marker through trusted local recovery;
there is deliberately no automatic reset or enrollment bypass. A different
`--database` does not reset the administrator. Run the app under the same OS account.

**Raspberry Pi movement access** continues to show receiver, sensor and movement
information, but gestures and movement-panel codes cannot sign in to the administrator.
Pi monitoring, EDC/HMAC, SSH startup and the phone-hub transport are retained.
The standalone phone hub has its own existing authentication and is outside this
desktop-login replacement. See [SECURITY.md](SECURITY.md) for the security model.

## Structure

```text
main.py                         Root launch command
pet_monitoring/
  main.py                       QApplication startup, logging, command-line arguments
  controller.py                 State, timers, scenario commands, service coordination
  models/records.py             SensorReading, BehaviourEvent, Alert, PetProfile, DailySummary
  models/secure_packet.py       Encrypted packet, security events, results, metrics
  security/edc_adapter.py        Demo cipher / pluggable EDC backend
  security/provision_account.py Compatibility entry point for one-time admin enrollment
  logic/
    behaviour_interpreter.py    Validation, presence interpretation, location
    wellness_score.py           Configurable behavioural score
    anomaly_detection.py        Baseline comparisons independent of the classifier
  services/
    authentication_service.py   Replaceable local login adapter
    security_service.py         Encrypt-then-MAC, verification, replay checks, decryption
    sensor_service.py           Sensor protocol, timed mock, safe real adapter
    ml_service.py               ML protocol, demo predictions, unavailable adapter
    telegram_service.py         Mock and real Telegram transport
    credential_service.py       Windows DPAPI protection / session-only fallback
    database_service.py         SQLite creation, queries, demo seed, persistence
  ui/
    main_window.py              Login shell, navigation, global status, logout
    login_page.py
    dashboard_page.py
    live_monitoring_page.py
    behaviour_page.py
    history_page.py
    alerts_page.py
    pet_profile_page.py
    settings_page.py
    widgets.py                  Shared cards, tables, score ring, typography
    theme.py                    Light/dark palettes and stylesheet
    security_panel.py           Dashboard security card, settings log, demo inspector
tests/test_application.py       Behaviour, services, UI and end-to-end tests
tests/test_security.py          Integrated security and packet rejection tests
data/pet_monitor.db             Created automatically; ignored by Git
logs/app.log                    Rotating application log; ignored by Git
```

The UI sends commands to `MonitoringController` and reads its state; it does not generate readings, calculate scores, or make network calls. Qt signals notify the pages when state changes. The score ring is drawn directly with Qt. A transparent pixel-art corgi mascot is bundled in `pet_monitoring/resources/` and displayed on the login page and sidebar; its generation prompt is recorded in that folder's README.

## Mock Mode

Mock Mode is enabled by default. Five sensor samples update approximately every second using a `QTimer`. The simulator follows a repeating sequence: long sleeping-area occupancy, brief entrance movement, food and water visits, another resting period, and a litter visit. Distances drift smoothly within those periods.

Readings below configurable thresholds are interpreted as presence. Invalid, missing, non-finite, negative, or out-of-range distances are flagged rather than treated as detections. Visits are counted on the transition from absence to presence; estimated sleep/activity time accrues while the appropriate station is occupied.

Mock Mode seeds 30 days of clearly labelled illustrative totals. Today's demo totals start near a normal baseline so a university presentation does not require hours of observation. Mock ML produces `Normal Sleep`, `Pass-by`, and `Abnormal Movement`, with simulated confidence and latency. The KNN/SVM/Decision Tree performance table contains example metrics, **not measured evaluation results**.

Mock Mode can be changed in Live Monitoring or Settings â†’ System. With Mock Mode off, the default real sensor adapter reports offline and the default ML adapter reports unavailable, until you install your own adapters. Demo controls are disabled in that mode. Monitoring pauses on logout.

## Presentation sequence

1. Sign in with the demo account. The dashboard shows Milo and normal behavioural status.
2. Open Live Monitoring: five readings update and Sleeping Area shows pet presence.
3. Open Behaviour AI: `Normal Sleep`, approximately 92% confidence, and demo KNN appear.
4. Go to **Settings â†’ Demo Mode** and select **Simulate Abnormal Movement**.
5. Return to the dashboard: behaviour changes and the wellness score falls by 40 points.
6. Overview shows `Abnormal Movement`. Alerts records â€œUnusual movement detected in sleeping area.â€ Mock Telegram delivery appears in the alert and in `logs/app.log`.
7. Overview retains the activity totals; History lists observed phone-setting changes and completed Pi events. SQLite retains sensor samples, the classification, alert, and summary.
8. Choose **Reset Demo** to return to normal sensing and normal totals. Existing history/alerts are retained.

Additional working controls:

| Scenario | Effect |
|---|---|
| Normal Day / Reset Demo | Normal baselines, sleeping presence, network restored, five connected sensors |
| Abnormal Movement | Changing sleep-zone readings, abnormal classification, score penalty, movement alert |
| Low Food | Food visits become 50% of baseline; baseline alert and lower score |
| Low Water | Water visits become 37.5% of baseline; baseline alert and lower score |
| Restless Sleep | Sleep becomes 50% of baseline; abnormal movement classification and alerts |
| Sensor Failure | Sleeping sensor offline, incomplete observation warning, lower score, critical alert |
| Network Failure | Local monitoring continues, score falls, critical alert with failed Telegram delivery |

A scenario remains active until another scenario is selected. Scenarios are transient across restarts; saved daily totals and historical records persist. Normal Day and Reset do not clear historical alerts. Clear them from Alerts if desired. Repeated ticks suppress duplicate alerts while a condition remains active; explicit scenario activation represents a fresh demonstration event.

## Profile, score, and baseline comparisons

Edit Milo's identity and daily averages in Pet Profile and select Save profile. Sleep and active-time baselines must total no more than 24 hours; baselines must be positive. Updates immediately affect comparisons and scoring.

For each category:

```text
category_score = max(0, 100 - 100 * abs(observed - baseline) / baseline)
wellness = sum(weight * category_score) / sum(weight)
```

The five default weights are activity 20%, sleep 25%, food 20%, water 20%, litter 15%. Edit them under Settings â†’ Sensors & score. They are normalised automatically. An abnormal movement classification subtracts 40 points; an offline sensor or simulated network failure subtracts 15 points. Scores are clamped to 0â€“100.

| Score | Interpretation |
|---|---|
| 80â€“100 | NORMAL |
| 60â€“79 | ATTENTION |
| 0â€“59 | ABNORMAL |

These are configurable prototype rules, not a clinically validated metric. The score card includes the requested explanatory tooltip.

Baseline deviation is `(observed - baseline) / baseline`. By default a deviation exceeding 30% generates an alert. Food/water comparisons detect decreases; sleep/activity/litter comparisons detect changes in either direction. Settings exposes the deviation threshold. Real daily totals are evaluated at the day boundary, avoiding comparisons between a partial morning and a complete-day baseline. Demo scenarios and saved demo baseline edits evaluate immediately. Today's charts and score are still provisional in real mode.

## SQLite and logging

SQLite is created automatically and stores the profile, settings, sensor batches, behaviour events, alerts/read/delivery state, and daily summaries. Demo and live summaries use separate records for the same date, so toggling simulation preserves real daily totals. Overview shows activity summaries. History stores a persistent chronological log of observed phone-setting changes and completed Pi activity, with time and category filters. Intermediate phone edits while disconnected cannot be reconstructed. Demo seeding inserts only missing demo dates.

Sensor batches are stored every five seconds; classification changes are stored immediately and steady classifications every ten seconds. Summaries are saved every five seconds and on logout/close. Demo scenario changes save their data immediately. A clean restart retains the edited profile, settings, alerts, classifications, and summaries. Alerts support severity filters, marking read, clearing one, and clearing all.

The log records startup/shutdown, login outcomes, connection changes, classifications, alerts, Telegram results, storage errors, and model errors. It rotates at 2 MB with three backups. Passwords and Telegram tokens are not logged; Telegram exception messages are deliberately omitted because they can contain the token URL.

When the database cannot open, the application uses temporary in-memory storage and displays a warning; those changes cannot persist. A corrupt file is not deleted or overwritten. Storage failures are logged and surfaced in the status bar. A short SQLite lock timeout prevents extended waits on a locked file.

Use a separate database for another demonstration:

```powershell
.\.venv-local\Scripts\python.exe main.py --database data\presentation.db
```

## Connect ultrasonic hardware later

Implement the `SensorService` protocol in `services/sensor_service.py`, then inject your implementation at the point `MonitoringController` chooses `RealSensorService`. Keep the same `read()` contract:

```python
def read(self) -> list[SensorReading]:
    # Return the most recently acquired readings; do not block this method.
    return cached_readings
```

Each sample contains `sensor_id` (`sleep`, `food`, `water`, `litter`, `entrance`), area name, distance in cm or `None`, presence, ISO timestamp, and connection status. Optional `level_low` is for an independent bowl-level measurement. The controller validates distances and applies the configured thresholds, so hardware adapters need not duplicate interpretation logic.

Read GPIO/serial/MQTT on a worker thread or use an asynchronous transport and publish cached latest readings. Do not run GPIO echo waits or socket reads inside widgets or the GUI timer. Add a stale-sample timeout to your hardware adapter and report `Offline` when fresh samples stop arriving. The shipped real adapter intentionally reports offline instead of fabricating hardware data.

## Connect a trained ML model later

Implement `MLService.predict(features)` and inject it at the controller's real-ML selection point. `features` includes readings, daily totals, and the current demo scenario. Return a `BehaviourEvent` with classification, confidence in [0,1], ISO timestamp, model name, and measured latency in milliseconds. Additional labels do not require changing the history table or storage.

Load your trusted local model once during service initialisation with joblib or your chosen framework, apply exactly the feature order/window/scaling used during training, and measure inference time with `time.perf_counter()`. Add joblib/scikit-learn to requirements only when that integration uses them. Never load an untrusted pickle/model file.

For expensive inference, run the model on a worker and have `predict()` return the latest completed result; use Qt signals to publish worker results. This keeps the existing page interfaces intact. The current demo inference is inexpensive and runs within the timer. Replace the `performance` list with measured accuracy, precision, recall, F1, and latency from your evaluation. The separate rule-based anomaly module can also be replaced without changing the classifier contract.

## Telegram

Settings â†’ Connections supports Mock and Real modes. Credentials are optional at startup.

1. Obtain a Telegram bot token and a chat ID, and start a conversation with your bot.
2. Select Real, enter the token and chat ID, enable notifications, then Save settings.
3. Select Test saved Telegram connection. This sends a short test message using the saved settings.
4. Choose which categories should send Telegram messages in Settings â†’ Notifications.

Mock mode logs `[MOCK TELEGRAM]` instead of making an HTTP request. Alert delivery is labelled `Sent Â· Mock` to distinguish it from actual Telegram delivery. Turning a category off suppresses Telegram for that category; the local alert still exists.

Tokens are masked while entering and the field is cleared after saving. On Windows, the token is encrypted using the current user's DPAPI and the encrypted blob is stored in SQLite. When secure storage is unavailable (including some sandboxes), the token remains **session-only**, with a visible status message; it is never written as plaintext. On macOS/Linux secure persistence needs a platform keychain adapter. To remove a token, tick Remove saved bot token and save.

All sends and tests run in a background executor with finite HTTP timeouts. Missing credentials, bad credentials, offline networks, and invalid responses update delivery status without crashing the GUI. No real Telegram message is sent until Real mode is configured and a test or enabled alert is triggered.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q main.py pet_monitoring tests
.\.venv-local\Scripts\python.exe main.py --smoke-test
```

Tests run Qt offscreen and use temporary databases. They exercise login, sidebar navigation, logout, live timers, timed sensor patterns, all scenario buttons, confidence/history, all graph ranges, baseline/profile edits, alert filters/read/clear, restart persistence, theme/resizing, invalid sensors, unavailable ML, database failure, token handling, notification toggles, and responsiveness during a slow network operation. Real Telegram HTTP responses/failures are mocked; hardware and real trained-model accuracy require later integration testing.

The UI is designed for 1440 Ã— 900 and 1280 Ã— 720. Smaller heights use vertical page scrolling; cards and text adapt to the available width. `--smoke-test` opens the real GUI and closes it after two seconds without entering credentials.

API references used during implementation: [Qt thread signals](https://doc.qt.io/qtforpython-6.8/examples/example_widgets_thread_signals.html), [PyQtGraph PlotWidget](https://github.com/pyqtgraph/pyqtgraph/blob/master/pyqtgraph/widgets/PlotWidget.py), and [Microsoft CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).

### Phone-app link and change history

The sidebar **Open phone app** button opens the Pi web app through a localhost SSH tunnel after the hub reports ready. Its tooltip also shows the LAN address to open on a phone connected to the same network. The Pi and existing SSH connection must be available. Deployment includes the complete web asset folder. Phone pet/profile and sensor-setting snapshots are validated and recorded in **History**; credentials and Telegram tokens are excluded. Activity summaries are consolidated in **Overview**.

### Two-way sensor settings

Sensor assignments, thresholds and full/empty calibration can be saved in the phone app or the desktop GUI. The latest save accepted by the Pi hub updates both interfaces. Desktop saves are queued persistently until the GUI-managed SSH hub confirms them. Offline saves are applied on reconnection, so ordering is by acceptance at the Pi, not unsynchronised device clocks. Replayed snapshots and duplicate save retries cannot roll back a newer confirmed version. The GUI reports pending/confirmed synchronisation separately from sensor configuration acknowledgement.

Capacity and the linear-volume checkbox remain desktop-only options. Pet profile and location edits retain their existing phone-to-desktop behavior. This sensor settings control uses the existing localhost SSH tunnel and does not alter EDC/HMAC sensor transport, administrator login or phone authentication. A separately running legacy hub reports that two-way desktop settings are unavailable; use the GUI-managed hub for this feature. Restart the GUI to deploy the updated hub and refresh the phone page.

### Gesture and Telegram OTP dashboard sign-in

Sign in normally, then open **Door Authentication** in the dashboard sidebar for Sensor 5 gesture and OTP controls. A fresh AUTH_GESTURE opens a 120-second sign-in window. Enter `admin`, press **Send gesture login OTP to Telegram**, enter the delivered code and press **Verify OTP and open dashboard**. Successful door verification keeps the dedicated dashboard section open. Password followed by login OTP remains available as the other sign-in path.

A gesture alone cannot sign in. The gesture login code is purpose-bound, registered in the existing native administrator credential record, delivered through the existing enrolled Telegram account, single-use, and limited to the gesture window. Stale/replaced challenges, changed credentials, expired or failed-delivery codes, wrong usernames, lockout and disconnect cannot authenticate. Verification has at most five attempts per gesture; failed registered-code checks also update persistent administrator failure/lockout counters. Ordinary password-login codes and gesture-login codes are separate. No demo code is exposed and no new account is created. Recovery codes are not consumed by this flow.

Telegram delivery runs off the Qt thread. Successful verification records access and sends AUTH_OK / OTP_OK feedback commands to the connected Pi through the existing EDC/HMAC transport. Commands expire quickly and cannot be replayed on a later connection. Hardware feedback requires the compatible bundled Pi sender; local software tests do not verify physical LEDs/buzzer or live Telegram delivery.

### Dashboard door authentication and alert delivery

The sidebar **Door Authentication** entry uses a door outline icon and the same shared Sensor 5 gesture/Telegram OTP controller as the login panel. Once signed in, a recognised authentication gesture takes you to this dedicated section. Verifying another door OTP keeps you on this page and updates the Pi feedback; it does not open a second receiver or create a separate account. There is no gesture entry or automatic gesture popup on the login page.

For real Telegram alerts, when both alert token and chat ID are blank, enabled alerts can use the existing verified administrator Telegram destination. Settings -> Connections exposes this fallback explicitly. A separately configured alert destination takes priority; partial configurations are not combined with administrator credentials. Mock mode, globally disabled alerts, and disabled alert categories remain respected. This does not change the Telegram destination used for login. Existing failed alerts retain their historical status; the fix applies to new alerts and the saved-connection test.
