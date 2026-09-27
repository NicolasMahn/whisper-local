import queue
import sys
import threading
import time
from collections.abc import Callable


def _print_error(error: Exception) -> None:
    print(f"whisper-local: {type(error).__name__}", file=sys.stderr)


class Dictation:
    """Turns dictation-key events into recordings and inserted transcripts.

    Satisfies platform.HotkeyHandler. Hotkey callbacks return immediately;
    transcription runs through `submit`, which by default is a single daemon
    worker: transcripts are inserted in the order they were spoken, and
    pending ones die with the process instead of pasting after quitting.

    `on_state` hears "listening", "hands_free", "transcribing" or "idle"
    whenever that changes. `on_error` hears every failure. Both may be called
    from any thread, so a UI must hand them over to its own main loop.
    """

    def __init__(
        self,
        recorder,
        transcribe: Callable[[bytes], str],
        insert_text: Callable[[str], None],
        cue: Callable[[str], None] = lambda name: None,
        submit: Callable[[Callable[[], None]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        min_seconds: float = 0.3,
        on_state: Callable[[str], None] = lambda state: None,
        on_error: Callable[[Exception], None] = _print_error,
        hands_free_key: str = "",
    ):
        self._recorder = recorder
        self._transcribe = transcribe
        self._insert_text = insert_text
        self._cue = cue
        self._submit = submit or _start_worker()
        self._clock = clock
        self._min_seconds = min_seconds
        self._dedicated_hands_free = bool(hands_free_key)
        self._on_state = on_state
        self._on_error = on_error
        self.enabled = True
        self._started_at: float | None = None
        self._hands_free = False
        self._key_down = False
        self._hands_free_key_down = False
        self._last_tap_at: float | None = None
        # Pasting with a trigger modifier down would change the shortcut.
        self._key_up = threading.Event()
        self._key_up.set()
        # Jobs finish on the worker while the hotkey thread starts recordings,
        # so the state is derived and reported under one lock to stay ordered.
        self._state_lock = threading.Lock()
        self._pending = 0
        self._state = "idle"

    def pressed(self) -> None:
        if self._key_down:
            return
        self._key_down = True
        self._key_up.clear()
        if self._hands_free:
            if not self._dedicated_hands_free:
                self._finish_recording()
            return
        if not self.enabled:
            self._last_tap_at = None
            return
        now = self._clock()
        hands_free = (
            not self._dedicated_hands_free
            and self._last_tap_at is not None
            and 0 <= now - self._last_tap_at <= 0.4
        )
        self._last_tap_at = None
        self._start_recording(now, hands_free)

    def hands_free_pressed(self) -> None:
        if not self._dedicated_hands_free or self._hands_free_key_down:
            return
        self._hands_free_key_down = True
        self._key_up.clear()
        if self._key_down and not self._hands_free:
            self.interrupted()
            return
        if self._hands_free:
            self._finish_recording()
        elif self.enabled:
            self._start_recording(self._clock(), True)

    def hands_free_released(self) -> None:
        if not self._hands_free_key_down:
            return
        self._hands_free_key_down = False
        if not self._key_down:
            self._key_up.set()

    def _start_recording(self, now: float, hands_free: bool) -> None:
        try:
            self._recorder.start()
        except Exception as error:
            self._fail(error)
            return
        self._started_at = now
        self._hands_free = hands_free
        self._report_state()
        self._cue("start")

    def interrupted(self) -> None:
        if self._started_at is None or self._hands_free:
            return
        self._cancel_recording()

    def escaped(self) -> None:
        self._last_tap_at = None
        if self._started_at is not None:
            self._cancel_recording()

    def _cancel_recording(self) -> None:
        try:
            self._recorder.cancel()
        except Exception as error:
            self._stop_recording()
            self._fail(error)
            return
        self._stop_recording()

    def released(self) -> None:
        if not self._key_down:
            return
        self._key_down = False
        if not self._hands_free_key_down:
            self._key_up.set()
        if self._started_at is None or self._hands_free:
            return
        held = self._clock() - self._started_at
        try:
            wav = self._recorder.stop()
        except Exception as error:
            self._stop_recording()
            self._fail(error)
            return
        # Accidental taps would otherwise send near-silence to the server.
        if held < self._min_seconds:
            self._stop_recording()
            if not self._dedicated_hands_free:
                self._last_tap_at = self._clock()
            return
        self._stop_recording(pending=1)
        self._cue("stop")
        self._submit(lambda: self._transcribe_and_insert(wav))

    def _finish_recording(self) -> None:
        try:
            wav = self._recorder.stop()
        except Exception as error:
            self._stop_recording()
            self._fail(error)
            return
        self._stop_recording(pending=1)
        self._cue("stop")
        self._submit(lambda: self._transcribe_and_insert(wav))

    def _stop_recording(self, pending: int = 0) -> None:
        self._started_at = None
        self._hands_free = False
        self._report_state(pending)

    def _transcribe_and_insert(self, wav: bytes) -> None:
        try:
            text = self._transcribe(wav)
            if text:
                self._key_up.wait()
                self._insert_text(text)
        except Exception as error:
            self._fail(error)
        finally:
            self._report_state(-1)

    def _report_state(self, pending_change: int = 0) -> None:
        with self._state_lock:
            self._pending += pending_change
            if self._started_at is not None:
                state = "hands_free" if self._hands_free else "listening"
            elif self._pending:
                state = "transcribing"
            else:
                state = "idle"
            if state != self._state:
                self._state = state
                self._on_state(state)

    def _fail(self, error: Exception) -> None:
        self._on_error(error)
        self._cue("error")


def _start_worker() -> Callable[[Callable[[], None]], None]:
    jobs: queue.Queue[Callable[[], None]] = queue.Queue()

    def work() -> None:
        while True:
            jobs.get()()

    threading.Thread(target=work, daemon=True).start()
    return jobs.put
