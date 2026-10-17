"""Simulation cipher adapters. Live Raspberry Pi EDC uses edc_transport.py."""
from typing import Protocol, Callable
import os


class CipherBackend(Protocol):
    algorithm: str
    def encrypt(self, plaintext: bytes, nonce: bytes) -> bytes: ...
    def decrypt(self, ciphertext: bytes, nonce: bytes) -> bytes: ...


class DemoCipherBackend:
    """A labelled integration/demo backend, never presented as EDC.

    AES-CTR from cryptography performs actual encryption. Separate HMAC protects
    the ciphertext and metadata. The random AES key is memory-only for this run.
    """
    algorithm = "Demo AES-CTR + HMAC-SHA256 (EDC not connected)"

    def __init__(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        self._Cipher, self._AES, self._CTR = Cipher, algorithms.AES, modes.CTR
        self._key = os.urandom(32)

    def encrypt(self, plaintext, nonce):
        encryptor = self._Cipher(self._AES(self._key), self._CTR(nonce)).encryptor()
        return encryptor.update(plaintext) + encryptor.finalize()

    def decrypt(self, ciphertext, nonce):
        decryptor = self._Cipher(self._AES(self._key), self._CTR(nonce)).decryptor()
        return decryptor.update(ciphertext) + decryptor.finalize()


class EDCAdapter:
    """Wrap your original EDC functions without changing GUI or packet handling.

    Inject callables (data_bytes, nonce_bytes) -> bytes. Keep EDC keys inside your
    backend/key manager, not inside settings or the packet. If your cipher uses
    its own IV, encode it into ciphertext or consume the authenticated nonce.
    """
    algorithm = "EDC + HMAC-SHA256"

    def __init__(self, encrypt: Callable, decrypt: Callable):
        self._encrypt, self._decrypt = encrypt, decrypt

    def encrypt(self, plaintext, nonce):
        return self._encrypt(plaintext, nonce)

    def decrypt(self, ciphertext, nonce):
        return self._decrypt(ciphertext, nonce)
