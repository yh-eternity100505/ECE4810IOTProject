# PetCare AI - current version, 13 October 2026

This is a standalone copy of the laptop GUI, Raspberry Pi service, trained model, source code, tests and integration instructions.

This snapshot includes the restored dark teal theme, compact animated pet illustrations, and the previous panel dimensions. Light theme remains available in Settings. Live readings, container calibration, daily activity timing and Telegram alerts are included.

## Laptop GUI (Windows)

1. Extract the ZIP completely. Install Python 3.11 if it is not installed (the GUI was tested with Python 3.11).
2. Open PowerShell in this extracted folder and run:

```powershell
cd GUI
py -3.11 -m venv .venv-local
.\.venv-local\Scripts\python.exe -m pip install -r requirements-tested.txt
.\.venv-local\Scripts\python.exe main.py
```

After setup, double-click **GUI/Launch PetCare AI.vbs**. It uses python.exe with its terminal hidden. Demo login: **admin / pet123**. Sensor Hub supports Demo mode without hardware. The app creates a fresh database and logs on first run.

## Raspberry Pi

Read **raspberry_pi/PI_SETUP.md** for wiring. Sensor 5 remains fixed for entrance gesture KNN. Copy the raspberry_pi folder to the Pi. Use your existing model-compatible environment or install the requirements in a Pi virtual environment:

```bash
cd raspberry_pi
python3 -m venv env --system-site-packages
source env/bin/activate
python -m pip install -r requirements.txt
python predict_movement.py --gui-host YOUR_LAPTOP_IPV4
```

The bundled model requires scikit-learn 1.6.1. For exact reproducibility, also use the numpy/scipy versions from training if available. RPi.GPIO must support your Pi model and OS. The laptop GUI environment and Pi environment are separate.

On the GUI, select **Live Pi**, open **Connect Pi / Gesture access**, and start the receiver on **0.0.0.0:5005**. Both devices must be able to communicate on the network. Allow the GUI's actual python.exe through Windows Firewall for inbound TCP 5005 if needed. The Pi obtains saved assignments automatically. No real Telegram messages are sent until the owner configures Real Telegram mode.

To test without hardware or the model:

```bash
python predict_movement.py --gui-host YOUR_LAPTOP_IPV4 --simulate
```

## Included features

- Continuous live readings across Sensor Hub, Live Sensors, Overview, Activity Analysis, History and Movement & Access.
- Four configurable inputs for food level, water level, sleeping or toilet; fixed fifth input for gesture KNN and live distance.
- Sleeping below 14 cm and toilet below 13 cm by default, with estimated current session time and daily hours.
- Food/water percentages, optional estimated litres and level history after full/empty calibration.
- PASS_BY event logging, APPROACH_STAY entrance Telegram alerts, AUTH_GESTURE username/OTP authentication.
- Existing Telegram configuration, local credential encryption, and original demo analytics in Demo mode.

Food/water calibration must be measured for the actual containers. Capacity and a uniform-cross-section assumption are needed for linear litre estimates. Sleep/toilet labels are proximity estimates. The lab TCP link is unencrypted.

## Personal data and portability

Saved Telegram tokens/chat IDs, user database, personal records, logs, screenshots, caches and machine-specific Python environments are not included. Your friend configures their own Telegram bot/chat, sensor assignments, thresholds, pet profile and container calibration. The built-in demo login is included intentionally. Package manifests verify the copied files; they do not contain personal settings.

The source in this folder is the preserved current snapshot. Setup installs dependencies on the destination computer rather than copying an environment tied to the original machine.

## Tests

From GUI, run `.\.venv-local\Scripts\python.exe -m unittest discover -s tests -v`. The Pi protocol tests use the GUI Python/Qt dependencies on a development laptop. From the package root, run `GUI\.venv-local\Scripts\python.exe -m unittest discover -s raspberry_pi -p test_*.py -v`.


