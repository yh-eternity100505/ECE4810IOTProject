"""ECE4810 Activity 2: Raspberry Pi ultrasonic movement classification service.

The Raspberry Pi:
    1. Reads one HC-SR04 sensor.
    2. Triggers when a valid distance is below 20 cm.
    3. Records 60 readings at 10 Hz (approximately six seconds).
    4. Extracts the *same* 14 features as the trained KNN model.
    5. Sends newline-delimited JSON events over TCP to a laptop GUI.

The laptop GUI must run a TCP *server* listening on GUI_PORT. This script
is the TCP *client*. The GUI decides what to do with each classification;
there is no authentication, OTP, database, or Telegram code on the Pi.

Requires: RPi.GPIO, numpy, joblib, scikit-learn (matching model version).
Keep movement_knn_model.joblib in the same directory as this script.
"""

import json
import queue
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import RPi.GPIO as GPIO


# ============================================================
# 1. USER SETTINGS: CHANGE GUI_HOST TO YOUR LAPTOP'S IPv4 ADDRESS
# ============================================================
GUI_HOST = "192.168.1.100"  # EXAMPLE ONLY: replace with the laptop IPv4
GUI_PORT = 5055             # Must match the TCP listener in the GUI

MODEL_FILE = Path(__file__).resolve().parent / "movement_knn_model.joblib"

# BOARD pin numbering, identical to the original program.
PIN_TRIGGER = 7
PIN_ECHO = 11

TRIGGER_DISTANCE_CM = 20.0
RESET_DISTANCE_CM = 20.0
MAX_VALID_DISTANCE_CM = 200.0
ECHO_TIMEOUT_S = 0.03
CONNECT_TIMEOUT_S = 0.75
SOCKET_TIMEOUT_S = 0.75
RESET_CONFIRM_READINGS = 3


# ============================================================
# 2. LOAD THE EXISTING TRAINED MODEL (NO RETRAINING)
# ============================================================
def load_model_bundle():
    bundle = joblib.load(MODEL_FILE)
    required = (
        "model", "feature_names", "window_size", "sample_interval",
        "close_cm", "mid_max_cm", "crossing_cm",
    )
    missing = [key for key in required if key not in bundle]
    if missing:
        raise ValueError(f"Model bundle is missing keys: {missing}")

    model = bundle["model"]
    feature_names = list(bundle["feature_names"])
    window_size = int(bundle["window_size"])
    sample_interval = float(bundle["sample_interval"])

    if len(feature_names) != 14:
        raise ValueError("This detector expects the existing 14-feature model.")
    if window_size < 2 or sample_interval <= 0:
        raise ValueError("Invalid window_size or sample_interval in model bundle.")

    print(f"Model: {MODEL_FILE}")
    print(f"Classes: {list(model.classes_)}")
    print(f"Features: {len(feature_names)}")
    print(f"Window: {window_size} samples at {1 / sample_interval:.1f} Hz")
    return bundle, model, feature_names, window_size, sample_interval


# ============================================================
# 3. NON-BLOCKING TCP SENDER
# ============================================================
class GuiEventSender:
    """Network I/O runs in a background thread, never in the sensor loop.

    Delivery is best effort. If the GUI is offline, events are discarded
    instead of replayed later (a stale AUTH_GESTURE must not initiate login).
    A new connection is attempted for the next event.
    """

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.events = queue.Queue(maxsize=32)
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.worker.start()

    def send(self, event_type, **data):
        event = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # Keep the sensor operating and discard stale pending messages.
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            try:
                self.events.put_nowait(event)
            except queue.Full:
                pass

    def _run(self):
        connection = None
        while not self.stop_event.is_set() or not self.events.empty():
            try:
                event = self.events.get(timeout=0.1)
            except queue.Empty:
                continue

            # If messages have been stuck in the queue, they must not
            # trigger a delayed security action when the GUI returns.
            emitted_at = datetime.fromisoformat(event["timestamp"])
            if (datetime.now(timezone.utc) - emitted_at).total_seconds() > 3.0:
                continue

            message = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
            try:
                if connection is None:
                    connection = socket.create_connection(
                        (self.host, self.port), timeout=CONNECT_TIMEOUT_S
                    )
                    connection.settimeout(SOCKET_TIMEOUT_S)
                    print(f"[TCP] Connected to GUI at {self.host}:{self.port}")
                connection.sendall(message)
                print(f"[TCP] Sent: {event['type']}")
            except OSError as exc:
                print(f"[TCP] GUI unreachable; {event['type']} not sent: {exc}")
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
                connection = None

        if connection is not None:
            connection.close()

    def close(self):
        self.stop_event.set()
        self.worker.join(timeout=2.0)


# ============================================================
# 4. ULTRASONIC SENSOR (BOARD 7 TRIGGER, BOARD 11 ECHO)
# ============================================================
def setup_sensor():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(PIN_TRIGGER, GPIO.OUT)
    GPIO.setup(PIN_ECHO, GPIO.IN)
    GPIO.output(PIN_TRIGGER, GPIO.LOW)
    time.sleep(1.0)


def calculate_distance():
    """Return centimetres or None for a missing/invalid echo."""
    GPIO.output(PIN_TRIGGER, GPIO.LOW)
    time.sleep(0.000002)
    GPIO.output(PIN_TRIGGER, GPIO.HIGH)
    time.sleep(0.00001)
    GPIO.output(PIN_TRIGGER, GPIO.LOW)

    deadline = time.monotonic() + ECHO_TIMEOUT_S
    while GPIO.input(PIN_ECHO) == GPIO.LOW:
        if time.monotonic() >= deadline:
            return None

    pulse_start = time.monotonic()
    deadline = pulse_start + ECHO_TIMEOUT_S
    while GPIO.input(PIN_ECHO) == GPIO.HIGH:
        if time.monotonic() >= deadline:
            return None

    pulse_end = time.monotonic()
    distance_cm = round((pulse_end - pulse_start) * 17150.0, 2)
    if not (0 < distance_cm <= MAX_VALID_DISTANCE_CM):
        return None
    return distance_cm


