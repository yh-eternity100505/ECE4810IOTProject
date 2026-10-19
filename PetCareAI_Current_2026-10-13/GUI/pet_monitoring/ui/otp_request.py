"""Keep Telegram requests off the Qt UI thread and ignore stale callbacks."""
import threading
from PySide6.QtCore import QObject, Signal


class OtpRequest(QObject):
    finished = Signal(int, bool, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.generation = 0
        self.busy = False

    def invalidate(self):
        self.generation += 1
        self.busy = False

    def start(self, operation):
        if self.busy:
            return
        self.generation += 1
        generation = self.generation
        self.busy = True

        def work():
            try:
                success, message = operation()
            except Exception:
                success, message = False, "Could not request a Telegram code. Try again."
            try:
                self.finished.emit(generation, success, message)
            except RuntimeError:
                # The widget may have been closed while delivery was in flight.
                pass

        threading.Thread(target=work, daemon=True, name="petcare-login-otp").start()
