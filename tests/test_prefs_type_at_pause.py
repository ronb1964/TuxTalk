"""Preferences → Advanced → TuxTalk Labs → While you talk: the choice
between live text and Type at pause, Type at pause's fine-tuning, and its
live test (with a fake microphone stream)."""
import threading
import time
from types import SimpleNamespace

import gi
import numpy as np
import pytest

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

import gtk_text  # noqa: E402
from tuxtalk import mic_stream, prefs_type_at_pause, voice_detector  # noqa: E402
from tuxtalk.mic_test import MicTest  # noqa: E402
from tuxtalk.prefs import PreferencesWindow  # noqa: E402
from tuxtalk.prefs_advanced import AdvancedTab  # noqa: E402
from tuxtalk.prefs_audio import AudioTab  # noqa: E402
from tuxtalk.prefs_type_at_pause import TypeAtPause  # noqa: E402


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

    def stop(self, ignore_errors=True):
        self.stopped = True

    def close(self, ignore_errors=True):
        self.closed = True


@pytest.fixture
def win(monkeypatch):
    """The Labs entry, in a PreferencesWindow with only what it needs."""
    monkeypatch.setattr(mic_stream, "input_samplerate", lambda idx, rate: 16000)
    w = PreferencesWindow.__new__(PreferencesWindow)
    w.config = {"chunked_dictation": False, "live_text": False, "chunk_pause_seconds": 0.8,
                "chunk_context_marks": True, "mic": ""}
    w.updates = []

    def update_config(key, value):
        w.config[key] = value
        w.updates.append((key, value))

    w.update_config = update_config
    w.get_selected_device_idx = lambda: 3
    w.audio_tab = AudioTab(w)
    w.audio_tab.mic_test = MicTest(lambda: 3, lambda title, text: None)
    tap = TypeAtPause(w)
    w.advanced_tab = AdvancedTab(w)
    w.advanced_tab.type_at_pause = tap
    w._tabs = [(w.audio_tab, Gtk.ScrolledWindow()), (w.advanced_tab, Gtk.ScrolledWindow())]
    tap.streams = []

    def factory(**kwargs):
        s = FakeStream(**kwargs)
        tap.streams.append(s)
        return s

    tap._pause_stream_factory = factory
    tap.grid = Gtk.Grid()
    tap.build(tap.grid, 0)
    return tap


def stopped(stream):
    """Whether *stream* was stopped and closed. Preferences does that off
    its window's thread, so it is given a moment."""
    deadline = time.monotonic() + 2
    while not (stream.stopped and stream.closed) and time.monotonic() < deadline:
        time.sleep(0.01)
    return stream.stopped and stream.closed


def opened(win, count):
    """Whether *count* streams have been opened, the last one started. A test
    started right after another opens its stream on the GTK thread once the
    last one is closed, so the main loop is run meanwhile."""
    deadline = time.monotonic() + 2
    while not (len(win.streams) == count and win.streams[-1].started) \
            and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    return len(win.streams) == count and win.streams[-1].started


def slow_to_stop(win):
    """Make each new stream's stop() wait until the returned Event is set."""
    release = threading.Event()

    class SlowToStop(FakeStream):
        def stop(self, ignore_errors=True):
            release.wait(5)
            super().stop()
    win._pause_stream_factory = lambda **kwargs: win.streams.append(SlowToStop(**kwargs)) or \
        win.streams[-1]
    return release


def labels(widget):
    return [w.get_text() for w in gtk_text.widgets(widget) if isinstance(w, Gtk.Label)]


def test_the_entry_has_its_heading_and_choice(win):
    assert "While you talk" in labels(win.grid)
    assert win.while_talking.get_active_id() == "nothing"
    win.while_talking.set_active_id("pause")
    assert win.tap_fold.button.get_label() == "Adjust Type at pause"
    summary = win.tap_fold.summary.get_text()
    assert summary == "pause 0.8 s \u00b7 punctuation from context on"
    assert not win.tap_fold.is_open()


def test_type_at_pauses_settings_show_only_when_it_is_the_choice(win):
    assert not win.tap_fold.get_visible()
    win.while_talking.set_active_id("pause")
    assert win.tap_fold.get_visible()
    assert ("chunked_dictation", True) in win.win.updates
    win.while_talking.set_active_id("box")
    assert not win.tap_fold.get_visible()