# ============================================================
# 5. EXACTLY THE ORIGINAL 14 FEATURE DEFINITIONS
# ============================================================
def extract_features(sequence, feature_names, window_size, sample_interval,
                     close_cm, mid_max_cm, crossing_cm):
    distances = np.asarray(sequence, dtype=float)
    if distances.shape != (window_size,):
        raise ValueError(f"Expected {window_size} distances; got {distances.shape}")

    time_axis = np.arange(window_size) * sample_interval
    close = distances < close_cm
    mid = (distances >= close_cm) & (distances <= mid_max_cm)
    far = distances > mid_max_cm
    below_threshold = distances < crossing_cm

    start_mean = np.mean(distances[:5])
    end_mean = np.mean(distances[-5:])
    feature_dict = {
        "mean_distance": np.mean(distances),
        "min_distance": np.min(distances),
        "max_distance": np.max(distances),
        "std_distance": np.std(distances),
        "q1": np.percentile(distances, 25),
        "q3": np.percentile(distances, 75),
        "start_mean": start_mean,
        "end_mean": end_mean,
        "end_minus_start": end_mean - start_mean,
        "slope": np.polyfit(time_axis, distances, 1)[0],
        "close_ratio": np.mean(close),
        "mid_ratio": np.mean(mid),
        "far_ratio": np.mean(far),
        "threshold_crossings": np.count_nonzero(
            below_threshold[1:] != below_threshold[:-1]
        ),
    }
    # IMPORTANT: the order is loaded from the training bundle.
    return np.array([feature_dict[name] for name in feature_names], dtype=float)


# ============================================================
# 6. RECORD EXACTLY window_size READINGS AT THE TRAINED INTERVAL
# ============================================================
def record_movement(first_distance, window_size, sample_interval):
    readings = [first_distance]
    last_valid_distance = first_distance
    started_at = time.monotonic()

    print(f"[SENSOR] Triggered at {first_distance:.2f} cm; recording...")
    for sample_index in range(1, window_size):
        # Schedule each sample at t = index * interval, rather than
        # taking the second reading immediately after the first.
        target_time = started_at + sample_index * sample_interval
        remaining = target_time - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

        distance = calculate_distance()
        if distance is None:
            # Same forward-fill policy as the existing script.
            distance = last_valid_distance
        else:
            last_valid_distance = distance
        readings.append(distance)

    print(f"[SENSOR] Recorded {len(readings)} readings")
    return np.asarray(readings, dtype=float)


# ============================================================
# 7. PREDICT CLASS + CONFIDENCE
# ============================================================
def classify_movement(sequence, bundle, model, feature_names,
                      window_size, sample_interval):
    features = extract_features(
        sequence, feature_names, window_size, sample_interval,
        float(bundle["close_cm"]), float(bundle["mid_max_cm"]),
        float(bundle["crossing_cm"]),
    )
    x = features.reshape(1, -1)
    prediction = str(model.predict(x)[0])
    probabilities = model.predict_proba(x)[0]
    classes = [str(label) for label in model.classes_]
    confidence = float(probabilities[classes.index(prediction)])
    print(f"[ML] {prediction} | confidence: {confidence:.1%}")
    return prediction, confidence


# ============================================================
# 8. REARM ONLY AFTER THE AREA CLEARS
# ============================================================
def wait_for_reset(sample_interval):
    print("[SENSOR] Waiting for area to clear...")
    clear_readings = 0
    while True:
        started_at = time.monotonic()
        distance = calculate_distance()
        if distance is not None and distance >= RESET_DISTANCE_CM:
            clear_readings += 1
            if clear_readings >= RESET_CONFIRM_READINGS:
                print("[SENSOR] Area clear; detector rearmed")
                return
        else:
            clear_readings = 0
        time.sleep(max(0, sample_interval - (time.monotonic() - started_at)))


# ============================================================
# 9. MAIN - SEND STATUS + RESULT TO THE LAPTOP GUI
# ============================================================
def main():
    bundle, model, feature_names, window_size, sample_interval = load_model_bundle()
    sender = GuiEventSender(GUI_HOST, GUI_PORT)
    setup_sensor()
    sender.start()

    print(f"GUI destination: {GUI_HOST}:{GUI_PORT}")
    print(f"Trigger: distance < {TRIGGER_DISTANCE_CM:g} cm")
    print("Detector running; press Ctrl+C to stop.\n")
    sender.send("IDLE")

    try:
        while True:
            started_at = time.monotonic()
            distance = calculate_distance()
            if distance is not None and distance < TRIGGER_DISTANCE_CM:
                sender.send("TRIGGERED")
                sequence = record_movement(distance, window_size, sample_interval)
                prediction, confidence = classify_movement(
                    sequence, bundle, model, feature_names,
                    window_size, sample_interval,
                )
                sender.send(
                    "MOVEMENT_RESULT",
                    prediction=prediction,
                    confidence=round(confidence, 4),
                )
                wait_for_reset(sample_interval)
                sender.send("IDLE")
                print("[SENSOR] Ready for next movement\n")

            time.sleep(max(0, sample_interval - (time.monotonic() - started_at)))
    except KeyboardInterrupt:
        print("\nStopping movement detector...")
    finally:
        sender.close()
        GPIO.cleanup()
        print("GPIO cleaned up")


if __name__ == "__main__":
    main()
