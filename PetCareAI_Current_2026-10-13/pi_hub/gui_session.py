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
    stop = threading.Event()
    previous_handlers = {}
    for name in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous_handlers[name] = signal.signal(name, lambda *_: stop.set())
    try:
        if service_active("petcare-sensors"):
            raise RuntimeError("The system petcare-sensors service already owns GPIO; stop it before using the GUI-managed encrypted sender")
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
