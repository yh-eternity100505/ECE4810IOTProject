"""Deploy a separate GUI-managed phone hub and run it over the existing SSH identity."""
from pathlib import Path
import shlex
import shutil
import socket
from PySide6.QtCore import QObject, QProcess, Signal


class PiHubLauncher(QObject):
    ready = Signal(int, str)
    status = Signal(str)
    stopped = Signal()
    REMOTE_ROOT = "/home/jiating/.cache/petcare/gui-hub"
    DATA_DIR = "/home/jiating/Desktop/pi_hub/data"
    REMOTE_PORT = 5007

    def __init__(self, address, parent=None):
        super().__init__(parent)
        self.address = address
        self.key_path = Path.home() / ".ssh" / "petcare_pi"
        self.source = Path(__file__).resolve().parents[3] / "pi_hub"
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.phase = None
        self.local_port = None
        self._buffer = b""
        self._ready = False
        self._closing = False
        self._failure_detail = ""
        self.legacy_hub = False

    @property
    def is_ready(self):
        return self._ready

    def _options(self):
        return ["-i", str(self.key_path), "-o", "IdentitiesOnly=yes",
                "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", "ConnectTimeout=10"]

    def start(self):
        if self.phase is not None:
            return
        if not shutil.which("ssh") or not shutil.which("scp") or not self.key_path.is_file():
            self.status.emit("Phone app: SSH, SCP or the existing petcare_pi identity is unavailable")
            return
        files = ("pi_hub.py", "gui_session.py", "web/index.html", "web/manifest.webmanifest", "web/icon.svg")
        if not all((self.source / file).is_file() for file in files):
            self.status.emit("Phone app: pi_hub source folder is missing")
            return
        self._ready = self.legacy_hub = self._closing = False
        self._buffer = b""
        self._failure_detail = ""
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.local_port = listener.getsockname()[1]
        self.phase = "prepare"
        self.status.emit("Phone app: preparing the Pi hub over SSH")
        self.process.start(shutil.which("ssh"), self._options() + [self.address,
                           "mkdir -p " + shlex.quote(self.REMOTE_ROOT + "/web")])

    def _read(self):
        self._buffer = (self._buffer + bytes(self.process.readAllStandardOutput()))[-16384:]
        if b"[PETCARE_HUB_LEGACY]" in self._buffer:
            self.legacy_hub = True
        for line in self._buffer.splitlines(keepends=True):
            if b"[PETCARE_HUB_ERROR]" in line and line.endswith((b"\n", b"\r")):
                self._failure_detail = line.split(b"[PETCARE_HUB_ERROR]", 1)[1].decode("utf-8", errors="replace").strip()[:300]
        if self.phase == "run" and not self._ready and b"[PETCARE_HUB_READY]" in self._buffer:
            self._ready = True
            host = self.address.rsplit("@", 1)[-1]
            url = f"http://{host}:8080"
            self.status.emit("Phone app ready: " + url)
            self.ready.emit(self.local_port, url)

    def _finished(self, code, _status):
        self._read()
        if self._closing:
            return
        if code:
            # Do not print remote logs, settings, or command output containing private data.
            detail = self._failure_detail or ("Another GUI-managed hub is running" if code == 75 else
                     "Check SSH access and ports 8080/5007 on the Pi")
            self.status.emit(f"Phone app: {self.phase} failed (exit {code}). {detail}")
            self.phase = None
            self.stopped.emit()
            return
        self._buffer = b""
        if self.phase == "prepare":
            self.phase = "upload hub"
            self.process.start(shutil.which("scp"), self._options() +
                               [str(self.source / name) for name in ("pi_hub.py", "gui_session.py")] +
                               [f"{self.address}:{self.REMOTE_ROOT}/"])
        elif self.phase == "upload hub":
            self.phase = "upload web"
            self.process.start(shutil.which("scp"), self._options() +
                               [str(self.source / "web" / name) for name in ("index.html", "manifest.webmanifest", "icon.svg")] +
                               [f"{self.address}:{self.REMOTE_ROOT}/web/"])
        elif self.phase == "upload web":
            self._run_hub()
        else:
            self.phase = None
            self.status.emit("Phone app stopped")
            self.stopped.emit()

    def _run_hub(self):
        self.phase = "run"
        self._ready = False
        command = ("exec flock -n -E 75 " +
                   shlex.quote(self.REMOTE_ROOT + "/hub.lock") + " python3 -u " +
                   shlex.quote(self.REMOTE_ROOT + "/gui_session.py") + " --sensor-port " +
                   str(self.REMOTE_PORT) + " --data-dir " + shlex.quote(self.DATA_DIR))
        self.status.emit("Phone app: starting hub and SSH tunnel")
        self.process.start(shutil.which("ssh"), self._options() +
                           ["-tt", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=10",
                            "-o", "ServerAliveCountMax=3", "-L",
                            f"127.0.0.1:{self.local_port}:127.0.0.1:{self.REMOTE_PORT}", self.address, command])

    def _error(self, error):
        if not self._closing and error == QProcess.ProcessError.FailedToStart:
            self.phase = None
            self.status.emit("Phone app: could not start SSH/SCP")
            self.stopped.emit()

    def stop(self):
        self._closing = True
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        if self.phase == "run":
            self.process.write(b"\x03")
            self.process.waitForBytesWritten(300)
        if not self.process.waitForFinished(1500):
            self.process.terminate()
            if not self.process.waitForFinished(1000):
                self.process.kill()
                self.process.waitForFinished(1000)
