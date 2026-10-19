"""Native OS credential storage, with no file or plaintext fallback."""
import sys


class CredentialStoreError(RuntimeError):
    pass


class AdminCredentialStore:
    SERVICE = "PetCareAI.Administrator.v1"
    ACCOUNT = "admin"

    def __init__(self):
        self._backend = None

    def _native(self):
        if self._backend is None:
            try:
                if sys.platform == "win32":
                    from keyring.backends.Windows import WinVaultKeyring
                    self._backend = WinVaultKeyring()
                elif sys.platform == "darwin":
                    from keyring.backends.macOS import Keyring
                    self._backend = Keyring()
                elif sys.platform.startswith("linux"):
                    from keyring.backends.SecretService import Keyring
                    self._backend = Keyring()
                else:
                    raise CredentialStoreError("Unsupported OS credential store")
            except Exception:
                raise CredentialStoreError("The OS credential store is unavailable") from None
        return self._backend

    def read(self):
        try:
            if sys.platform == "win32":
                from win32ctypes.pywin32 import win32cred, pywintypes
                try:
                    credential = win32cred.CredRead(self.SERVICE, win32cred.CRED_TYPE_GENERIC)
                except pywintypes.error as error:
                    if error.winerror == 1168:
                        return None
                    raise
                if credential["UserName"] != self.ACCOUNT:
                    raise CredentialStoreError("Unexpected credential owner")
                return credential["CredentialBlob"].decode("utf-16-le")
            return self._native().get_password(self.SERVICE, self.ACCOUNT)
        except Exception:
            raise CredentialStoreError("The OS credential store could not be read") from None

    def write(self, value):
        try:
            if sys.platform == "win32":
                from win32ctypes.pywin32 import win32cred
                # Direct replace: keyring's Windows multi-user compatibility
                # path keeps an old copy under a second target. Authentication
                # state must have exactly one authoritative record, no fallback.
                win32cred.CredWrite({"Type": win32cred.CRED_TYPE_GENERIC,
                                    "TargetName": self.SERVICE, "UserName": self.ACCOUNT,
                                    "CredentialBlob": value,
                                    "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE}, 0)
            else:
                self._native().set_password(self.SERVICE, self.ACCOUNT, value)
            if self.read() != value:
                raise CredentialStoreError("Credential verification failed")
        except Exception:
            raise CredentialStoreError("The OS credential store could not save credentials") from None
