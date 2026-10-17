# ECE4810 Smart Pet Monitoring System — Digital Twin V3

A refinement of the supplied V2 project, retaining Flask + Flask-SocketIO, Three.js,
OrbitControls, the five-distance HTTP publisher, and Tkinter launch helper.
**Five physical HC-SR04 inputs → three configurable logical zones → exactly one pet.**
The pet fades out for 250 ms, relocates while hidden, then fades in for 250 ms.
A clear/unknown state hides it; it is never cloned.

## 1. Start the viewer

Requires Python 3.10+ and a browser with WebGL (Chrome, Edge, Firefox).
Extract the ZIP first. In Windows PowerShell:

```powershell
cd smart_pet_twin_v3
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

Linux / Raspberry Pi:

```bash
cd smart_pet_twin_v3
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

Open **http://127.0.0.1:5000**. Keep the server terminal open.
Do not double-click the HTML file: it needs the local server.
Python installation needs package access once; all browser JavaScript is bundled,
so the viewer does not need internet/CDN access. No npm installation is needed to run it.

- Left drag: rotate; wheel: zoom; right drag: pan.
- Perspective / Plan view: restore camera positions.
- Sensor 1–5 buttons: stable simulated detection, held until another simulation
  command or a live distance POST. Clear detections hides the pet immediately
  after its fade. Simulation goes through the configured backend mapping.
- Labels and zone rings can be toggled independently.
- After a simulation command, a live distance POST automatically resumes live mode.
  Stop the Pi publisher while experimenting with simulation buttons.

## 2. Geometry and reference alignment

The annotated plan is the orientation authority: S1 upper-left, S2 upper-right,
S3 lower-right upper position, S4 lower-right lower position, S5 lower-left.
Plan view displays these positions without mirroring. The black open tray,
left lane with a passage, cross-partition with two openings, and lower divider
follow that sketch. S2 has one open circular cardboard bed; S5 has a corner
curved enclosure. Two shallow curved panels in the S3/S4 region follow the
orange sketch annotations. **These panels are not clearly visible in the photos**;
confirm whether they represent planned panels or the current build before treating
them as an exact as-built model.

The photos informed corrugated cardboard appearance, the low S1 mount,
four elevated sensors on twin sticks/cardboard backboards, blue sensor PCBs,
paired silver transducers and a simplified water bottle. There is no second
pet toy baked into the environment; only the one live avatar exists.
Wiring and bench clutter are intentionally simplified.

No measured dimensions or mounting angles were supplied. The tray is 10×15
**relative scene units**, not metres or centimetres. Heights, tilt, radii and
positions are visual estimates. Direction arrows indicate estimated orientation,
not verified ultrasonic beam coverage. This is a zone display, not triangulation
or continuous tracking, and cannot identify an animal from ultrasonic data.

## 3. Configure the five-to-three mapping

Edit `config.json`, then restart the server and refresh the browser. It contains
one shared source for groups, zone names/representative positions, sensor mount
positions/heights/yaw/tilt, baselines, thresholds and timing.

| Physical input | Default logical zone | Plan location |
|---|---|---|
| S1 | A | Upper-left lane, low mount |
| S2 | A | Upper-right bed, raised mount |
| S3 | B | Right edge near upper water area, raised mount |
| S4 | B | Right edge near lower corner, raised mount |
| S5 | C | Lower-left curved enclosure, raised mount |

This retains V2's default logical grouping; it does not assert that S1 and S2
measure the same precise position. A single representative avatar position is
used for each zone. For example, to swap the grouping, change:

```json
"groups": {"A": [1], "B": [2, 3], "C": [4, 5]}
```

Exactly A/B/C must exist, each nonempty, with inputs 1–5 assigned once.
`zones` controls representative pet positions and colours.
`static/scene.js` controls physical tray/partition shapes.
Geometry coordinates use the sketch convention: x0 left / x10 right;
z15 top / z0 bottom. A presentation transform makes the browser's plan readable;
continue editing in this same sketch convention.

**Calibrate before using real readings.** Current 100 cm baselines and 15 cm drops
are placeholders. Measure the empty prototype at each sensor and set
`baselines_cm`; choose `drop_cm` from actual no-pet versus detected readings.
Distances are centimetres. Valid detection range is 2–400 cm; 0/invalid echo
values do not confirm detection. Send null for a timeout. Missing/null frames do
not count toward confirmation and expire after `stale_seconds` (default 3 s).

