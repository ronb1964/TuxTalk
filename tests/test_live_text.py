"""Live text (TuxTalk Labs): the words so far, shown while the user talks."""
import logging

import numpy as np
import pytest

from tuxtalk import dictation_worker, engine, live_text
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs


class Window:
    """Stands in for live_text_window.LiveTextWindow: what it was asked to show."""

    def __init__(self):
        self.shown, self.hidden = [], 0

    def show(self, words, rec):
        self.shown.append((words, rec))

    def hide(self):
        self.hidden += 1


def _speech(seconds, rate=16000):
    """*seconds* of int16 audio blocks, as the audio callback keeps them."""
    samples = (np.sin(np.arange(int(rate * seconds)) / 9) * 8000).astype(np.int16)
    return [samples.tobytes()]


@pytest.fixture
def talking(monkeypatch):
    """A recording running with live text on; returns (recording, window,
    what was transcribed)."""
    window, heard = Window(), []
    rec = rs.Recording()
    rec.frames = _speech(3)
    monkeypatch.setattr(live_text, "window", window)
    monkeypatch.setattr(live_text, "_last_read", 0.0)
    monkeypatch.setattr(live, "live_text", True)
    monkeypatch.setattr(rs.state, "current", rec)
    monkeypatch.setattr(rs.state, "is_recording", True)

    def transcribe(audio, language):
        heard.append(len(audio))
        return "so far so good"
    monkeypatch.setattr(engine, "transcribe", transcribe)
    return rec, window, heard


def test_the_words_so_far_are_shown_while_recording(talking):
    rec, window, heard = talking
    live_text.tick(now=100.0)
    assert window.shown == [("so far so good", rec)]
    assert heard == [3 * 16000]


def test_they_are_read_again_about_once_a_second(talking):
    _rec, window, _heard = talking
    for now in (100.0, 100.4, 100.9, 101.0, 101.5):
        live_text.tick(now=now)
    assert len(window.shown) == 2           # at 100.0 and 101.0


@pytest.mark.parametrize("why", ["off", "not recording", "voice edit", "worker busy",
                                 "a read running"])
def test_no_read_when_it_isnt_wanted_or_would_hold_up_dictation(talking, monkeypatch, why):
    rec, window, _heard = talking
    if why == "off":
        monkeypatch.setattr(live, "live_text", False)
    elif why == "not recording":
        monkeypatch.setattr(rs.state, "is_recording", False)
    elif why == "voice edit":
        rec.edit_mode = True
    elif why == "worker busy":
        monkeypatch.setattr(dictation_worker, "idle", lambda: False)
    else:
        live_text._reading.set()
        monkeypatch.setattr(live_text, "_reading", live_text._reading)
    try:
        live_text.tick(now=100.0)
    finally:
        live_text._reading.clear()
    assert window.shown == []


def test_a_moment_of_sound_isnt_read(talking):
    rec, window, heard = talking
    rec.frames = _speech(0.2)
    live_text.tick(now=100.0)
    assert heard == [] and window.shown == []


def test_a_microphone_at_another_rate_is_read_at_the_engines(talking):
    rec, _window, heard = talking
    rec.samplerate = 48000
    rec.frames = _speech(2, rate=48000)
    live_text.tick(now=100.0)
    assert heard == [2 * 16000]


def test_a_failed_read_lets_the_next_one_run(talking, monkeypatch):
    _rec, window, _heard = talking

    def broken(audio, language):
        raise RuntimeError("engine hiccup")
    monkeypatch.setattr(engine, "transcribe", broken)
    with pytest.raises(RuntimeError):
        live_text.tick(now=100.0)
    assert not live_text._reading.is_set()


def test_stopping_closes_the_window(talking):
    _rec, window, _heard = talking
    live_text.hide()
    assert window.hidden == 1


def test_live_reads_leave_the_log_alone_but_not_its_warnings(caplog):
    """A line of text every second would be most of the log."""
    with caplog.at_level(logging.INFO, logger="tuxtalk.engine"):
        with engine.quietly():
            engine.logger.info("Raw transcription (Parakeet): so far")
            engine.logger.warning("Transcription took 3.10s")
        engine.logger.info("Raw transcription (Parakeet): the whole thing")
    assert [r.getMessage() for r in caplog.records] == [
        "Transcription took 3.10s", "Raw transcription (Parakeet): the whole thing"]


def test_the_labs_switch_turns_it_on(prefs_window):
    """Off until it's ticked, then saved with the rest on Apply."""
    from gi.repository import Gtk
    win = prefs_window()

    def checks(widget):
        if isinstance(widget, Gtk.CheckButton):
            yield widget
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                yield from checks(child)
    [switch] = [c for c in checks(win.window) if c.get_label() == "Show the words while you talk"]
    assert not switch.get_active()
    switch.set_active(True)
    assert win.config["live_text"] is True
