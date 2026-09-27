import sys
import threading

import numpy as np
import sounddevice as sd

SAMPLERATE = 44100
VOLUME = 0.15

# (frequency in Hz, duration in seconds) for each note of a cue. Rising means
# "listening", falling means "done", two low notes mean "something went wrong".
CUES = {
    "start": [(660, 0.06), (880, 0.06)],
    "stop": [(880, 0.06), (660, 0.06)],
    "error": [(220, 0.12), (0, 0.05), (220, 0.12)],
}


def _tone(frequency: float, seconds: float) -> np.ndarray:
    t = np.arange(int(SAMPLERATE * seconds)) / SAMPLERATE
    # A short fade in and out avoids audible clicks at the edges.
    envelope = np.minimum(1, np.minimum(t, seconds - t) / 0.005)
    return (VOLUME * envelope * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def _synthesise(name: str) -> np.ndarray:
    return np.concatenate([_tone(frequency, seconds) for frequency, seconds in CUES[name]])


_SOUNDS = {name: _synthesise(name) for name in CUES}
# sd.play() keeps one global stream, and cues come from both the hotkey and
# the worker thread.
_lock = threading.Lock()


def play(name: str) -> None:
    try:
        with _lock:
            sd.play(_SOUNDS[name], SAMPLERATE)
    except Exception as error:
        # A missing or busy audio device must never break dictation.
        print(f"whisper-local: cannot play sound: {error}", file=sys.stderr)
