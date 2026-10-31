"""A recording at the microphone's own rate is resampled to the speech
engine's 16 kHz (recording.resample). Its length, worked out as it always
has been, is pinned: a helper shared with the beeps once came out a sample
longer for some sizes, which changes what the engine hears."""
import numpy as np
import pytest

from tuxtalk import recording


@pytest.mark.parametrize("samples, rate, length", [
    (3003, 48000, 1000),    # len / orig * target lands just under 1001
    (1009, 8000, 2017),
    (44100, 44100, 16000),
    (1, 48000, 0),
    (4801, 48000, 1600),
])
def test_a_recording_resampled_to_16_khz_keeps_its_length(samples, rate, length):
    audio = np.arange(samples, dtype=np.float32)
    assert len(recording.resample(audio, rate, 16000)) == length


def test_a_recording_at_16_khz_is_left_as_it_is():
    audio = np.ones(321, dtype=np.float32)
    assert recording.resample(audio, 16000, 16000) is audio
