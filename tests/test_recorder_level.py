import numpy as np

from whisper_local.recorder import Recorder, is_silent, loudness, to_wav


def test_audio_callback_reports_each_chunk_and_keeps_its_samples():
    levels = []
    recorder = Recorder(on_level=levels.append)
    chunk = np.array([[1000], [-1000]], dtype=np.int16)

    recorder._collect(chunk, 2, None, None)
    chunk[:] = 0  # sounddevice reuses the callback buffer
    recorder._collect(chunk, 2, None, None)

    assert levels == [loudness(np.array([1000, -1000], dtype=np.int16)), 0.0]
    assert np.array_equal(recorder._chunks[0], [1000, -1000])


def test_room_noise_counts_as_silence_but_a_word_does_not():
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 40, 16000).astype(np.int16)  # about -58 dBFS
    word = noise.copy()
    word[8000:8800] = (3000 * np.sin(np.arange(800) / 5)).astype(np.int16)

    assert is_silent(to_wav(noise, 16000))
    assert not is_silent(to_wav(word, 16000))
