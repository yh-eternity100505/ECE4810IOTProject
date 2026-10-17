"""Security presentation only. Cryptography and session decisions stay in services."""
import json
import time
from datetime import datetime
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPlainTextEdit
from .widgets import Card, Page, label, button, table, fill_table, time_text, STATUS_COLORS

EVENT_NAMES = {
    "LOGIN_SUCCESS": "Login successful", "LOGIN_FAILURE": "Login rejected", "LOGIN_LOCKED": "Login temporarily locked",
    "LOGOUT": "Signed out", "SESSION_EXPIRED": "Session expired", "REPEATED_AUTHENTICATION_FAILURE": "Repeated login failures",
    "PACKET_VERIFIED": "Sensor packet verified", "PACKET_DECRYPTED": "Sensor packet decrypted",
    "PACKET_REJECTED_HMAC_FAILURE": "Tampered packet rejected", "PACKET_REJECTED_MALFORMED": "Malformed packet rejected",
    "PACKET_REJECTED_REPLAY": "Replayed or expired packet rejected", "PACKET_REJECTED_DECRYPTION_FAILURE": "Decryption failed",
    "PACKET_REJECTED_INVALID_PAYLOAD": "Invalid recovered reading rejected", "PACKET_REJECTED_SESSION": "Authentication required",
    "SECURITY_MODULE_OFFLINE": "Security module offline", "TELEGRAM_ALERT_SENT": "Security notification sent",
    "TELEGRAM_ALERT_FAILED": "Security notification delivery failed"}


