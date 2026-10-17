
"""Temporary laptop receiver for testing Pi EDC packets."""

import json
import socket

from edc_codec import EDCCodec


HOST = "0.0.0.0"
PORT = 5006


def send_json(connection, message):
    """Send one newline-delimited JSON packet."""
    data = json.dumps(message) + "\n"
    connection.sendall(data.encode("utf-8"))


def main():
    codec = EDCCodec.from_environment()

    # The Pi needs a configuration before sending zone data.
    configuration = {
        "type": "SET_CONFIG",
        "revision": 1,
        "mapping": {
            "1": "eat",
            "2": "sleep",
            "3": "water",
            "4": "toilet"
        },
        "thresholds_cm": {
            "1": 20,
            "2": 20,
            "3": 20,
            "4": 20
        },
        "gesture_sensor": 5
    }

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )
        server.bind((HOST, PORT))
        server.listen(1)

        print(f"[LISTENING] TCP port {PORT}")

        while True:
            connection, address = server.accept()

            with connection:
                print(f"\n[CONNECTED] {address}")

                # Send encrypted configuration to the Pi.
                send_json(connection, codec.encode(configuration))

                buffer = b""

                while True:
                    data = connection.recv(4096)

                    if not data:
                        print("[DISCONNECTED]")
                        break

                    buffer += data

                    if len(buffer) > 131072:
                        raise ValueError("Packet too large")

                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)

                        try:
                            packet = json.loads(line)

                            print("\n" + "=" * 50)
                            print("RECEIVED ENCRYPTED PACKET")
                            print("=" * 50)

                            print("Type:", packet.get("type"))
                            print(
                                "Ciphertext:",
                                packet.get("ciphertext", "")[:80],
                                "..."
                            )
                            print("HMAC:", packet.get("tag"))

                            # Authenticate and decrypt.
                            message = codec.decode(packet)

                            print("\n[HMAC VERIFIED]")
                            print("[DECRYPTION SUCCESSFUL]")
                            print(json.dumps(message, indent=2))

                        except (ValueError, TypeError, KeyError) as error:
                            print("[PACKET REJECTED]", error)


if __name__ == "__main__":
    main()
