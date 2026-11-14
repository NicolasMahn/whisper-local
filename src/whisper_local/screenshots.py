"""Match screenshots, which arrive from GNOME Shell, to the recordings they belong to."""

import threading
import time
from concurrent.futures import Future


class PendingScreenshot:
    """A screenshot on its way. Waiting is counted from when it was asked for."""

    def __init__(self, patience: float):
        self._future: Future[bytes] = Future()
        self._deadline = time.monotonic() + patience

    def deliver(self, png: bytes) -> None:
        self._future.set_result(png)

    def wait(self) -> bytes | None:
        try:
            remaining = max(0, self._deadline - time.monotonic())
            return self._future.result(timeout=remaining) or None
        except TimeoutError:
            return None


class Screenshots:
    """Screenshots that were asked for and have not arrived.

    Recordings can overlap with the transcription of earlier ones, so each
    request has its own id. Without the extension nothing ever arrives, hence
    the short memory.
    """

    def __init__(self, patience: float = 1.5, remembered: int = 8):
        self._patience = patience
        self._remembered = remembered
        self._lock = threading.Lock()
        self._pending: dict[int, PendingScreenshot] = {}
        self._next_id = 1

    def ask(self) -> tuple[int, PendingScreenshot]:
        pending = PendingScreenshot(self._patience)
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            self._pending[request_id] = pending
            while len(self._pending) > self._remembered:
                del self._pending[next(iter(self._pending))]
        return request_id, pending

    def deliver(self, request_id: int, png: bytes) -> None:
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if pending is not None:
            pending.deliver(png)
