from dataclasses import dataclass, field, asdict
import json


@dataclass
class SecurePacket:
    packet_id: str
    source_id: str
    timestamp: str
    ciphertext: str
    authentication_tag: str
    algorithm: str
    nonce: str
    version: int = 1
    verified: bool = field(default=False, repr=False)

    def authenticated_fields(self):
        # verified is a local result, never trusted from the wire.
        return {key: value for key, value in asdict(self).items()
                if key not in ("verified", "authentication_tag")}

    def to_wire(self) -> bytes:
        fields = self.authenticated_fields()
        fields["authentication_tag"] = self.authentication_tag
        return json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass
class SecurityEvent:
    event_type: str
    timestamp: str
    packet_id: str = ""
    source_id: str = ""
    verification_status: str = ""
    encryption_ms: float = 0
    verification_ms: float = 0
    decryption_ms: float = 0
    total_ms: float = 0


@dataclass
class PacketResult:
    accepted: bool
    reason: str
    data: dict | None = None
    packet: SecurePacket | None = None
    verification_ms: float = 0
    decryption_ms: float = 0
    total_ms: float = 0


@dataclass
class SecurityMetrics:
    total_packets: int = 0
    verified_packets: int = 0
    accepted_packets: int = 0
    rejected_packets: int = 0
    encrypted_packets: int = 0
    decrypted_packets: int = 0
    encryption_total_ms: float = 0
    verification_total_ms: float = 0
    decryption_total_ms: float = 0

    @property
    def success_rate(self):
        return self.verified_packets / self.total_packets * 100 if self.total_packets else 0

    @property
    def average_encryption_ms(self):
        return self.encryption_total_ms / self.encrypted_packets if self.encrypted_packets else 0

    @property
    def average_verification_ms(self):
        return self.verification_total_ms / self.total_packets if self.total_packets else 0

    @property
    def average_decryption_ms(self):
        return self.decryption_total_ms / self.decrypted_packets if self.decrypted_packets else 0
