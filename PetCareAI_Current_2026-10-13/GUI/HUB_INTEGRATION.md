# Reconfigurable Smart Pet Hub

The primary **Sensor Hub** dashboard implements one Raspberry Pi with Sensors 1–4 assigned to pet zones and Sensor 5 reserved for gesture KNN. The previous pet demonstration remains under the clearly labeled Legacy pages; its five fixed sensing zones and wellness calculations do not represent the new hub hardware.

## Use the GUI

1. Open `Launch PetCare AI.vbs` and sign in with your existing account (`admin` / `pet123` for the demo).
2. Sensor Hub opens by default. Select Eating, Sleeping, Water or Toilet for each of Sensors 1–4. Default assignments are Eating, Sleeping, Water, Toilet in that order. Set the presence thresholds and click **Save assignments & send to Pi**.
3. **Demo** simulates four zone inputs continuously, including when another tab is selected. Completed visits and proximity durations are recorded separately from real Pi data. Software assignments cannot move hardware: point each sensor at the selected station.
4. For hardware, click **Connect Pi / Gesture access**, then **Start receiver**, normally on `0.0.0.0:5005`. Select **Live Pi** on Sensor Hub. The Pi connects to the laptop IPv4 address and receives the saved configuration automatically.
5. The GUI says confirmation pending until the Pi acknowledges the exact revision. Real readings and events are only accepted for that revision after acknowledgement. Disconnected or stale inputs show offline. No tab change pauses the hub.
6. Sensor 5 movement predictions continue to use the username/OTP access flow documented in `MOVEMENT_INTEGRATION.md`. They never become pet-zone visits.

## Pi integration contract

Use one persistent bidirectional TCP connection with newline-delimited UTF-8 JSON, maximum 4096 bytes per message. The GUI sends the current configuration on every new connection and whenever assignments change:

```json
{"type":"SET_CONFIG","revision":2,"mapping":{"1":"water","2":"eat","3":"toilet","4":"sleep"},"thresholds_cm":{"1":20,"2":20,"3":20,"4":20},"gesture_sensor":5}
```

The Pi must save the mapping, apply it to subsequent sequential sensor readings, and acknowledge only after applying it:

```json
{"type":"CONFIG_ACK","revision":2}
```

For Sensors 1–4, the Pi runs proximity and duration rules using the applied assignment. Send current observations periodically, including away/offline inputs:

```json
{"type":"SENSOR_READING","sensor_id":1,"revision":2,"distance_cm":12.4,"presence":true}
{"type":"SENSOR_READING","sensor_id":2,"revision":2,"distance_cm":null,"presence":false}
```

Send a completed visit when a sensor transitions from present to absent. Give each completed visit a globally unique event ID, retained if retrying the event. Duration is the Pi's measured proximity time, not an inference that eating/sleeping occurred:

```json
{"type":"ZONE_EVENT","event_id":"e466bce8-9e00-492d-a765-c7df97c2b522","sensor_id":1,"revision":2,"duration_seconds":8.3}
```

The laptop records receipt timestamps and deduplicates event IDs. Historical records retain the assignment used when received. Finish or discard active visits under the old revision before applying a new configuration; the GUI rejects events from old revisions after a mapping change. Today's counts are completed visits, and durations are summed proximity durations. Multiple sensors may be assigned to the same zone; totals then sum those sensors and may include overlapping observations.

Sensor 5 sends the existing movement status/results with `sensor_id:5`:

```json
{"type":"TRIGGERED","sensor_id":5}
{"type":"MOVEMENT_RESULT","sensor_id":5,"prediction":"AUTH_GESTURE","confidence":0.94}
```

Legacy gesture messages omitting `sensor_id` remain accepted. Explicit gesture messages from Sensors 1–4 are rejected. Pet-zone messages from Sensor 5 are rejected. Keep Sensor 5's six-second collection and KNN inference separate from sequential zone acquisition so zone sensors continue sampling during gestures.

The updated Pi sends Sensor 5 live telemetry separately:

```json
{"type":"GESTURE_READING","sensor_id":5,"distance_cm":12.4,"state":"RECORDING","samples":24,"total":60}
```

Supported states are `WAITING`, `RECORDING`, `CLASSIFYING`, and `WAIT_CLEAR`. Distance is a finite number above zero and at most 200 cm, or null for no valid echo. The GUI displays it in both Sensor Hub and Movement & Access, and marks it stale after five seconds. Older Pi scripts still deliver classification results but cannot populate this live card. Update the Pi script to see distances. Legacy pages are hidden behind **Show legacy demo pages** by default.

## Current boundary

The compatible Raspberry Pi backend is now available in `../raspberry_pi/predict_movement.py`, with wiring and commands in `../raspberry_pi/PI_SETUP.md`. It reads `SET_CONFIG`, samples the four pet sensors, emits zone events and keeps Sensor 5 for gesture inference. Copy the new script to the Pi once; later owner reassignment needs no Python edits. Real Pi hardware, interface circuits, KNN accuracy and latency still need validation. The TCP channel is unencrypted and intended for the lab network.

## Checks

## Sleep and toilet analysis

Lab 1 report Table 1 (page 9) specifies sleeping-area presence at 14 cm and litter-box presence at 13 cm. Following the requested below-threshold rule, defaults are strict `<14 cm` for Sleeping and `<13 cm` for Toilet. Existing saved assignments are migrated once to these thresholds and sent to the Pi with a new revision. Changing a sensor's mode in Sensor Hub selects that mode's default threshold; the owner can still edit it before saving.

Sensor Hub, Live Sensors, Overview, Activity Analysis and History show threshold-based estimated sleep/toilet status. Current sessions and today's hours accumulate from fresh readings, including ongoing visits. Separate daily duration records persist in SQLite, split at midnight and remain separate from demo data. Missing echoes, stale inputs, mode changes and disconnections interrupt sessions. Two sensors assigned to the same mode count overlapping time once. Completed Pi visit counts remain separate; they are not added again to accumulated activity time. These totals begin accumulating when the updated GUI runs and are not reconstructed from old visit records. Ultrasound measures zone presence, not verified sleep or elimination. In Live Pi mode, the old simulated wellness and KNN pet analytics are hidden to avoid conflicting with the new analysis; the gesture KNN remains available in Movement & Access.

## Automated checks

Food and water now measure container levels, not pet visits. In Sensor Hub select Food level / Water level, then enter sensor-to-surface distances for a full and empty container in **Container calibration**. Empty must be farther than full. Estimated filled-height percentage is `100 * (empty_distance - reading) / (empty_distance - full_distance)`, clamped to 0–100. No percentage is invented before calibration. Capacity in litres is optional; volume estimates require the explicit uniform-cross-section assumption. For irregular bottles or granular food, a linear height-to-volume estimate can be inaccurate. Food litres mean bulk occupied volume, not weight. Low level means below 30%.

All live sensor/analysis pages display the calibrated values and level history instead of food/water visits. Calibrated samples are saved at most once per five seconds, separately for Demo and Live Pi. Missing or stale echoes show no level. Existing Pi raw-distance messages suffice; food/water `ZONE_EVENT` messages are ignored for new visit records. Old food/water visit records remain stored but are excluded from the hub activity history. Calibration is tied to a physical sensor/container; moving or changing the container requires recalibration.

Run `.\.venv-local\Scripts\python.exe -m unittest tests.test_hub tests.test_movement -v`. The tests cover assignment persistence, fixed Sensor 5, revision acknowledgement, bidirectional TCP configuration, stale inputs, event deduplication, historical assignment retention and separate demo/live records.

