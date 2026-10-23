"""Preferences → Advanced → TuxTalk Labs → Type at pause: the switch, the
fine-tuning, and the live test (with a fake microphone stream)."""
from types import SimpleNamespace

import gi
import numpy as np
import pytest

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import gtk_text  # noqa: E402
from tuxtalk.prefs import PreferencesWindow  # noqa: E402


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


@pytest.fixture
def win():
    """A PreferencesWindow with only what the Labs entry needs."""
    w = PreferencesWindow.__new__(PreferencesWindow)
    w.config = {"chunked_dictation": False, "chunk_pause_seconds": 0.8,
                "chunk_speech_db": 6.0, "chunk_context_marks": True, "mic": ""}
    w.updates = []

    def update_config(key, value):
        w.config[key] = value
        w.updates.append((key, value))

    w.update_config = update_config
    w.get_selected_device_idx = lambda: 3
    w._get_device_samplerate = lambda idx: 16000
    w.streams = []

    def factory(**kwargs):
        s = FakeStream(**kwargs)
        w.streams.append(s)
        return s

    w._pause_stream_factory = factory
    w.grid = Gtk.Grid()
    w._build_type_at_pause(w.grid, 0, 20, lambda text: Gtk.Label(label=text))
    return w


def labels(widget):
    return [w.get_text() for w in gtk_text.widgets(widget) if isinstance(w, Gtk.Label)]


def test_the_entry_has_its_heading_and_switch(win):
    assert "Type at pause" in labels(win.grid)
    assert win.tap_check.get_label() == "Type each phrase when you pause"
    assert win.tap_fold.button.get_label() == "Adjust Settings"
    summary = win.tap_fold.summary.get_text()
    assert "pause 0.8 s" in summary and "noise filter 6" in summary
    assert not win.tap_fold.is_open()


def test_the_switch_greys_the_fine_tuning(win):
    assert win.tap_fine.get_sensitive() is False
    assert win.tap_fold.header.get_sensitive() is False
    win.tap_check.set_active(True)
    assert win.tap_fine.get_sensitive() is True
    assert win.tap_fold.header.get_sensitive() is True
    assert ("chunked_dictation", True) in win.updates


def test_the_controls_write_their_settings(win):
    win.tap_pause_spin.set_value(1.2)
    win.tap_db_spin.set_value(9)
    win.tap_context_check.set_active(False)
    assert ("chunk_pause_seconds", 1.2) in win.updates
    assert ("chunk_speech_db", 9.0) in win.updates
    assert ("chunk_context_marks", False) in win.updates


def test_the_test_opens_the_chosen_mic_and_shows_what_it_hears(win):
    win.tap_check.set_active(True)
    win.tap_test_btn.clicked()
    stream = win.streams[-1]
    assert stream.started
    assert stream.kwargs["device"] == 3 and stream.kwargs["samplerate"] == 16000
    assert stream.kwargs["dtype"] == "int16"
    assert win.tap_test_btn.get_label() == "Stop"
    block = (np.random.default_rng(3).normal(0, 380, 320)).astype(np.int16).reshape(-1, 1)
    stream.kwargs["callback"](block, 320, None, None)
    win._tap_show()
    assert win.tap_status.get_text().startswith(("Waiting", "Quiet", "Hearing you talk"))
    win.tap_test_btn.clicked()                  # Stop: the result, not the frozen live line
    assert win.tap_status.get_text().startswith(("Test done", "Test stopped"))


def test_closing_preferences_stops_the_test(win):
    win.tap_check.set_active(True)
    win.tap_test_btn.clicked()
    win._stop_mic_test()                 # what OK, Cancel and the X all call
    assert win.streams[-1].stopped and win.streams[-1].closed
    assert win.tap_test_btn.get_label() == "Test"


def test_turning_the_feature_off_stops_the_test(win):
    """Review focus 2."""
    win.tap_check.set_active(True)
    win.tap_test_btn.clicked()
    win.tap_check.set_active(False)
    assert win.streams[-1].stopped


def test_the_button_opens_the_settings_and_hiding_them_stops_the_test(win):
    win.tap_check.set_active(True)
    win.tap_fold.button.clicked()
    assert win.tap_fold.is_open()
    assert win.tap_fold.button.get_label() == "Hide Settings"
    win.tap_test_btn.clicked()
    win.tap_fold.button.clicked()
    assert not win.tap_fold.is_open()
    assert win.tap_fold.button.get_label() == "Adjust Settings"
    assert win.streams[-1].stopped


