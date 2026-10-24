"""The test suite never plays a sound on the user's speakers.

The user is often at the computer while the suite runs. A beep from a test
is confusing at best, and a replayed recording plays their own voice back.
"""
import shutil
import subprocess
import sys
import threading
import wave

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


# Opens the default output and every one it lists, and closes each without
# starting it, so even an output this reaches stays silent.
_OPEN_EVERY_OUTPUT = """
import sounddevice
outputs = [None] + [d["index"] for d in sounddevice.query_devices() if d["max_output_channels"]]
for device in outputs:
    try:
        sounddevice.OutputStream(samplerate=44100, channels=1, device=device).close()
    except Exception:
        continue
    print("opened", device)
"""


def test_a_python_child_finds_no_output_to_play_on():
    """A program a test starts doesn't go through this process's guard."""
    ran = subprocess.run([sys.executable, "-c", _OPEN_EVERY_OUTPUT],
                         capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == ""


@pytest.mark.parametrize("player", ["paplay --volume=0", "pw-play --volume=0", "aplay -q"])
def test_a_player_a_shell_starts_reaches_no_sound_server(player, tmp_path):
    """The name check above sees only "bash"; the player still can't play.
    What it is given is silence, so even a player this misses is heard by
    nobody."""
    if shutil.which(player.split()[0]) is None:
        pytest.skip(f"{player.split()[0]} isn't installed")
    silence = tmp_path / "silence.wav"
    with wave.open(str(silence), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(44100)
        out.writeframes(bytes(4410 * 2))
    ran = subprocess.run(["bash", "-c", f"{player} {silence}"],
                         capture_output=True, text=True, timeout=30)
    assert ran.returncode != 0, f"{player} played: {ran.stderr}"


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
