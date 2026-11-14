import io
import math
import wave
from collections.abc import Callable

import numpy as np
import sounddevice as sd


def to_wav(samples: np.ndarray, samplerate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(samplerate)
        wav.writeframes(samples.astype("<i2").tobytes())
    return buffer.getvalue()


def loudness(samples: np.ndarray) -> float:
    """Map int16 PCM RMS from -60..-10 dBFS to a 0..1 level."""
    if samples.size == 0:
        return 0.0
    scaled = np.asarray(samples, dtype=np.float64) / 32768.0
    rms = math.sqrt(float(np.mean(scaled * scaled)))
    if rms == 0:
        return 0.0
    return max(0.0, min(1.0, (20 * math.log10(rms) + 60) / 50))


def is_silent(wav: bytes, threshold: float = 0.3) -> bool:
    """True when no 20 ms window gets louder than about -45 dBFS.

    Whisper invents text for silence, often by repeating its prompt, so a
    recording without any sound worth hearing should not reach it at all.
    Room noise on a typical microphone stays near -55 dBFS; speech is far above.
    """
    with wave.open(io.BytesIO(wav)) as recording:
        window = max(1, recording.getframerate() // 50)
        samples = np.frombuffer(recording.readframes(recording.getnframes()), "<i2")
    return all(
        loudness(samples[start:start + window]) < threshold
        for start in range(0, samples.size, window)
    )


class Recorder:
    """Records the microphone between start() and stop().

    The stream is opened per recording rather than kept open, so the OS
    microphone indicator is only on while the user is dictating.
    """

    def __init__(
        self, samplerate: int = 16000, on_level: Callable[[float], None] | None = None
    ):
        self.samplerate = samplerate
        self.on_level = on_level
        self._stream = None
        self._chunks = []

    def start(self) -> None:
        self._chunks = []
        stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="int16",
            callback=self._collect,
        )
        try:
            stream.start()
        except BaseException:
            stream.close()
            raise
        self._stream = stream

    def stop(self) -> bytes:
        try:
            self._close()
            samples = np.concatenate(self._chunks) if self._chunks else np.empty(0, np.int16)
            return to_wav(samples, self.samplerate)
        finally:
            self._chunks = []

    def cancel(self) -> None:
        try:
            self._close()
        finally:
            self._chunks = []

    def _collect(self, indata, _frames, _time, _status):
        # sounddevice reuses its buffer, so keep a copy of each chunk.
        chunk = indata[:, 0].copy()
        self._chunks.append(chunk)
        if self.on_level is not None:
            self.on_level(loudness(chunk))

    def _close(self):
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