@pytest.mark.parametrize("choice, pause, box", [("nothing", False, False),
                                                ("box", False, True), ("pause", True, False)])
def test_one_choice_never_leaves_both_on(win, choice, pause, box):
    """Both on, the box showed each phrase and then emptied as it was typed."""
    win.while_talking.set_active_id("box" if choice != "box" else "pause")
    win.while_talking.set_active_id(choice)
    assert (win.win.config["chunked_dictation"], win.win.config["live_text"]) == (pause, box)


@pytest.mark.parametrize("device", ["cpu", "vulkan"])
def test_the_box_says_when_this_computer_cant_show_it(win, device):
    """On the processor the box never opens: picked anyway, with no word
    why, it would look broken."""
    win.win.config["device"] = device
    win.while_talking.set_active_id("box")
    on_processor = win.while_talking_note.get_text() == prefs_type_at_pause.BOX_ON_PROCESSOR
    assert on_processor is (device == "cpu")


def test_the_setting_going_off_is_written_first(win):
    """The service reads each write: it must never see both on."""
    win.while_talking.set_active_id("box")
    win.win.updates.clear()
    win.while_talking.set_active_id("pause")
    assert win.win.updates == [("live_text", False), ("chunked_dictation", True)]


def test_the_controls_write_their_settings(win):
    win.tap_pause_spin.set_value(1.2)
    win.tap_context_check.set_active(False)
    assert ("chunk_pause_seconds", 1.2) in win.win.updates
    assert ("chunk_context_marks", False) in win.win.updates


def test_the_test_opens_the_chosen_mic_and_shows_what_it_hears(win):
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    stream = win.streams[-1]
    assert stream.started
    assert stream.kwargs["device"] == 3 and stream.kwargs["samplerate"] == 16000
    assert stream.kwargs["dtype"] == "int16"
    assert win.tap_test_btn.get_label() == "Stop"
    block = (np.random.default_rng(3).normal(0, 380, 320)).astype(np.int16).reshape(-1, 1)
    stream.kwargs["callback"](block, 320, None, None)
    win._tap_show(win._pause_test)
    assert win.tap_status.get_text().startswith(("Waiting", "Quiet", "Hearing you talk"))
    win.tap_test_btn.clicked()                  # Stop: the result, not the frozen live line
    assert win.tap_status.get_text().startswith(("Test done", "Test stopped"))


def test_closing_preferences_stops_the_test(win):
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    win.win._stop_tests(closing=True)       # what OK, Cancel and the X all call
    assert win.streams[-1].stopped and win.streams[-1].closed
    assert win.tap_test_btn.get_label() == "Test"


def test_turning_the_feature_off_stops_the_test(win):
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    win.while_talking.set_active_id("nothing")
    assert stopped(win.streams[-1])


def test_the_button_opens_the_settings_and_hiding_them_stops_the_test(win):
    win.while_talking.set_active_id("pause")
    win.tap_fold.button.clicked()
    assert win.tap_fold.is_open()
    assert win.tap_fold.button.get_label() == "Hide Type at pause"
    win.tap_test_btn.clicked()
    win.tap_fold.button.clicked()
    assert not win.tap_fold.is_open()
    assert win.tap_fold.button.get_label() == "Adjust Type at pause"
    assert stopped(win.streams[-1])


def test_leaving_the_advanced_tab_stops_the_test(win):
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    notebook = SimpleNamespace(get_tab_label_text=lambda page: "Audio")
    win.win.on_tab_switched(notebook, Gtk.ScrolledWindow(), 1)
    assert stopped(win.streams[-1])


def test_a_mic_that_wont_open_says_so(win):
    def broken(**kwargs):
        raise OSError("Device unavailable")
    win._pause_stream_factory = broken
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    assert "Couldn't open the microphone" in win.tap_status.get_text()
    assert win.tap_test_btn.get_label() == "Test"


