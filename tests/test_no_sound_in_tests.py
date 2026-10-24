"""The test suite never plays a sound on the user's speakers.

The user is often at the computer while the suite runs. A beep from a test
is confusing at best, and a replayed recording plays their own voice back.
"""
import logging
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


@pytest.fixture(scope="module")
def after_the_test():
    """Checks to make once the test asking for this is over and its fakes
    are put back: this fixture's own end comes after that test's."""
    checks = []
    yield checks.append
    for check in checks:
        check()


def test_a_beep_left_playing_as_a_test_ends_is_played_by_that_tests_fakes(
        monkeypatch, after_the_test):
    """The test ends while its beep is still being looked up on the sound
    thread; the beep is over before the test's fakes are put back, a fake
    wait among them."""
    looking_up = threading.Event()
    late_beep = []

    def slow_lookup(samplerate):
        looking_up.set()
        threading.Event().wait(0.3)
        return None, samplerate
    monkeypatch.setattr(beeps, "output_device", slow_lookup)
    monkeypatch.setattr(beeps, "_play", lambda *a: late_beep.append("played by the test's fake"))
    monkeypatch.setattr(beeps, "wait_until_played", lambda *a, **k: True)
    beeps.beep(True, *beeps.START_BEEP)
    assert looking_up.wait(5)

    def played_by_the_tests_fake():
        beeps.wait_until_played(limit=5)
        assert late_beep == ["played by the test's fake"]
    after_the_test(played_by_the_tests_fake)


def test_the_stand_in_player_plays_each_sound_silently_and_says_it_played(caplog):
    """Outside test_beeps every sound goes to conftest's player; a sound
    played through it is played, not failed."""
    caplog.set_level(logging.INFO)
    told = []
    beeps.play(np.zeros(100, np.float32), 44100, finished=told.append)
    beeps.beep(True, *beeps.START_BEEP)
    assert beeps.wait_until_played(limit=5)
    assert told == [None]
    assert not [r for r in caplog.records if r.getMessage().startswith("Could not play")]

