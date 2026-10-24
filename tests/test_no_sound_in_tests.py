"""The test suite never plays a sound on the user's speakers.

The user is often at the computer while the suite runs. A beep from a test
is confusing at best, and a replayed recording plays their own voice back.
"""
import subprocess
import threading

import numpy as np
import pytest
import sounddevice

from tuxtalk import beeps


@pytest.mark.parametrize("stream", ["OutputStream", "RawOutputStream", "Stream", "RawStream"])
def test_no_stream_that_plays_can_open(stream):
    with pytest.raises(RuntimeError, match="must not play sound"):
        getattr(sounddevice, stream)(samplerate=44100, channels=1)


def test_sounddevice_play_is_refused():
    with pytest.raises(RuntimeError, match="must not play sound"):
        sounddevice.play(np.zeros(10, np.float32), 44100)


def test_no_sound_player_can_start():
    with pytest.raises(RuntimeError, match="must not play sound"):
        subprocess.run(["pw-play", "--version"], capture_output=True)


# The next two tests go together: the first leaves a beep on the sound
# thread as it ends, and the second checks where that beep went.
_late_beep = []


def test_a_beep_left_playing_at_the_end_of_a_test(monkeypatch):
    """Ends while its beep is still being looked up on the sound thread."""
    looking_up = threading.Event()

    def slow_lookup(samplerate):
        looking_up.set()
        threading.Event().wait(0.3)
        return None, samplerate
    monkeypatch.setattr(beeps, "output_device", slow_lookup)
    monkeypatch.setattr(beeps, "_play", lambda *a: _late_beep.append("played by the test's fake"))
    beeps.beep(True, *beeps.START_BEEP)
    assert looking_up.wait(5)


def test_is_played_by_that_tests_fakes_before_they_are_put_back():
    beeps.wait_until_played(5)
    assert _late_beep == ["played by the test's fake"]