def test_a_voice_detector_that_fails_stops_the_test_and_says_so(win):
    """An error in the stream's callback ended the stream: the meter froze
    with the test still showing as running."""
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    stream = win.streams[-1]

    def broken(block):
        raise ValueError("broken")
    win._pause_test._detector.feed = broken
    stream.kwargs["callback"](np.zeros((320, 1), np.int16), 320, None, None)
    win._tap_show(win._pause_test)
    assert win.tap_status.get_text() == prefs_type_at_pause.DETECTOR_FAILED
    assert win.tap_test_btn.get_label() == "Test" and stopped(stream)


def test_a_voice_detector_that_wont_load_says_so(win, monkeypatch):
    def unavailable():
        raise voice_detector.Unavailable("the voice detector didn't load")
    monkeypatch.setattr(voice_detector, "_model", unavailable)
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    assert win.streams == [] and win.tap_test_btn.get_label() == "Test"
    assert win.tap_status.get_text() == prefs_type_at_pause.DETECTOR_MISSING


def test_a_mic_that_cant_be_found_says_so(win):
    """The microphone's device is looked up before any stream exists; a
    lookup that failed left the test button doing nothing at all."""
    def no_devices():
        raise OSError("no audio devices")
    win.win.get_selected_device_idx = no_devices
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    assert win.tap_status.get_text() == "Couldn't open the microphone: no audio devices"
    assert win.tap_test_btn.get_label() == "Test"


def test_a_new_pause_length_applies_to_a_running_test(win):
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    win.tap_pause_spin.set_value(1.5)
    assert win._pause_test._detector.pause_seconds == 1.5


def test_the_advanced_tab_builds_it(prefs_window):
    from gtk_text import below, find
    window = prefs_window()
    labs = below(find(window.window, Gtk.Label, "🧪 TuxTalk Labs"))
    choice = window.advanced_tab.type_at_pause.while_talking
    assert choice in labs
    choice.set_active_id("pause")
    assert window.config["chunked_dictation"] is True


def test_help_explains_type_at_pause(help_pages):
    hands_free = next(text for title, text in help_pages.items() if "Hands-free" in title)
    assert "Type at Pause" in hands_free
    # The heading, and beside it where to find those settings.
    assert "Fine-tuning\n(under While you talk → Adjust Type at pause)" in hands_free


# --- stopping the test, its microphone, and what the settings show ------------------

def test_leaving_the_window_stops_the_test(win):
    """Clicking into the app to dictate takes the focus from Preferences. A test
    still holding a direct hw: mic would make the dictation fail with only a
    beep, so the test stops then."""
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    win.win._on_window_active(SimpleNamespace(is_active=lambda: False), None)
    assert stopped(win.streams[-1])


def test_a_microphone_that_never_stops_doesnt_freeze_the_window(win, monkeypatch):
    """A stream that vanished from the audio system can hang stop() for
    good. Stop is left to another thread, and closing Preferences waits for
    it only a moment."""
    monkeypatch.setattr(mic_stream, "STOP_TIMEOUT_S", 0.1)
    monkeypatch.setattr(mic_stream, "_closing", set())
    release = slow_to_stop(win)
    win.while_talking.set_active_id("pause")

    def closing():
        win.win._stop_tests(closing=True)
    for count, stop in ((1, win.tap_test_btn.clicked), (2, closing)):
        win.tap_test_btn.clicked()
        assert opened(win, count)
        started = time.monotonic()
        stop()
        assert time.monotonic() - started < 2      # the stream never stops at all
        assert win.tap_test_btn.get_label() == "Test"
    release.set()
    assert all(stopped(stream) for stream in win.streams)


def test_a_stream_still_stopping_feeds_nothing_to_the_next_test(win):
    """Stop hands the stream to another thread to close, and Test comes back
    at once: the old stream's callback still fed the new test's detector,
    and its redraw drew the new test."""
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    old = win.streams[0].kwargs["callback"]
    speech = (np.random.default_rng(5).normal(0, 6000, 320)).astype(np.int16).reshape(-1, 1)
    old(speech, 320, None, None)                 # its redraw is queued
    win.tap_test_btn.clicked()
    assert stopped(win.streams[0])
    win.tap_test_btn.clicked()
    assert opened(win, 2)
    test = win._pause_test
    for _ in range(50):
        old(speech, 320, None, None)            # late, from the old stream's thread
    while GLib.MainContext.default().iteration(False):
        pass
    assert test.snapshot().level == 0.0 and test._detector.speech_time == 0.0
    assert win.tap_status.get_text() == "Listening\u2026 talk, then pause."