def test_leaving_the_advanced_tab_stops_the_test(win):
    win.tap_check.set_active(True)
    win.tap_test_btn.clicked()
    notebook = SimpleNamespace(get_tab_label_text=lambda page: "Audio")
    win.on_tab_switched(notebook, SimpleNamespace(get_vadjustment=lambda: None), 1)
    assert win.streams[-1].stopped


def test_a_mic_that_wont_open_says_so(win):
    """Review focus 4."""
    def broken(**kwargs):
        raise OSError("Device unavailable")
    win._pause_stream_factory = broken
    win.tap_check.set_active(True)
    win.tap_test_btn.clicked()
    assert "Couldn't open the microphone" in win.tap_status.get_text()
    assert win.tap_test_btn.get_label() == "Test"


def test_a_slider_change_applies_to_a_running_test(win):
    win.tap_check.set_active(True)
    win.tap_test_btn.clicked()
    win.tap_db_spin.set_value(12)
    assert win._pause_test._detector.speech_over_room == pytest.approx(10 ** (12 / 20))


def test_the_advanced_tab_builds_it(prefs_window):
    from gtk_text import below, find
    window = prefs_window()
    labs = below(find(window.window, Gtk.Label, "🧪 TuxTalk Labs"))
    switch = find(window.window, Gtk.CheckButton, "Type each phrase when you pause")
    assert switch in labs
    switch.set_active(True)
    assert window.config["chunked_dictation"] is True


def test_help_explains_type_at_pause(help_pages):
    hands_free = next(text for title, text in help_pages.items() if "Hands-free" in title)
    assert "Type at Pause" in hands_free
    assert "Fine-tuning (under Type at pause → Adjust Settings)" in hands_free


# --- final review fixes ------------------------------------------------------

def test_leaving_the_window_stops_the_test(win):
    """Clicking into the app to dictate takes the focus from Preferences. A test
    still holding a direct hw: mic would make the dictation fail with only a
    beep, so the test stops then."""
    win.tap_check.set_active(True)
    win.tap_test_btn.clicked()
    win._tap_on_window_active(SimpleNamespace(is_active=lambda: False), None)
    assert win.streams[-1].stopped


def test_the_window_focus_is_watched():
    connected = []
    w = PreferencesWindow.__new__(PreferencesWindow)
    w.config = {}
    w.update_config = lambda k, v: None
    w.window = SimpleNamespace(connect=lambda sig, cb: connected.append(sig))
    w._build_type_at_pause(Gtk.Grid(), 0, 20, lambda text: Gtk.Label(label=text))
    assert "notify::is-active" in connected


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
    win.tap_check.set_active(True)
    win.tap_test_btn.clicked()
    assert opened[-1].closed
    assert "Couldn't open the microphone" in win.tap_status.get_text()


def test_punctuation_from_context_comes_first(win):
    assert win.tap_fine.get_child_at(0, 0) is win.tap_context_check


def _listen(win, seconds=1.5):
    """Feed the running test room noise through its callback, then draw."""
    callback = win.streams[-1].kwargs["callback"]
    noise = np.random.default_rng(4).normal(0, 380, int(seconds * 16000)).astype(np.int16)
    for i in range(0, len(noise), 320):
        callback(noise[i:i + 320].reshape(-1, 1), 320, None, None)
    win._tap_show()


def test_the_mark_follows_the_sensitivity_after_the_test(win):
    """Stopped, the mark still moves with Sensitivity, using the room level the
    last test measured."""
    win.tap_check.set_active(True)
    win.tap_test_btn.clicked()
    _listen(win)
    win.tap_test_btn.clicked()               # Stop
    before = win.tap_meter.threshold
    win.tap_db_spin.set_value(12)
    assert win.tap_meter.threshold > before
    win.tap_db_spin.set_value(3)
    assert win.tap_meter.threshold < before


def test_no_mark_before_the_first_test(win):
    win.tap_check.set_active(True)
    win.tap_db_spin.set_value(12)
    assert win.tap_meter.threshold == 0.0


def test_the_title_follows_the_settings(win):
    win.tap_check.set_active(True)
    win.tap_pause_spin.set_value(1.2)
    win.tap_context_check.set_active(False)
    summary = win.tap_fold.summary.get_text()
    assert "pause 1.2 s" in summary and "punctuation from context off" in summary


def test_opening_the_settings_scrolls_them_into_view(win, monkeypatch):
    shown = []
    monkeypatch.setattr(type(win), "_scroll_into_view",
                        lambda self, widget, delay_ms=60: shown.append(widget), raising=False)
    win.tap_check.set_active(True)
    win.tap_fold.button.clicked()
    assert shown == [win.tap_fold.revealer]
    win.tap_fold.button.clicked()                # closing doesn't scroll
    assert shown == [win.tap_fold.revealer]
