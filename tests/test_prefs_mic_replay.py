"""Preferences' microphone test plays the recording back to the last word.

sounddevice.play() lost about one output latency at the end of what it
played (issue #21), so a slow desktop cut off the end of the test
recording, and the user would blame their microphone. The replay goes
through the same player as the beeps, on the sound thread, and a failure
is shown in a dialog from GTK's own thread.
"""
from types import SimpleNamespace

import numpy as np

from tuxtalk import beeps, prefs
from tuxtalk.prefs import PreferencesWindow


def test_the_whole_recording_goes_to_the_player_louder(monkeypatch):
    asked = []
    monkeypatch.setattr(beeps, "play", lambda samples, rate, failed: asked.append((samples, rate)))
    recording = np.full((32000, 1), 0.1, np.float32)          # 2 s at 16 kHz, as recorded
    window = SimpleNamespace(recorded_audio=recording, _recording_sr=16000)
    PreferencesWindow.on_replay_recording(window, None)
    (samples, rate), = asked
    assert rate == 16000
    assert samples.shape == (32000,) and np.allclose(samples, 0.3)


def test_a_replay_that_fails_says_so_in_a_dialog_on_the_gtk_thread(monkeypatch):
    failures = []
    monkeypatch.setattr(beeps, "play", lambda samples, rate, failed: failures.append(failed))
    scheduled = []
    monkeypatch.setattr(prefs.GLib, "idle_add", lambda fn, *args: scheduled.append((fn, args)))
    shown = []
    window = SimpleNamespace(recorded_audio=np.zeros((100, 1), np.float32), _recording_sr=16000,
                             show_error_dialog=lambda title, text: shown.append((title, text)))
    window._replay_failed = lambda error: PreferencesWindow._replay_failed(window, error)
    PreferencesWindow.on_replay_recording(window, None)
    failures[0]("Device unavailable")              # on the sound thread
    assert shown == []
    fn, args = scheduled[0]
    fn(*args)                                      # what GTK's main loop runs
    assert shown == [("Playback failed!", "Device unavailable")]
