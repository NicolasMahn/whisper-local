import io
import wave

import numpy as np
import pytest

from whisper_local import recorder
from whisper_local.recorder import Recorder, to_wav


def test_to_wav_round_trips_mono_int16_samples():
    samples = np.array([0, 1, -1, 32767, -32768], dtype=np.int16)
    samplerate = 22050

    wav_bytes = to_wav(samples, samplerate)

    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == samplerate
        assert wav.getnframes() == len(samples)
        assert np.array_equal(
            np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2"), samples
        )


def test_stop_closes_stream_when_stream_stop_fails():
    class FailingStream:
        def __init__(self):
            self.closed = False

        def stop(self):
            raise RuntimeError("stop failed")

        def close(self):
            self.closed = True

    recorder = Recorder()
    stream = FailingStream()
    recorder._stream = stream
    recorder._chunks = [np.array([1], dtype=np.int16)]

    with pytest.raises(RuntimeError, match="stop failed"):
        recorder.stop()

    assert stream.closed
    assert recorder._stream is None
    assert recorder._chunks == []


def test_loudness_maps_rms_db_to_zero_one_range():
    angles = 2 * np.pi * np.arange(4096) / 4096
    silence = np.zeros(4096, dtype=np.int16)
    full_scale_sine = np.rint(32767 * np.sin(angles)).astype(np.int16)
    minus_35_db_rms = 32767 * 10 ** (-35 / 20)
    minus_35_db_sine = np.rint(
        np.sqrt(2) * minus_35_db_rms * np.sin(angles)
    ).astype(np.int16)

    assert recorder.loudness(silence) == 0.0
    assert recorder.loudness(full_scale_sine) == 1.0
    assert recorder.loudness(minus_35_db_sine) == pytest.approx(0.5, abs=0.05)
