"""Hands-free and Type at pause judge the microphone's audio in fixed 20 ms
windows, whatever size of block it sends (audio_windows.split_windows): the
samples left over from one block go in front of the next, none is lost or
heard twice, and nothing kept still points into a buffer the audio system
reuses."""
import numpy as np
import pytest

from tuxtalk.audio_windows import split_windows, window_length


@pytest.mark.parametrize("rate, samples", [(16000, 320), (44100, 882), (48000, 960), (40, 1)])
def test_a_window_is_20_ms_at_any_rate(rate, samples):
    assert window_length(rate) == samples


def test_each_sample_lands_in_one_window_in_order_and_the_rest_waits():
    """Blocks of 7 into windows of 4: the windows hold the samples in the
    order they came, and what is left over starts the next window."""
    audio = np.arange(30, dtype=np.int16)
    rest = np.zeros(0, dtype=np.int16)
    heard = []
    for start in range(0, audio.size, 7):
        windows, rest = split_windows(rest, audio[start:start + 7], 4)
        assert windows.shape[1:] == (4,) and rest.size < 4
        heard.extend(windows.ravel().tolist())
    assert heard == list(range(28)) and rest.tolist() == [28, 29]


def test_a_block_shorter_than_a_window_is_kept_whole_for_the_next():
    windows, rest = split_windows(np.array([1, 2], np.int16), np.array([3], np.int16), 4)
    assert windows.shape == (0, 4) and rest.tolist() == [1, 2, 3]
    assert rest.dtype == np.int16


def test_scribbling_over_the_microphone_buffer_changes_nothing_kept():
    """sounddevice reuses a block's buffer once the callback returns: the
    windows and the leftover are the samples as heard, and the leftover is
    an array of its own, holding no more than itself."""
    buffer = np.arange(10, dtype=np.int16)
    windows, rest = split_windows(np.zeros(0, np.int16), buffer, 4)
    buffer[:] = -1
    assert windows.tolist() == [[0, 1, 2, 3], [4, 5, 6, 7]] and rest.tolist() == [8, 9]
    assert rest.base is None and not np.shares_memory(windows, buffer)


def test_the_high_passed_audio_keeps_its_float_samples():
    """Type at pause windows its high-passed audio, which is float."""
    windows, rest = split_windows(np.array([0.5]), np.array([1.25, -2.0, 3.5]), 2)
    assert windows.tolist() == [[0.5, 1.25], [-2.0, 3.5]] and rest.size == 0
