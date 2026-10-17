# Raspberry Pi pet hub setup

Copy `predict_movement.py` to the Raspberry Pi beside your existing trained `movement_knn_model.joblib`. The original upload is preserved as `predict_movement_original.py` for reference; the new file is standalone.

## Wiring: physical BOARD pins

| Sensor | Default purpose | TRIG | ECHO |
|---|---|---:|---:|
| 1 | Eating zone | 13 | 15 |
| 2 | Sleeping zone | 16 | 18 |
| 3 | Water zone | 22 | 24 |
| 4 | Toilet zone | 29 | 31 |
| 5 | Gesture KNN (fixed) | 7 | 11 |

These are physical header numbers, not BCM numbers. All sensors share ground. Use the appropriate 5V supply for HC-SR04 VCC and a voltage divider or level shifter on each 5V ECHO output: Raspberry Pi GPIO inputs must not receive 5V. Do not treat any TRIG/ECHO pin as a power pin. Confirm the wiring before starting the program. Sensor 5 preserves your original wiring. The assigned pins assume no conflicting HAT, SPI or other peripheral use.

The script continues using `RPi.GPIO`, as in your original program. Use a Pi/OS configuration supported by that library. Keep the existing numpy/joblib/scikit-learn environment used to train/load your model, particularly the matching scikit-learn version. The new hub code adds only Python standard-library modules.

## Run

1. On the laptop GUI, sign in, open **Sensor Hub**, select **Live Pi**, and click **Connect Pi / Gesture access → Start receiver**. Default TCP port is `5005`.
2. Find the laptop LAN IPv4 address with `ipconfig` in PowerShell. The Pi and laptop must be able to reach each other on that network.
3. On the Pi, run (replace the example address):

```bash
python3 predict_movement.py --gui-host 192.168.1.100
```

If your model has a different filename:

```bash
python3 predict_movement.py --gui-host 192.168.1.100 --model 'movement_knn_model (1).joblib'
```

For a different GUI listening port, add `--gui-port PORT`. Stop with Ctrl+C.

4. The Pi receives `SET_CONFIG`, saves it as `hub_configuration.json` beside the script, applies thresholds/mapping and sends `CONFIG_ACK`. The GUI then shows the confirmed revision. Keep that directory writable. Reconnects obtain the latest GUI mapping; previously saved settings do not substitute for confirmation from the current GUI session.
5. Change Sensors 1–4 assignments in the GUI and save. Sensor 5 remains gesture-only. Reposition sensors to face the selected stations. No Pi code edits are needed for later assignment changes.

## What runs where

Sensor 5 now sends live `GESTURE_READING` messages approximately five times per second. Update both this Pi script and the laptop GUI together. Sensor Hub and Movement & Access show its raw distance, receipt time, recording progress, classification state and rearm state. Missing echoes are reported explicitly; no telemetry for five seconds marks the input offline. These readings do not start OTP authentication; a separate `AUTH_GESTURE` classification is still required.

The Pi runs a single sequential acquisition loop so ultrasonic sensors are never triggered concurrently. Sensor 5 is scheduled at the model's sample interval; one rotating zone sensor is sampled midway between gesture samples. At the original 0.1-second interval, each zone is sampled approximately every 0.4 seconds. GPIO echoes time out after 30 ms. Recording and waiting for the gesture area to clear are state machines, so the four pet zones keep sampling. KNN inference uses a background worker; the model loads once.

The original 14 feature definitions, feature order from the model bundle, forward-fill policy and confidence calculation are retained. Late sampling windows are discarded rather than classified at the wrong trained timing. Multi-sensor scheduling requires a trained sample interval of at least 0.08 seconds. Actual timing, ultrasonic crosstalk, detection reliability and classifier accuracy must be validated on the wired Pi; software scheduling alone does not guarantee them.

The Pi sends zone distances/proximity and completed visit durations, with unique event IDs and configuration revisions. An invalid echo discards an incomplete visit rather than reporting a departure. Reassignment discards partial visits under the old mapping. Visits are time near a station, not proof of eating/sleeping/drinking/toileting. The GUI handles history, alerts, Telegram and OTP authentication.

TCP connection attempts and configuration reads run in a background thread. Messages are bounded and connection attempts retry automatically. Delivery is best effort: events are dropped while disconnected or older than three seconds; completed visits during outages are not replayed. Gesture capture waits for the current GUI connection/configuration to be ready, and old inference results are discarded after a connection or revision change. The lab TCP connection remains unencrypted.

## Test without hardware or model

With the GUI receiver running and Sensor Hub set to **Live Pi**:

```bash
python3 predict_movement.py --gui-host 192.168.1.100 --simulate
```

This explicitly simulated Pi client sends synthetic four-zone readings, records synthetic visits, and sends demo `PASS_BY` results. It needs neither GPIO nor model packages. To test the OTP page deliberately:

```bash
python3 predict_movement.py --gui-host 192.168.1.100 --simulate --demo-class AUTH_GESTURE
```

Simulation messages use the live protocol and therefore appear in the GUI's Live Pi history; use a temporary demo database if you do not want synthetic entries in your live history. There is no physical door actuator in this script.
