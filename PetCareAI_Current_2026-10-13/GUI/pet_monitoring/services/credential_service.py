"""Windows DPAPI encryption: bind saved tokens to the current Windows user.

On other platforms keep the token in memory only. Never fall back to plaintext.
"""
import base64
import ctypes
import os
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class CredentialService:
    def __init__(self, database):
        self.db = database
        self.session_token = ""

    @staticmethod
    def _crypt(data: bytes, encrypt: bool) -> bytes:
        buffer = ctypes.create_string_buffer(data)
        source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = _Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        function = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        function.restype = wintypes.BOOL
        result = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            free = ctypes.windll.kernel32.LocalFree
            free.argtypes = [ctypes.c_void_p]
            free.restype = ctypes.c_void_p
            free(target.pbData)

    def save(self, token: str) -> bool:
        self.session_token = token
        if os.name != "nt":
            return False
        try:
            encoded = base64.b64encode(self._crypt(token.encode(), True)).decode()
        except OSError:
            return False
        return self.db.set("telegram_token_dpapi", encoded)

    def load(self) -> str:
        if self.session_token:
            return self.session_token
        encoded = self.db.get("telegram_token_dpapi", "")
        if not encoded or os.name != "nt":
            return ""
        try:
            return self._crypt(base64.b64decode(encoded), False).decode()
        except (ValueError, OSError, UnicodeError):
            return ""

    def clear(self):
        self.session_token = ""
        self.db.set("telegram_token_dpapi", "")
