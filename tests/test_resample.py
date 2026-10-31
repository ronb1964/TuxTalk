"""Audio is resampled linearly (resampling.resample): a recording at the
microphone's own rate to the speech engine's 16 kHz, and a beep or the mic
test's replay to the speakers' rate. Its length is the exact one, rounded
down: worked out in floating point it came out a sample short for some sizes
(3,003 samples at 48 kHz gave 1,000), so what the engine heard depended on
rounding error and the beeps were cut differently from the recordings."""
import numpy as np
import pytest

from tuxtalk.resampling import resample


@pytest.mark.parametrize("samples, rate, length", [
    (3003, 48000, 1001),    # exactly 1001, where len / 48000 * 16000 fell short
    (3009, 48000, 1003),
    (1009, 8000, 2018),     # the same, upsampled from 8 kHz
    (6006, 96000, 1001),
    (4801, 48000, 1600),    # 1600.33: rounded down
    (22051, 44100, 8000),
    (44100, 44100, 16000),
    (1, 48000, 0),
])
def test_a_recording_resampled_to_16_khz_has_the_exact_length(samples, rate, length):
    audio = np.arange(samples, dtype=np.float32)
    assert len(resample(audio, rate, 16000)) == length


@pytest.mark.parametrize("samples, rate, length", [
    (9, 48000, 27),         # where len / 16000 * 48000 would give 26
    (5600, 44100, 15435),   # and 15,434
    (999, 44100, 2753),     # 2753.49: rounded down
])
def test_a_sound_resampled_for_the_speakers_has_the_exact_length(samples, rate, length):
    audio = np.zeros(samples, dtype=np.float32)
    assert len(resample(audio, 16000, rate)) == length


def test_resampling_keeps_the_ends_and_the_sample_type():
    audio = np.linspace(-1.0, 1.0, 4801, dtype=np.float32)
    out = resample(audio, 48000, 16000)
    assert out.dtype == np.float32
    assert out[0] == audio[0] and out[-1] == audio[-1]


def test_audio_already_at_the_rate_is_left_as_it_is():
    audio = np.ones(321, dtype=np.float32)
    assert resample(audio, 16000, 16000) is audio
