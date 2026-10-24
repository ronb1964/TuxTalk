"""Preferences' microphone test stops when the user goes off to dictate:
another window takes the focus, or another tab is shown. Before, only Type
at pause's test stopped then; a recording left running, or one still
waiting for the last stream to close, kept a direct hw: microphone (or
opened it later), and the dictation failed with only a beep. The streams
are fakes; nothing is recorded."""
import threading
import time

import gi
import pytest
import sounddevice

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from test_mic_test import Microphone  # noqa: E402
from tuxtalk import mic_stream  # noqa: E402

AUDIO_TAB = 1


@pytest.fixture
def prefs(prefs_window, monkeypatch):
    """Preferences on its Audio tab and holding the focus (Xvfb has no
    window manager to give it), with a microphone test whose streams go in
    prefs.opened and whose stop() waits until prefs.release is set."""
    opened, release = [], threading.Event()
    release.set()
    monkeypatch.setattr(Gtk.Window, "is_active", lambda window: True)
    monkeypatch.setattr(mic_stream, "input_samplerate", lambda device, rate: rate)
    monkeypatch.setattr(sounddevice, "InputStream",
                        lambda **settings: Microphone(opened, release, **settings))
    window = prefs_window()
    window.audio_tab.mic_test._device = lambda: None
    window.notebook.set_current_page(AUDIO_TAB)
    window.opened, window.release = opened, release
    yield window
    window.audio_tab.mic_test.close()          # a start a failed test left waiting opens nothing later
    release.set()


def leave_the_window(prefs, monkeypatch):
    monkeypatch.setattr(Gtk.Window, "is_active", lambda window: False)
    prefs.window.notify("is-active")


def show_another_tab(prefs, monkeypatch):
    prefs.notebook.set_current_page(0)


STOP_PATHS = {"another window": leave_the_window, "another tab": show_another_tab}


def run_main_loop(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


@pytest.mark.parametrize("stop", STOP_PATHS.values(), ids=STOP_PATHS.keys())
def test_a_recording_stops_and_frees_the_microphone(prefs, stop, monkeypatch):
    mic = prefs.audio_tab.mic_test
    mic.start_btn.clicked()
    assert mic.recording
    stop(prefs, monkeypatch)
    assert not mic.recording and prefs.opened[0].closed.wait(2)
    run_main_loop(0.1)
    assert len(prefs.opened) == 1
    assert mic.start_btn.get_sensitive() and not mic.stop_btn.get_sensitive()


@pytest.mark.parametrize("stop", STOP_PATHS.values(), ids=STOP_PATHS.keys())
def test_a_recording_waiting_to_start_never_opens_the_microphone(prefs, stop, monkeypatch):
    """Start again while the last stream is still stopping waits to open the
    mic; going off meanwhile left it waiting, and it opened the mic with
    Preferences in the background."""
    mic = prefs.audio_tab.mic_test
    prefs.release.clear()
    mic.start_btn.clicked()
    mic.stop_btn.clicked()
    mic.start_btn.clicked()                        # waits for the close
    stop(prefs, monkeypatch)
    assert not mic._reopen.waiting
    prefs.release.set()
    assert prefs.opened[0].closed.wait(2) and mic._reopen.closing.wait(2)
    run_main_loop(0.3)                             # what the wait hands to the main loop
    assert len(prefs.opened) == 1 and not mic.recording
    assert mic.start_btn.get_sensitive() and not mic.stop_btn.get_sensitive()


def test_the_audio_tab_keeps_its_recording(prefs):
    """Showing the Audio tab itself, as when Preferences opens on it, stops nothing."""
    prefs.notebook.set_current_page(0)
    prefs.audio_tab.mic_test.start_btn.clicked()
    prefs.notebook.set_current_page(AUDIO_TAB)
    assert prefs.audio_tab.mic_test.recording
