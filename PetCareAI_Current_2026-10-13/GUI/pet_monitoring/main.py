"""Start the desktop GUI and encrypted Raspberry Pi sender."""

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil
import sys

from PySide6.QtCore import QTimer, QProcess
from PySide6.QtWidgets import QApplication

from pet_monitoring.services.database_service import DatabaseService
from pet_monitoring.controller import MonitoringController
from pet_monitoring.ui.main_window import MainWindow
from pet_monitoring.services.pi_hub_bridge import PiHubBridge
from pet_monitoring.services.pi_hub_launcher import PiHubLauncher


ROOT = Path(__file__).resolve().parent.parent

PI_ADDRESS = "jiating@10.156.153.139"
PI_PYTHON = "/home/jiating/Desktop/env/bin/python"
PI_SCRIPT = "/home/jiating/Desktop/predict_movement_test.py"
PI_KEY_FILE = "/home/jiating/.config/petcare/edc.env"
PI_LOCK_FILE = "/home/jiating/.config/petcare/sender.lock"


def setup_logging():
    (ROOT / "logs").mkdir(exist_ok=True)

    handler = RotatingFileHandler(
        ROOT / "logs" / "app.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )

    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def start_pi_sender(app, port=5005):
    """Launch one Pi sender without blocking the GUI."""
    existing = getattr(app, "pi_sender", None)
    if existing is not None and existing.state() != QProcess.NotRunning:
        print("[PI] SSH launcher is already running.", flush=True)
        return

    ssh_program = shutil.which("ssh")
    key_path = Path.home() / ".ssh" / "petcare_pi"

    if ssh_program is None:
        print("[PI] Cannot start: ssh was not found.", flush=True)
        return

    if not key_path.is_file():
        print(f"[PI] Cannot find SSH key: {key_path}", flush=True)
        return

    port = int(port)

    # Stop immediately if loading the EDC keys fails.
    # SSH_CONNECTION's first field is the laptop's address.
    command = (
        "set -e; "
        f". {PI_KEY_FILE}; "
        "set -- $SSH_CONNECTION; "
        'test -n "${1:-}"; '
        f"cd /home/jiating/Desktop; "
        f"exec flock -n -E 75 {PI_LOCK_FILE} "
        f"{PI_PYTHON} -u {PI_SCRIPT} "
        f'--gui-host "$1" --gui-port {port} --edc'
    )

    process = QProcess(app)
    app.pi_sender = process
    process.setProcessChannelMode(QProcess.MergedChannels)

    def show_output():
        output = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if output:
            print(output, end="", flush=True)

    def finished(code, status):
        show_output()

        if code == 75:
            print(
                "[PI] Another GUI-managed sender holds the sender lock.",
                flush=True,
            )
        else:
            print(
                f"[PI] SSH sender exited with code {code}.",
                flush=True,
            )

    def shutdown():
        if process.state() == QProcess.NotRunning:
            return

        # The remote terminal translates Ctrl+C into an interrupt,
        # allowing the sender to run its GPIO cleanup.
        process.write(b"\x03")
        process.waitForBytesWritten(300)

        if not process.waitForFinished(1500):
            process.terminate()

            if not process.waitForFinished(1000):
                process.kill()
                process.waitForFinished(1000)

    process.readyReadStandardOutput.connect(show_output)
    process.finished.connect(finished)
    process.errorOccurred.connect(
        lambda error: print(
            f"[PI] SSH error: {process.errorString()}",
            flush=True,
        )
    )
    app.aboutToQuit.connect(shutdown)

    print(f"[PI] Starting encrypted sender on {PI_ADDRESS}...", flush=True)

    process.start(
        ssh_program,
        [
            "-tt",
            "-i", str(key_path),
            "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=10",
            "-o", "ServerAliveCountMax=3",
            PI_ADDRESS,
            command,
        ],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="PetCare AI behavioural monitoring"
    )

    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "data" / "pet_monitor.db",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Open the GUI, then close automatically after 2 seconds",
    )
    parser.add_argument(
        "--setup-edc",
        action="store_true",
        help="Save matching Pi encryption keys once",
    )

    parser.add_argument("--setup-admin", action="store_true",
                        help="One-time local administrator password and Telegram OTP enrollment")
    args = parser.parse_args(argv)

    if args.setup_edc:
        from pet_monitoring.security.edc_settings import setup_keys
        return setup_keys()

    setup_logging()
    logging.info("Application startup")

    app = QApplication(sys.argv[:1])
    app.setApplicationName("PetCare AI")
    app.setStyle("Fusion")

    if args.setup_admin:
        from pet_monitoring.services.authentication_service import AuthenticationService
        from pet_monitoring.ui.admin_setup import run_setup
        from pet_monitoring.ui.theme import apply_theme
        apply_theme(app, "Dark")
        database = DatabaseService(args.database)
        try:
            return run_setup(AuthenticationService(database))
        finally:
            database.close()

    database = DatabaseService(args.database)
    controller = MonitoringController(database)
    window = MainWindow(controller)
    window.show()

    receiver = window.movement.receiver
    phone_bridge = PiHubBridge(receiver, window.hub, app)
    phone_launcher = PiHubLauncher(PI_ADDRESS, app)
    # Keep the accepted-message relay separate from the original decoder and SSH sender.
    def phone_ready(port, url):
        phone_bridge.start(port, legacy=phone_launcher.legacy_hub)
        window.set_phone_app_status("Phone app ready", url, phone_launcher.phone_url)
        # The session has checked that a system sensor service does not already own GPIO.
        start_pi_sender(app, receiver.port)
        # Stop the sender first, then close only our hub/tunnel session.
        app.aboutToQuit.connect(phone_launcher.stop)

    phone_launcher.ready.connect(phone_ready)
    phone_launcher.synced.connect(window.hub_sync.apply)
    phone_launcher.status.connect(window.set_phone_app_status)
    phone_bridge.status.connect(window.set_phone_app_status)
    phone_launcher.stopped.connect(phone_bridge.stop)
    phone_launcher.stopped.connect(window.phone_app_stopped)
    app.aboutToQuit.connect(phone_bridge.stop)
    app.aboutToQuit.connect(lambda: phone_launcher.stop() if not phone_launcher.is_ready else None)

    def start_connections():
        receiver_config = database.get(
            "movement_receiver",
            {"host": "0.0.0.0", "port": 5005},
        )

        host = receiver_config.get("host", "0.0.0.0")
        port = receiver_config.get("port", 5005)

        if not receiver.start(host, port):
            print(
                "[GUI] Receiver failed to start; Pi sender was not launched.",
                flush=True,
            )
            return

        print(
            f"[GUI] Receiver listening on {host}:{receiver.port}",
            flush=True,
        )

        phone_launcher.start()

    app.aboutToQuit.connect(receiver.stop)

    if args.smoke_test:
        QTimer.singleShot(2000, window.close)
    else:
        QTimer.singleShot(0, start_connections)

    try:
        code = app.exec()
    finally:
        database.close()
        logging.info("Application shutdown")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
