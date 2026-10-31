"""Audio is resampled linearly (resampling.resample): a recording at the
microphone's own rate to the speech engine's 16 kHz, and the mic test's
replay to the speakers' rate. Its length is the exact one, rounded down:
len / rate * 16000 as a float can land just under a whole number (3,003
samples at 48 kHz is 1000.999...), and what the engine hears would then
hang on rounding error."""
import numpy as np
import pytest

from tuxtalk.resampling import resample


@pytest.mark.parametrize("samples, rate, length", [
    (3003, 48000, 1001),    # exactly 1001; len / 48000 * 16000 is 1000.999...
    (3009, 48000, 1003),
    (1009, 8000, 2018),     # the same upsampled from 8 kHz: 2017.999...
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
    (9, 48000, 27),         # len / 16000 * 48000 is 26.999...
    (5600, 44100, 15435),   # and 15434.999... here
    (999, 44100, 2753),     # 2753.49: rounded down
])
def test_the_mic_tests_replay_resampled_for_the_speakers_has_the_exact_length(samples, rate, length):
    audio = np.zeros(samples, dtype=np.float32)
    assert len(resample(audio, 16000, rate)) == length


def test_the_samples_between_are_interpolated():
    """Doubling the rate of 0, 1, 0, -1, 0: ten samples at steps of 4/9 of
    the original's, each on the straight line between its two neighbours."""
    out = resample(np.array([0, 1, 0, -1, 0], dtype=np.float32), 8000, 16000)
    expected = np.array([0, 4, 8, 6, 2, -2, -6, -8, -4, 0]) / 9
    np.testing.assert_allclose(out, expected, atol=1e-6)


def test_resampling_keeps_the_ends_and_the_sample_type():
    audio = np.linspace(-1.0, 1.0, 4801, dtype=np.float32)
    out = resample(audio, 48000, 16000)
    assert out.dtype == np.float32
    assert out[0] == audio[0] and out[-1] == audio[-1]


def test_audio_already_at_the_rate_is_left_as_it_is():
    audio = np.ones(321, dtype=np.float32)
    assert resample(audio, 16000, 16000) is audio