There are 3 confirmation and 3 clearing frames by default. A last-known location
holds for up to 1 s after its last supported observation. Across zones,
simultaneous detections report **ambiguous**. An existing supported location is
retained; without one, the pet stays hidden. No second avatar is created.
A browser poll every 500 ms ages live data even when the publisher stops.
Loss of input therefore clears the display after the stale timeout, a possible
hold interval and fade, plus polling delay. Simulation does not expire.

## 4. Connect your Raspberry Pi and existing Tkinter GUI

Your original Tkinter source, GPIO pin map and acquisition loop were not supplied.
This package integrates at the existing five-distance measurement boundary and
does not change your GPIO code or Tk mainloop.

If acquisition is already in a worker thread, publish each completed scan:

```python
from publish_sensor_readings import publish_readings
# Run once per completed five-channel measurement cycle, from a worker:
publish_readings(d1, d2, d3, d4, d5)  # cm; None for missing/timeout
```

If publishing from an existing Tk callback, use the nonblocking queue bridge:

```python
from tkinter_publisher import TwinPublisher
from open_from_tkinter import add_digital_twin_button

publisher = TwinPublisher()  # create once
add_digital_twin_button(root)  # existing Tk root, server already running

# After each complete five-sensor scan:
publisher.submit(d1, d2, d3, d4, d5)
# Add publisher.close() to your existing shutdown handler.
```

The bridge keeps the latest pending frame and performs HTTP on a daemon worker.
It logs network errors and never waits on HTTP in submit(). The synchronous
publisher still has a 1.5 s request timeout. To use your existing grid layout,
use the returned launch button: `button.pack_forget(); button.grid(...)`.
The viewer opens in a browser alongside Tkinter, rather than embedding WebGL
inside a Tk widget.

If server and Pi are on different LAN machines:

- Server: set `PET_HOST=0.0.0.0` before starting `server.py`.
- Publisher machine: set `PET_SERVER_URL=http://SERVER_LAN_IP:5000`.
- Tk machine: set `PET_VIEW_URL=http://SERVER_LAN_IP:5000` for its launch button.
- Allow TCP 5000 on the server's LAN firewall.

PowerShell environment example: `$env:PET_HOST="0.0.0.0"`.
Linux example: `export PET_SERVER_URL=http://192.168.1.20:5000`.
Optional `PET_PORT` changes the listening port (default 5000).
Optional `PET_API_TOKEN` must match on publisher/server; the browser prompts for
it when a protected simulation is requested and keeps it for that tab session.
Tokens protect writes, not read-only viewing. Use on a trusted LAN; the bundled
Flask development server uses HTTP and is not an internet deployment.

Stagger the five ultrasonic trigger pulses in your existing GPIO acquisition
code to avoid cross-talk. Keep the electrical interface already validated for
your Pi; this project adds no GPIO driver or wiring changes.

## 5. Test without hardware

Use the buttons first, then exercise the actual distance endpoint:

```bash
python simulate_readings.py
```

Run this in a second terminal using the same virtual-environment Python as the
server (Windows: `.venv\Scripts\python.exe`; Linux: `.venv/bin/python`). It cycles
all five channels with coherent distance frames, then clears them. Ctrl+C stops
it; the viewer subsequently marks live input stale.

Backend regression tests:

```bash
python -m unittest -v test_tracker.py
```

Optional developer browser checks (requires Node and Playwright, not needed for
normal operation): `npm install --no-save playwright`, `npx playwright install chromium`,
start the Python server, then `node browser_test.cjs`. Screenshots go to the OS
`/tmp` path by default; on Windows set `PET_SCREENSHOT_DIR` to an existing directory.
Use `PET_TEST_URL` for another host/port. See `TEST_REPORT.md` for observed results
and the checks that still need your actual Pi hardware.

## Files

- `server.py`: unchanged HTTP/Socket.IO architecture plus configuration endpoint
- `tracker.py`, `config.json`: mapping, thresholds, debounce, freshness and state
- `static/scene.js`: tray, partitions, sensors, one pet, fades and controls
- `static/vendor/`: local Three.js 0.167.1, OrbitControls, Socket.IO client 4.7.5 + licences
- `publish_sensor_readings.py`: existing synchronous five-distance publisher
- `tkinter_publisher.py`: optional nonblocking latest-frame bridge
- `open_from_tkinter.py`: existing GUI launch helper
- `simulate_readings.py`, `test_tracker.py`, `browser_test.cjs`: repeatable checks