def test_the_next_test_waits_until_the_last_stream_is_closed(win):
    """An ALSA hw: device is held until its stream is closed, so opening it
    again at once failed as busy ("Device unavailable")."""
    release = slow_to_stop(win)
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    win.tap_test_btn.clicked()                   # Stop
    win.tap_test_btn.clicked()                   # Test again
    assert len(win.streams) == 1 and not win.tap_test_btn.get_sensitive()
    release.set()
    assert opened(win, 2) and win.streams[0].closed
    assert win.tap_test_btn.get_sensitive() and win.tap_test_btn.get_label() == "Stop"


STOP_PATHS = {
    "leaving the window": lambda win: win.win._on_window_active(
        SimpleNamespace(is_active=lambda: False), None),
    "another tab": lambda win: win.win.on_tab_switched(
        SimpleNamespace(get_tab_label_text=lambda page: "Audio"),
        Gtk.ScrolledWindow(), 1),
    "hiding the settings": lambda win: win.tap_fold.button.clicked(),
    "turning it off": lambda win: win.while_talking.set_active_id("nothing"),
    "closing Preferences": lambda win: win.win._stop_tests(closing=True),
}


@pytest.mark.parametrize("stop", STOP_PATHS.values(), ids=STOP_PATHS.keys())
def test_a_test_called_off_while_it_waits_never_opens_the_mic(win, stop):
    """Test again while the last stream is still stopping waits to open the
    mic. Going off to dictate (or any other way the test stops) meanwhile
    left it waiting all the same: it then opened the mic with Preferences in
    the background, and a hw: mic was busy for the dictation."""
    release = slow_to_stop(win)
    win.while_talking.set_active_id("pause")
    win.tap_fold.button.clicked()                # open, so hiding them is a way to stop
    win.tap_test_btn.clicked()
    win.tap_test_btn.clicked()                   # Stop
    win.tap_test_btn.clicked()                   # Test again: waits
    stop(win)
    release.set()
    assert stopped(win.streams[0]) and win._pause_reopen.closing.wait(2)
    deadline = time.monotonic() + 0.3            # what the wait hands to the main loop
    while time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    assert len(win.streams) == 1 and win._pause_test is None
    assert win.tap_test_btn.get_sensitive() and win.tap_test_btn.get_label() == "Test"


def test_the_window_focus_is_watched(prefs_window, monkeypatch):
    seen = []
    monkeypatch.setattr(PreferencesWindow, "_on_window_active",
                        lambda self, window, _param: seen.append(window))
    window = prefs_window()
    window.window.notify("is-active")
    assert window.window in seen


def test_a_stream_that_wont_start_is_closed(win):
    class NoStart(FakeStream):
        def start(self):
            raise OSError("Device unavailable")
    opened = []

    def factory(**kwargs):
        s = NoStart(**kwargs)
        opened.append(s)
        return s

    win._pause_stream_factory = factory
    win.while_talking.set_active_id("pause")
    win.tap_test_btn.clicked()
    assert stopped(opened[-1])
    assert "Couldn't open the microphone" in win.tap_status.get_text()


def test_punctuation_from_context_comes_first(win):
    assert win.tap_fine.get_child_at(0, 0) is win.tap_context_check


def test_the_title_follows_the_settings(win):
    win.while_talking.set_active_id("pause")
    win.tap_pause_spin.set_value(1.2)
    win.tap_context_check.set_active(False)
    summary = win.tap_fold.summary.get_text()
    assert "pause 1.2 s" in summary and "punctuation from context off" in summary


def test_opening_the_settings_scrolls_them_into_view(win, monkeypatch):
    shown = []
    monkeypatch.setattr("tuxtalk.prefs_tab.scroll_into_view",
                        lambda widget, delay_ms=60: shown.append(widget))
    win.while_talking.set_active_id("pause")
    win.tap_fold.button.clicked()
    assert shown == [win.tap_fold.revealer]
    win.tap_fold.button.clicked()                # closing doesn't scroll
    assert shown == [win.tap_fold.revealer]
