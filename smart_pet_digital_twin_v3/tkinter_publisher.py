"""Optional nonblocking bridge; owns no GPIO and does not change your Tk mainloop."""
import logging
import queue
import threading
from publish_sensor_readings import publish_readings


class TwinPublisher:
    """Latest-frame queue. submit() never waits on HTTP. Create one per GUI."""
    def __init__(self):
        self.frames = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def submit(self, d1, d2, d3, d4, d5):
        if self.stop_event.is_set():
            return
        try:
            self.frames.get_nowait()
        except queue.Empty:
            pass
        try:
            self.frames.put_nowait((d1, d2, d3, d4, d5))
        except queue.Full:
            pass

    def _run(self):
        while not self.stop_event.is_set():
            try:
                frame = self.frames.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                publish_readings(*frame)
            except Exception as exc:
                logging.warning('Digital twin publication failed: %s', exc)

    def close(self):
        # No blocking join from a Tkinter callback; HTTP has a 1.5 s timeout.
        self.stop_event.set()
