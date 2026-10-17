
"""Test the EDC algorithm without GPIO, ML, or laptop."""

import json
import secrets

from predict_movement_test import (
    EDCCodec,
    encrypt,
    decrypt,
    encrypt_authenticated,
    decrypt_authenticated,
)


def expect_reject(test_name, test_function):
    """Confirm that an invalid message is rejected."""
    try:
        test_function()
    except (ValueError, TypeError) as error:
        print(f"[PASS] {test_name}: {error}")
    else:
        raise AssertionError(f"[FAIL] {test_name}")


def main():
    print("=" * 60)
    print("EDC ENCRYPTION AND SECURITY TEST")
    print("=" * 60)

    # Generate temporary keys for local testing.
    edc_key = secrets.token_hex(16).upper()
    hmac_key = secrets.token_bytes(32)

    # TEST 1: Verify original EDC algorithm.
    print("\nTEST 1 - ORIGINAL EDC")

    plaintext = "HELLO123"
    ciphertext = encrypt(plaintext, "ECE4810")
    recovered = decrypt(ciphertext, "ECE4810")

    print("Plaintext:", plaintext)
    print("Ciphertext:", ciphertext)
    print("Decrypted:", recovered)

    expected = "CHE5-CIV3-MEC2-FIT5-FIT5-FIT1-CHE4-FIT6"
    assert ciphertext == expected
    assert recovered == plaintext
    print("[PASS] Original EDC algorithm")

    # TEST 2: Encrypt a simulated ultrasonic reading.
    print("\nTEST 2 - SENSOR ENCRYPTION")

    sensor_data = "SENSOR1DIST13CM"

    ciphertext, tag = encrypt_authenticated(
        sensor_data, edc_key, hmac_key
    )

    recovered = decrypt_authenticated(
        ciphertext, tag, edc_key, hmac_key
    )

    print("Original:", sensor_data)
    print("Encrypted:", ciphertext)
    print("HMAC:", tag)
    print("Decrypted:", recovered)

    assert recovered == sensor_data
    print("[PASS] Encryption and decryption")

    # TEST 3: Attempt to decrypt with incorrect HMAC key.
    print("\nTEST 3 - WRONG HMAC KEY")

    wrong_key = secrets.token_bytes(32)

    expect_reject(
        "Wrong HMAC key",
        lambda: decrypt_authenticated(
            ciphertext, tag, edc_key, wrong_key
        )
    )

    # TEST 4: Encrypt an actual GUI-style JSON message.
    print("\nTEST 4 - ENCRYPTED JSON PACKET")

    pi = EDCCodec(edc_key, hmac_key)
    laptop = EDCCodec(edc_key, hmac_key)

    original_message = {
        "type": "MOVEMENT_RESULT",
        "sensor_id": 5,
        "prediction": "AUTH_GESTURE",
        "confidence": 0.94
    }

    packet = pi.encode(original_message)

    print("Original JSON:", original_message)
    print("Encrypted packet:", json.dumps(packet, indent=2))

    assert "AUTH_GESTURE" not in json.dumps(packet)

    recovered_message = laptop.decode(packet)
    assert recovered_message == original_message

    print("Decrypted JSON:", recovered_message)
    print("[PASS] JSON encryption/decryption")

    # TEST 5: Tamper with the encrypted ciphertext.
    print("\nTEST 5 - CIPHERTEXT TAMPERING")

    tampered = packet.copy()
    first = tampered["ciphertext"][0]
    tampered["ciphertext"] = (
        ("X" if first != "X" else "Y")
        + tampered["ciphertext"][1:]
    )

    expect_reject(
        "Tampering detection",
        lambda: EDCCodec(edc_key, hmac_key).decode(tampered)
    )

    # TEST 6: Replay the same encrypted packet.
    print("\nTEST 6 - REPLAY ATTACK")

    expect_reject(
        "Replay attack",
        lambda: laptop.decode(packet)
    )

    # TEST 7: Verify common IoT messages.
    print("\nTEST 7 - SENSOR MESSAGE TYPES")

    messages = [
        {
            "type": "SENSOR_READING",
            "sensor_id": 1,
            "distance_cm": 13.2,
            "presence": True,
            "revision": 1
        },
        {
            "type": "ZONE_EVENT",
            "sensor_id": 2,
            "duration_seconds": 5.1,
            "revision": 1,
            "event_id": "demo"
        },
        {
            "type": "GESTURE_READING",
            "sensor_id": 5,
            "distance_cm": 9.1,
            "state": "RECORDING",
            "samples": 21,
            "total": 60
        },
        {
            "type": "MOVEMENT_RESULT",
            "sensor_id": 5,
            "prediction": "PASS_BY",
            "confidence": 0.91
        },
        {
            "type": "CONFIG_ACK",
            "revision": 1
        }
    ]

    receiver = EDCCodec(edc_key, hmac_key)

    for message in messages:
        encrypted = pi.encode(message)
        decrypted = receiver.decode(encrypted)

        assert decrypted == message
        print(f"[PASS] {message['type']}")

    print("\n" + "=" * 60)
    print("ALL EDC TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
