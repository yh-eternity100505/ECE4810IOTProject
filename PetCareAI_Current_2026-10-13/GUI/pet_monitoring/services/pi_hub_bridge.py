"""Forward accepted readings through a localhost SSH tunnel, without another decode."""
import json
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtNetwork import QAbstractSocket, QTcpSocket


class PiHubBridge(QObject):
    status = Signal(str)
    MAX_PENDING = 131072

    def __init__(self, receiver, hub, parent=None, settings_sync=None):
        super().__init__(parent)
        self.receiver, self.hub = receiver, hub
        self.settings_sync = settings_sync
        self._sent_operation = None
        if settings_sync is not None:
            settings_sync.outgoing_changed.connect(self.send_settings)
        self.port = None
        self.source_connected = False
        self.legacy = False
        self._incoming = b""
        self._remote_config = None
        self._remote_ack = None
        self._configuration = None
        self.socket = QTcpSocket(self)
        self.socket.setReadBufferSize(8192)
        self.socket.connected.connect(self._connected)
        self.socket.disconnected.connect(self._disconnected)
        self.socket.readyRead.connect(self._read)
        self.socket.errorOccurred.connect(lambda _: self.status.emit("Phone app: waiting for SSH tunnel"))
        self.retry = QTimer(self)
        self.retry.setInterval(2000)
        self.retry.timeout.connect(self._connect)
        receiver.received.connect(self.forward)
        receiver.status.connect(self.source_status)
        hub.changed.connect(self.sync_configuration)

    def start(self, port, legacy=False):
        self.port = port
        self.legacy = legacy
        self.retry.start()
        self._connect()

    def stop(self):
        self.port = None
        self.retry.stop()
        self.socket.abort()

    def _connect(self):
        if self.port and (not self.legacy or self.source_connected) and self.socket.state() == QAbstractSocket.SocketState.UnconnectedState:
            self.socket.connectToHost("127.0.0.1", self.port)

    def _connected(self):
        self._configuration = None
        self._incoming = b""
        self._remote_config = self._remote_ack = None
        self.sync_configuration()
        self._sent_operation = None
        self.send_settings()
        self.status.emit("Phone app: connected · receiving accepted Pi readings")

    def _disconnected(self):
        self._configuration = None
        self._sent_operation = None
        if self.port:
            self.status.emit("Phone app: disconnected · retrying")

    def _write(self, message):
        if self.socket.state() != QAbstractSocket.SocketState.ConnectedState:
            return False
        data = json.dumps(message, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        if self.socket.bytesToWrite() + len(data) > self.MAX_PENDING:
            # Drop an overloaded connection rather than accumulate or replay stale telemetry.
            self.socket.abort()
            self.status.emit("Phone app: slow link · reconnecting")
            return False
        return self.socket.write(data) == len(data)

    @Slot(str)
    def source_status(self, status):
        if status.startswith("Raspberry Pi connected"):
            self.source_connected = True
        elif any(word in status.lower() for word in ("disconnected", "stopped", "lost", "invalid", "cannot")):
            self.source_connected = False
        else:
            return
        if self.legacy:
            if self.source_connected:
                self._connect()
            else:
                self.socket.abort()
        self.sync_configuration()

    def _read(self):
        self._incoming += bytes(self.socket.readAll())
        if len(self._incoming) > 8192:
            self.socket.abort()
            return
        while b"\n" in self._incoming:
            line, self._incoming = self._incoming.split(b"\n", 1)
            if not self.legacy:
                try:
                    ack = json.loads(line)
                    if ack.get("type") == "SETTINGS_ACK" and self.settings_sync is not None:
                        from .movement_receiver import parse_hub_sync
                        snapshot = {"type": "HUB_SYNC", **parse_hub_sync(ack["snapshot"])}
                        if ack.get("operation_id") == self._sent_operation:
                            self.settings_sync.acknowledge(self._sent_operation, snapshot)
                            self._sent_operation = None
                            self.send_settings()
                except (ValueError, TypeError, KeyError, AttributeError):
                    self.status.emit("Phone settings: invalid confirmation; reconnecting")
                    self.socket.abort()
                continue
            try:
                config = json.loads(line)
                if (not isinstance(config, dict) or config.get("type") != "SET_CONFIG"
                        or type(config.get("revision")) is not int or config["revision"] < 1):
                    raise ValueError("Invalid hub configuration")
                self._remote_config = config
                self._remote_ack = None
                self.sync_configuration()
            except (ValueError, TypeError, UnicodeError):
                self.socket.abort()
                return

    def send_settings(self):
        if self.settings_sync is None or not self.settings_sync.pending or self._sent_operation:
            return
        if self.legacy:
            self.status.emit("Phone settings sync needs the GUI-managed hub; save remains local")
            return
        operation = self.settings_sync.pending[0]
        if self._write(operation):
            self._sent_operation = operation["operation_id"]

    def _legacy_matches(self):
        config = self._remote_config
        return (config is not None and config.get("mapping") == self.hub.mapping
                and config.get("thresholds_cm") == self.hub.thresholds
                and self.hub.acknowledged == self.hub.revision and self.source_connected)

    @Slot()
    def sync_configuration(self):
        if self.legacy:
            if self._legacy_matches():
                revision = self._remote_config["revision"]
                if self._remote_ack != revision and self._write({"type": "CONFIG_ACK", "revision": revision}):
                    self._remote_ack = revision
                    self.status.emit("Phone app: existing hub receiving live readings")
            else:
                self._remote_ack = None
                if self._remote_config is not None and self.source_connected and self.hub.acknowledged == self.hub.revision:
                    self.status.emit("Phone app: match sensor assignments and thresholds in both apps to share zone readings")
            return
        message = {"type": "DASHBOARD_SOURCE", "mapping": dict(self.hub.mapping),
                   "thresholds_cm": dict(self.hub.thresholds), "revision": self.hub.revision,
                   "acknowledged": self.hub.acknowledged,
                   "connected": self.source_connected}
        if message != self._configuration and self._write(message):
            self._configuration = message

    @Slot(dict)
    def forward(self, message):
        # This signal contains only messages accepted by the original receiver.
        if message.get("type") == "HUB_SYNC":
            return  # comes from the hub itself
        self.sync_configuration()
        if self.legacy:
            if not self.source_connected or message["type"] == "CONFIG_ACK":
                return
            if message["type"] in {"SENSOR_READING", "ZONE_EVENT"}:
                if not self._legacy_matches() or message["revision"] != self.hub.revision:
                    return
                message = {**message, "revision": self._remote_config["revision"]}
        self._write(message)