class SecurityStatusCard(Card):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.title = label("Security status", "sectionTitle")
        self.box.addWidget(self.title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(10)
        self.values = {}
        for i, name in enumerate(["Authentication", "Encryption", "Integrity", "Secure packets", "Rejected packets", "Last verification"]):
            cell = QVBoxLayout()
            cell.setSpacing(3)
            cell.addWidget(label(name.upper(), "eyebrow"))
            value = label("—", wrap=True)
            cell.addWidget(value)
            self.values[name] = value
            grid.addLayout(cell, i // 3, i % 3)
        self.box.addLayout(grid)
        controller.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        c, m = self.c, self.c.security.metrics
        state = "FAILED" if not c.security.available else "WARNING" if c.security_warning or not c.auth.is_authenticated else "SECURE"
        self.title.setText("Security status  ·  " + state)
        color = STATUS_COLORS["CRITICAL" if state == "FAILED" else "WARNING" if state == "WARNING" else "NORMAL"]
        self.title.setStyleSheet(f"color: {color};")
        self.values["Authentication"].setText("Authenticated" if c.auth.is_authenticated else "Authentication Required")
        self.values["Encryption"].setText("Active · Demo AES-CTR" if c.security.available and "Demo" in c.security.algorithm else c.security.algorithm)
        self.values["Integrity"].setText("Verification failed · packet rejected" if c.security_warning else "HMAC Verified" if m.verified_packets else "Waiting for packets")
        self.values["Secure packets"].setText(f"{m.accepted_packets} accepted · {m.total_packets} received")
        self.values["Rejected packets"].setText(str(m.rejected_packets))
        self.values["Last verification"].setText(time_text(c.security.last_verification) or "—")


class SecurityPanel(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.signature = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(18)
        box.addWidget(SecurityStatusCard(controller))
        backend = Card("Transport protection")
        self.backend = label("", wrap=True)
        backend.box.addWidget(self.backend)
        backend.box.addWidget(label("Integrity protection: HMAC-SHA256 · verification precedes decryption. Keys stay in memory and are never displayed.", "muted", True))
        self.metrics = label("", wrap=True)
        backend.box.addWidget(self.metrics)
        self.latency = label("", "muted", True)
        backend.box.addWidget(self.latency)
        backend.box.addWidget(label("These metrics describe the legacy demo transport. Inspect actual Pi EDC + HMAC packets through Security Inspector in the sidebar. Counts and timings do not establish cipher strength.", "muted", True))
        backend.box.addWidget(button("Acknowledge security warning", controller.clear_security_warning))
        backend.box.addWidget(button("View Security Log", lambda: self.log.setFocus()))
        box.addWidget(backend)
        events = Card("Security log")
        self.log = table(["Time", "Event", "Source", "Result", "Processing"])
        self.log.setMinimumHeight(270)
        events.box.addWidget(self.log)
        box.addWidget(events)
        controller.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        c, m = self.c, self.c.security.metrics
        self.backend.setText("Active backend: " + c.security.algorithm)
        self.metrics.setText(f"Received: {m.total_packets}  ·  HMAC verified: {m.verified_packets}  ·  Accepted: {m.accepted_packets}  ·  Rejected: {m.rejected_packets}  ·  HMAC success: {m.success_rate:.1f}%")
        self.latency.setText(f"Average encrypt + tag: {m.average_encryption_ms:.3f} ms  /  verify: {m.average_verification_ms:.3f} ms  /  decrypt: {m.average_decryption_ms:.3f} ms")
        signature = [(e.timestamp, e.event_type, e.packet_id) for e in c.security_events]
        if signature != self.signature:
            self.signature = signature
            fill_table(self.log, [(time_text(e.timestamp), EVENT_NAMES.get(e.event_type, e.event_type),
                      e.source_id or ("Session" if e.event_type.startswith("LOGIN") or e.event_type in ("LOGOUT", "SESSION_EXPIRED", "REPEATED_AUTHENTICATION_FAILURE") else "System"),
                      e.verification_status or "Recorded", f"{e.total_ms:.3f} ms") for e in c.security_events])


class SecurityInspector(Page):
    """One immutable live snapshot; stage navigation never calls the codec."""

    STAGES = ("Received packet", "HMAC verification", "Reverse block permutation",
              "Reverse EDC transformations", "Base32 decoding", "Validation and delivery")
    CHECKS = (("packet",), ("hmac",), ("permutation",), ("transformations",),
              ("base32",), ("ciphertext_replay", "metadata", "timestamp", "nonce_metadata",
                            "nonce_replay", "application"))

    def __init__(self, receiver):
        super().__init__("Security Inspector", "EDC + HMAC · live educational cipher inspection")
        self.snapshot = None
        self.paused = False
        self.stage = 0
        self._last_refresh = -float("inf")
        self.box.addWidget(label("One actual received packet, captured during its original decode. "
                                 "Select a stage to freeze it; live monitoring continues. "
                                 "Capture is sampled at most once per second. EDC is educational.", "muted", True))
        controls = QHBoxLayout()
        self.capture = button("Pause capture", self.toggle_capture)
        controls.addWidget(self.capture)
        self.capture_state = label("Live capture · waiting for an encrypted packet", "muted", True)
        controls.addWidget(self.capture_state, 1)
        self.box.addLayout(controls)
        self.summary = label("Waiting for packets on the existing receiver.", wrap=True)
        self.box.addWidget(self.summary)
        timeline = Card("Decode stages")
        grid = QGridLayout()
        self.stage_buttons = []
        for index, title in enumerate(self.STAGES):
            btn = button(f"{index + 1}. {title}", lambda checked=False, i=index: self.select_stage(i))
            btn.setObjectName("inspectorStage")
            btn.setCheckable(True)
            self.stage_buttons.append(btn)
            grid.addWidget(btn, index // 2, index % 2)
        timeline.box.addLayout(grid)
        self.box.addWidget(timeline)
        card = Card()
        self.stage_title = label("1. Received packet", "sectionTitle")
        self.result = label("Waiting", "muted", True)
        card.box.addWidget(self.stage_title)
        card.box.addWidget(self.result)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(320)
        self.details.setMaximumHeight(480)
        self.details.setPlainText("No packet captured yet.")
        card.box.addWidget(self.details)
        navigation = QHBoxLayout()
        self.previous = button("Previous", lambda: self.select_stage(self.stage - 1))
        self.next = button("Next", lambda: self.select_stage(self.stage + 1))
        navigation.addWidget(self.previous)
        navigation.addStretch()
        navigation.addWidget(self.next)
        card.box.addLayout(navigation)
        self.box.addWidget(card)
        self.box.addStretch()
        receiver.inspection.connect(self.accept_snapshot, Qt.ConnectionType.QueuedConnection)
        self.render()

    @Slot(dict)
    def accept_snapshot(self, snapshot):
        if self.paused or time.monotonic() - self._last_refresh < 1.0:
            return
        self._last_refresh = time.monotonic()
        self.snapshot = snapshot
        self.render()

    def toggle_capture(self):
        self.paused = not self.paused
        self.capture.setText("Resume capture" if self.paused else "Pause capture")
        self.capture_state.setText("Frozen packet · monitoring continues" if self.paused
                                   else "Live capture · waiting for the next sampled packet")

    def select_stage(self, stage):
        if not 0 <= stage < len(self.STAGES):
            return
        if not self.paused:
            self.toggle_capture()
        self.stage = stage
        self.render()

    def render(self):
        self.previous.setEnabled(self.stage > 0)
        self.next.setEnabled(self.stage < 5)
        for index, btn in enumerate(self.stage_buttons):
            btn.setChecked(index == self.stage)
        self.stage_title.setText(f"{self.stage + 1}. {self.STAGES[self.stage]}")
        s = self.snapshot
        if not s:
            return
        received = datetime.fromtimestamp(s["received_at"]).astimezone().isoformat(timespec="seconds")
        outcome = s["outcome"]
        self.summary.setText(f"{outcome} · received {received}" +
                             (f"\nFailed stage: {s['failed_stage']} · {s['error']}" if outcome == "Rejected" else ""))
        checks = s.get("checks", {})
        self.result.setText("\n".join(f"{key.replace('_', ' ').title()}: {checks.get(key, 'Not reached')}"
                                     for key in self.CHECKS[self.stage]))
        pretty = lambda value: json.dumps(value, ensure_ascii=False, indent=2)
        if self.stage == 0:
            text = ("Actual ciphertext\n" + s.get("ciphertext", "Unavailable: invalid packet") +
                    "\n\nActual HMAC-SHA256 tag\n" + s.get("tag", "Unavailable") +
                    ("\nDisplay truncated to bounded capture limits." if s.get("tag_truncated") or s.get("ciphertext_truncated") else ""))
        elif self.stage == 1:
            text = checks.get("hmac", "Not reached") + "\n\nIntegrity is verified before any decryption."
            if s.get("failed_stage") == "hmac":
                text += "\nRejected before decryption"
            elif s.get("failed_stage") == "ciphertext_replay":
                text += "\nAuthenticated ciphertext replay rejected before decryption."
        elif self.stage == 2:
            text = "Transmitted order → restored order (four-token blocks)\n\n"
            text += "\n".join(f"{b['block']:02d}   {' '.join(b['transmitted'])}  →  {' '.join(b['restored'])}"
                              for b in s.get("blocks", [])) or "Not reached"
            if "token_count" in s:
                text += f"\n\nShowing up to 16 blocks of {s['token_count']} tokens."
        elif self.stage == 3:
            text = "Actual recovery after reversing coordinate shifts and ciphertext feedback.\nNo coordinates or matrix are displayed.\n\n"
            text += "\n".join(f"Position {sample['position']}: {sample['token']} → {sample['character']}"
                              for sample in s.get("samples", [])) or "No characters recovered"
        elif self.stage == 4:
            text = ("Recovered Base32\n" + s.get("base32", "Not reached") +
                    ("\n[Bounded sample]" if s.get("base32_truncated") else "") +
                    "\n\nActual decoded JSON envelope\n" + s.get("json_text", "Not reached"))
        else:
            text = "Actual validation results\n\n" + self.result.text()
            text += f"\n\nEnvelope sent_at: {s.get('sent_at', 'Not reached')}\nLaptop checked_at: {s.get('checked_at', 'Not reached')}"
            text += "\n\nAccepted application message\n" + (pretty(s["accepted_message"]) if "accepted_message" in s else "None · not delivered")
            text += "\n\nDelivery means emitted to existing GUI handlers; their sensor configuration and authentication rules still apply."
        if outcome == "Rejected":
            text += "\n\nRejection at " + s["failed_stage"] + ": " + s["error"]
        self.details.setPlainText(text)
