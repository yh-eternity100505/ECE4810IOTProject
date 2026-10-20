#!/usr/bin/env python3
"""Run a GUI hub, or tunnel accepted readings to an existing standalone hub."""
import argparse
from pathlib import Path
import selectors
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import json


def phone_snapshot(data_dir):
    """Read only the public pet/hardware fields; never send account or bot secrets."""
    path = Path(data_dir) / "hub_settings.json"
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        return None
    from pi_hub import DEFAULT_SETTINGS, SENSOR_PINS
    data = json.loads(path.read_text(encoding="utf-8"))
    pet = {**DEFAULT_SETTINGS["pet"], **data.get("pet", {})}
    config = data.get("sensors", DEFAULT_SETTINGS["sensors"])
    layout = data.get("sensor_layout", DEFAULT_SETTINGS["sensor_layout"])
    sensors = {}
    for sensor, pins in SENSOR_PINS.items():
        place = layout.get(sensor, DEFAULT_SETTINGS["sensor_layout"][sensor])
        entry = {"pins": pins, "location": {key: place.get(key) for key in ("x", "z", "facing", "mount", "note")}}
        if sensor == "5":
            entry["role"] = "entrance"
        else:
            calibration = data.get("calibration", {}).get(sensor)
            entry.update(role=config["mapping"][sensor], threshold_cm=config["thresholds"][sensor],
                         calibration={key: calibration[key] for key in ("full_cm", "empty_cm")} if calibration else None)
        sensors[sensor] = entry
    return {"type": "HUB_SYNC", "revision": config["revision"], "pets_total": max(1, len(data.get("pets", []))),
            "pet": {key: pet.get(key) for key in ("name", "animal", "breed", "sex", "birthday", "weight_kg", "avatar", "baseline_sleep_hours", "baseline_toilet_visits")},
            "sensors": sensors}


def watch_phone_settings(data_dir, stop):
    last = None
    elapsed = 5
    while not stop.is_set():
        try:
            snapshot = phone_snapshot(data_dir)
            if snapshot is not None and (snapshot != last or elapsed >= 5):
                encoded = json.dumps(snapshot, separators=(",", ":"), allow_nan=False)
                print("[PETCARE_HUB_SYNC] " + encoded, flush=True)
                last = snapshot
                elapsed = 0
        except (OSError, ValueError, KeyError, TypeError):
            pass  # A settings save may be in progress; retry without printing private data.
        stop.wait(1)
        elapsed += 1


def service_active(name):
    result = subprocess.run(["systemctl", "is-active", "--quiet", name],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    return result.returncode == 0


class HubProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class ProxyConnection(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with socket.create_connection(("127.0.0.1", 5005), timeout=3) as upstream:
                self.request.settimeout(2)
                upstream.settimeout(2)
                with selectors.DefaultSelector() as selector:
                    selector.register(self.request, selectors.EVENT_READ, upstream)
                    selector.register(upstream, selectors.EVENT_READ, self.request)
                    while not self.server.stop.is_set():
                        for key, _ in selector.select(.2):
                            data = key.fileobj.recv(4096)
                            if not data:
                                return
                            key.data.sendall(data)
        except OSError:
            return


def reuse_hub(sensor_port, stop):
    with HubProxy(("127.0.0.1", sensor_port), ProxyConnection) as proxy:
        proxy.stop = stop
        worker = threading.Thread(target=proxy.serve_forever, daemon=True)
        worker.start()
        print("[PETCARE_HUB_LEGACY]", flush=True)
        print("[PETCARE_HUB_READY]", flush=True)
        stop.wait()
        proxy.shutdown()
        worker.join(timeout=2)


def run_session(data_dir, sensor_port):
    process = None
    watcher = None
    stop = threading.Event()
    previous_handlers = {}
    for name in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous_handlers[name] = signal.signal(name, lambda *_: stop.set())
    try:
        if service_active("petcare-sensors"):
            raise RuntimeError("The system petcare-sensors service already owns GPIO; stop it before using the GUI-managed encrypted sender")
        watcher = threading.Thread(target=watch_phone_settings, args=(data_dir, stop), daemon=True)
        watcher.start()
        if service_active("petcare-hub"):
            reuse_hub(sensor_port, stop)
            return 0
        process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).with_name("pi_hub.py")),
                                    "--dashboard-source", "--sensor-port", str(sensor_port),
                                    "--data-dir", str(data_dir)])
        while process.poll() is None and not stop.wait(.2):
            pass
        return process.poll() or 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print("[PETCARE_HUB_ERROR] " + str(exc), flush=True)
        return 1
    finally:
        stop.set()
        if watcher is not None:
            watcher.join(timeout=2)
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        for name, handler in previous_handlers.items():
            signal.signal(name, handler)


def main():
    parser = argparse.ArgumentParser(description="GUI-managed PetCare phone hub session")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--sensor-port", type=int, default=5007)
    args = parser.parse_args()
    return run_session(args.data_dir, args.sensor_port)


if __name__ == "__main__":
    raise SystemExit(main())
