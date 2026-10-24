"""Preferences' microphone test plays the recording back to the last word.

sounddevice.play() lost about one output latency at the end of what it
played (issue #21), so a slow desktop cut off the end of the test
recording, and the user would blame their microphone. The replay goes
through the same player as the beeps, on the sound thread; Replay and
Start Recording are greyed out while it plays, and come back, with a
dialog if it failed, from GTK's own thread; or, behind a sound server that
never answers, once the player itself would have given up, and the replay
is then called off.
"""
import time

import numpy as np
import pytest

from tuxtalk import beeps, mic_test
from tuxtalk.mic_test import MicTest


def _window(monkeypatch, recording, timeouts=None):
    """Preferences' microphone test with a test recording, and what reaches
    the player and GTK's main loop; each GLib timeout goes in *timeouts*, as
    (milliseconds, call, its arguments)."""
    asked, scheduled, shown = [], [], []
    timeouts = [] if timeouts is None else timeouts
    monkeypatch.setattr(beeps, "play", lambda samples, rate, finished, called_off: asked.append(
        (samples, rate, finished, called_off)))
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
    (samples, rate, _finished, _called_off), = asked
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
    (_ms, call, args), = timeouts
    call(*args)
    assert mic.replay_btn.get_sensitive() is True and mic.start_btn.get_sensitive() is True
    assert shown == [("Playback failed!", "the sound output isn't answering")]


def test_the_limit_on_a_replay_lets_the_player_give_up_first(monkeypatch):
    """The player gives up on an output that takes no samples for the sound's
    length, its latency and STUCK_S, and then still waits up to MAX_AHEAD_S
    for what it handed over to be heard. Preferences giving up before that
    said "Playback failed!" while the replay played."""
    timeouts = []
    mic, _asked, _scheduled, _shown = _window(monkeypatch, np.zeros((32000, 1), np.float32),
                                              timeouts)
    mic.replay()
    (ms, _call, _args), = timeouts
    slowest_latency = 0.557                     # of the outputs test_beeps plays through
    player_done = 2 + slowest_latency + beeps.STUCK_S + beeps.MAX_AHEAD_S   # 2 s recording
    assert player_done < ms / 1000 < player_done + 2


def test_a_replay_given_up_on_never_plays_later(monkeypatch):
    """Behind a stuck output it stayed queued once its buttons came back, and
    played the user's voice whenever the output came back: after "Playback
    failed!", or into the next test recording."""
    timeouts = []
    mic, asked, _scheduled, _shown = _window(monkeypatch, np.zeros((32000, 1), np.float32),
                                             timeouts)
    mic.replay()
    called_off = asked[0][3]
    assert not called_off.is_set()
    (_ms, call, args), = timeouts
    call(*args)
    assert called_off.is_set()


class Microphone:
    def __init__(self, **settings):
        pass

    def start(self):
        pass


@pytest.mark.parametrize("ending", ["a new recording starts", "the window closes"])
def test_a_replay_is_called_off_when(monkeypatch, ending):
    import sounddevice
    mic, asked, _scheduled, _shown = _window(monkeypatch, np.zeros((100, 1), np.float32))
    monkeypatch.setattr(mic_test.mic_stream, "input_samplerate", lambda device, rate: rate)
    monkeypatch.setattr(sounddevice, "InputStream", Microphone)
    mic.replay()
    if ending == "a new recording starts":
        mic.start()
    else:
        mic.close()
    assert asked[0][3].is_set()


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


def test_a_microphone_that_wont_start_is_freed_and_says_so(monkeypatch):
    """A stream that opened but wouldn't start kept the microphone, and the
    test went on as though it recorded."""
    import sounddevice
    opened = []

    class WontStart:
        def __init__(self, **settings):
            self.closed = False
            opened.append(self)

        def start(self):
            raise OSError("Device unavailable")

        def stop(self):
            pass

        def close(self):
            self.closed = True
    mic, _asked, _scheduled, shown = _window(monkeypatch, None)
    monkeypatch.setattr(mic_test.mic_stream, "input_samplerate", lambda device, rate: rate)
    monkeypatch.setattr(sounddevice, "InputStream", WontStart)
    mic.start()
    assert shown == [("Recording failed!", "Device unavailable")]
    assert not mic.recording and mic.start_btn.get_sensitive()
    deadline = time.monotonic() + 2
    while not opened[0].closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert opened[0].closed


def test_a_screen_reader_names_the_microphone_controls_by_their_labels(prefs_window):
    """The microphone dropdown, the volume slider and the level meter sat
    beside their labels with nothing linking them, and the buttons were
    read out with their symbols."""
    import gtk_text
    from gi.repository import Gtk
    window = prefs_window()
    labels = {w.get_text(): w for w in gtk_text.widgets(window.window) if isinstance(w, Gtk.Label)}
    assert isinstance(labels["Microphone:"].get_mnemonic_widget(), Gtk.ComboBoxText)
    assert labels["Input Volume:"].get_mnemonic_widget() is window.volume_scale
    assert labels["Input Level:"].get_mnemonic_widget() is window.mic_test.meter
    assert [b.get_accessible().get_name() for b in (
        window.mic_test.start_btn, window.mic_test.stop_btn, window.mic_test.replay_btn)] == [
        "Start Recording", "Stop Recording", "Replay"]
