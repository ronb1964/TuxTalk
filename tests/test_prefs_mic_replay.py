"""Preferences' microphone test plays the recording back to the last word.

sounddevice.play() lost about one output latency at the end of what it
played (issue #21), so a slow desktop cut off the end of the test
recording, and the user would blame their microphone. The replay goes
through the same player as the beeps, on the sound thread; Replay is
greyed out while it plays, and comes back, with a dialog if it failed,
from GTK's own thread.
"""
from types import SimpleNamespace

import numpy as np

from tuxtalk import beeps, prefs
from tuxtalk.prefs import PreferencesWindow


class Button:
    def __init__(self):
        self.sensitive = True

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive


def _window(monkeypatch, recording):
    """A Preferences window with a test recording, and what reaches the
    player and GTK's main loop."""
    asked, scheduled, shown = [], [], []
    monkeypatch.setattr(beeps, "play", lambda samples, rate, finished: asked.append(
        (samples, rate, finished)))
    monkeypatch.setattr(prefs.GLib, "idle_add", lambda fn, *args: scheduled.append((fn, args)))
    window = SimpleNamespace(recorded_audio=recording, _recording_sr=16000, recording=False,
                             replay_btn=Button(),
                             show_error_dialog=lambda title, text: shown.append((title, text)))
    window._replay_over = lambda error: PreferencesWindow._replay_over(window, error)
    return window, asked, scheduled, shown


def _run_main_loop(scheduled):
    while scheduled:
        fn, args = scheduled.pop(0)
        fn(*args)


def test_the_whole_recording_goes_to_the_player_louder(monkeypatch):
    recording = np.full((32000, 1), 0.1, np.float32)          # 2 s at 16 kHz, as recorded
    window, asked, _scheduled, _shown = _window(monkeypatch, recording)
    PreferencesWindow.on_replay_recording(window, None)
    (samples, rate, _finished), = asked
    assert rate == 16000
    assert samples.shape == (32000,) and np.allclose(samples, 0.3)


def test_replay_is_greyed_out_until_the_recording_has_played(monkeypatch):
    """A second click would otherwise play the recording again after it."""
    window, asked, scheduled, shown = _window(monkeypatch, np.zeros((100, 1), np.float32))
    PreferencesWindow.on_replay_recording(window, None)
    assert window.replay_btn.sensitive is False
    finished = asked[0][2]
    finished(None)                                 # on the sound thread
    assert window.replay_btn.sensitive is False
    _run_main_loop(scheduled)
    assert window.replay_btn.sensitive is True
    assert shown == []


def test_replay_stays_greyed_out_when_a_new_recording_started_meanwhile(monkeypatch):
    window, asked, scheduled, _shown = _window(monkeypatch, np.zeros((100, 1), np.float32))
    PreferencesWindow.on_replay_recording(window, None)
    window.recording = True
    asked[0][2](None)
    _run_main_loop(scheduled)
    assert window.replay_btn.sensitive is False


def test_a_replay_that_fails_says_so_in_a_dialog_on_the_gtk_thread(monkeypatch):
    window, asked, scheduled, shown = _window(monkeypatch, np.zeros((100, 1), np.float32))
    PreferencesWindow.on_replay_recording(window, None)
    asked[0][2]("Device unavailable")              # on the sound thread
    assert shown == []
    _run_main_loop(scheduled)
    assert shown == [("Playback failed!", "Device unavailable")]
    assert window.replay_btn.sensitive is True
