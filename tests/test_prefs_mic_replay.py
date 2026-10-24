"""Preferences' microphone test plays the recording back to the last word.

sounddevice.play() lost about one output latency at the end of what it
played (issue #21), so a slow desktop cut off the end of the test
recording, and the user would blame their microphone. The replay goes
through the same player as the beeps, on the sound thread; Replay and
Start Recording are greyed out while it plays, and come back, with a
dialog if it failed, from GTK's own thread; or, behind a sound server that
never answers, once the replay would long have played.
"""
import numpy as np

from tuxtalk import beeps, mic_test
from tuxtalk.mic_test import MicTest


def _window(monkeypatch, recording, timeouts=None):
    """Preferences' microphone test with a test recording, and what reaches
    the player and GTK's main loop; each GLib timeout goes in *timeouts*, as
    (milliseconds, call, its arguments)."""
    asked, scheduled, shown = [], [], []
    timeouts = [] if timeouts is None else timeouts
    monkeypatch.setattr(beeps, "play", lambda samples, rate, finished: asked.append(
        (samples, rate, finished)))
    monkeypatch.setattr(mic_test.GLib, "idle_add", lambda fn, *args: scheduled.append((fn, args)))
    monkeypatch.setattr(mic_test.GLib, "timeout_add", lambda ms, fn, *args: timeouts.append(
        (ms, fn, args)))
    test = MicTest(lambda: None, lambda title, text: shown.append((title, text)))
    test.recorded_audio, test._rate = recording, 16000
    return test, asked, scheduled, shown


def _run_main_loop(scheduled):
    while scheduled:
        fn, args = scheduled.pop(0)
        fn(*args)


def test_the_whole_recording_goes_to_the_player_louder(monkeypatch):
    recording = np.full((32000, 1), 0.1, np.float32)          # 2 s at 16 kHz, as recorded
    mic, asked, _scheduled, _shown = _window(monkeypatch, recording)
    mic.replay()
    (samples, rate, _finished), = asked
    assert rate == 16000
    assert samples.shape == (32000,) and np.allclose(samples, 0.3)


def test_replay_is_greyed_out_until_the_recording_has_played(monkeypatch):
    """A second click would otherwise play the recording again after it."""
    mic, asked, scheduled, shown = _window(monkeypatch, np.zeros((100, 1), np.float32))
    mic.replay()
    assert mic.replay_btn.get_sensitive() is False
    finished = asked[0][2]
    finished(None)                                 # on the sound thread
    assert mic.replay_btn.get_sensitive() is False
    _run_main_loop(scheduled)
    assert mic.replay_btn.get_sensitive() is True
    assert shown == []


def test_no_new_recording_starts_while_a_replay_plays(monkeypatch):
    """Start Recording, then Stop, would bring Replay back while the replay
    still played, and the new recording would record it."""
    mic, asked, scheduled, _shown = _window(monkeypatch, np.zeros((100, 1), np.float32))
    mic.replay()
    assert mic.start_btn.get_sensitive() is False
    asked[0][2](None)
    _run_main_loop(scheduled)
    assert mic.start_btn.get_sensitive() is True


def test_a_replay_that_fails_says_so_in_a_dialog_on_the_gtk_thread(monkeypatch):
    mic, asked, scheduled, shown = _window(monkeypatch, np.zeros((100, 1), np.float32))
    mic.replay()
    asked[0][2]("Device unavailable")              # on the sound thread
    assert shown == []
    _run_main_loop(scheduled)
    assert shown == [("Playback failed!", "Device unavailable")]
    assert mic.replay_btn.get_sensitive() is True


def test_a_replay_the_sound_server_never_starts_gives_the_buttons_back(monkeypatch):
    """A sound server that hangs as the stream opens never says the replay
    is over; without a limit the whole microphone test stayed greyed out
    until Preferences was opened again."""
    timeouts = []
    mic, _asked, _scheduled, shown = _window(monkeypatch, np.zeros((32000, 1), np.float32),
                                                timeouts)
    mic.replay()
    (ms, call, args), = timeouts
    assert ms == (2 + beeps.WAIT_LIMIT_S + beeps.STUCK_S) * 1000   # 2 s of recording
    call(*args)
    assert mic.replay_btn.get_sensitive() is True and mic.start_btn.get_sensitive() is True
    assert shown == [("Playback failed!", "the sound output isn't answering")]


def test_the_limit_on_a_replay_that_played_changes_nothing_later(monkeypatch):
    """Its time runs out after the replay is over, when a new recording may
    have greyed Start Recording out again."""
    timeouts = []
    mic, asked, scheduled, shown = _window(monkeypatch, np.zeros((100, 1), np.float32),
                                              timeouts)
    mic.replay()
    asked[0][2](None)
    _run_main_loop(scheduled)
    mic.start_btn.set_sensitive(False)        # recording again
    (_ms, call, args), = timeouts
    call(*args)
    assert mic.start_btn.get_sensitive() is False
    assert shown == []
