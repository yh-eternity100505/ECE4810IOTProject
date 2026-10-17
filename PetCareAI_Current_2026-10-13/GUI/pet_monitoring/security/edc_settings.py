"""One-time Windows key setup for the existing Pi receiver."""
import json
import os
from pathlib import Path
import tempfile
from pet_monitoring.security.edc_transport import EDCCodec, load_json
from pet_monitoring.services.credential_service import CredentialService

KEY_FILE = Path(__file__).resolve().parents[2] / "data" / "edc_keys.dpapi"


def has_saved_keys():
    return KEY_FILE.exists()


def save_keys(edc_key, hmac_key_hex):
    codec = EDCCodec(edc_key, bytes.fromhex(hmac_key_hex))
    if os.name != "nt":
        raise ValueError("Saved EDC keys require Windows; use environment keys on other systems")
    raw = json.dumps({"edc_key": codec.edc_key, "hmac_key_hex": codec.hmac_key.hex()}).encode()
    protected = CredentialService._crypt(raw, True)
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=KEY_FILE.parent, prefix="edc-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(protected)
        temporary.replace(KEY_FILE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_codec():
    try:
        if os.name != "nt" or KEY_FILE.stat().st_size > 16384:
            raise ValueError()
        raw = CredentialService._crypt(KEY_FILE.read_bytes(), False)
        fields = load_json(raw)
        if not isinstance(fields, dict) or set(fields) != {"edc_key", "hmac_key_hex"}:
            raise ValueError()
        return EDCCodec(fields["edc_key"], bytes.fromhex(fields["hmac_key_hex"]))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("Cannot read saved EDC keys; run main.py --setup-edc again") from exc


def setup_keys():
    from getpass import getpass
    print("Enter the same keys used by your encrypted Pi sender. Input is hidden.")
    try:
        edc_key = getpass("EDC key: ")
        hmac_key_hex = getpass("HMAC key (64 hex characters): ")
        save_keys(edc_key, hmac_key_hex)
    except (ValueError, OSError):
        print("Keys could not be saved. Use an A-Z/0-9 EDC key and a 64-hex HMAC key on Windows.")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nEDC setup cancelled.")
        return 1
    print("Keys saved for your Windows account. Launch the GUI normally and start its receiver on 5005.")
    print("The Pi must use the matching keys and --edc --gui-port 5005.")
    return 0
