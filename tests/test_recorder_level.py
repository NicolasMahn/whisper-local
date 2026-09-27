import numpy as np

from whisper_local.recorder import Recorder, loudness


def test_audio_callback_reports_each_chunk_and_keeps_its_samples():
    levels = []
    recorder = Recorder(on_level=levels.append)
    chunk = np.array([[1000], [-1000]], dtype=np.int16)

    recorder._collect(chunk, 2, None, None)
    chunk[:] = 0  # sounddevice reuses the callback buffer
    recorder._collect(chunk, 2, None, None)

    assert levels == [loudness(np.array([1000, -1000], dtype=np.int16)), 0.0]
    assert np.array_equal(recorder._chunks[0], [1000, -1000])
