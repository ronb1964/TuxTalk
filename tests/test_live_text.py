"""Live text (TuxTalk Labs): the words so far, shown while the user talks."""
import logging
import threading

import numpy as np
import pytest

from tuxtalk import dictation_worker, engine, focused_window, live_text, x11_desktop
from tuxtalk import live_settings as live
from tuxtalk import logger as tt_logger
from tuxtalk import recording_state as rs


class Window:
    """Stands in for live_text_window.LiveTextWindow: what it was asked to
    show, and whether it is up now."""

    def __init__(self):
        self.shown, self.hidden, self.up = [], 0, False

    def show(self, words, rec, phrase):
        self.shown.append(words)
        self.up = True

    def hide(self):
        self.hidden += 1
        self.up = False


def _speech(seconds, rate=16000):
    """*seconds* of int16 audio blocks, as the audio callback keeps them."""
    samples = (np.sin(np.arange(int(rate * seconds)) / 9) * 8000).astype(np.int16)
    return [samples.tobytes()]


@pytest.fixture
def talking(monkeypatch):
    """A recording running with live text on; returns (recording, window,
    the length of each piece of audio transcribed)."""
    window, heard = Window(), []
    rec = rs.Recording()
    rec.frames = _speech(3)
    monkeypatch.setattr(live_text, "window", window)
    monkeypatch.setattr(live_text, "_last_read", 0.0)
    monkeypatch.setattr(live_text, "_too_slow", None)
    monkeypatch.setattr(live_text, "_shown", None)
    monkeypatch.setattr(live, "live_text", True)
    monkeypatch.setattr(live, "remove_fillers", False)
    monkeypatch.setattr(rs.state, "current", rec)
    monkeypatch.setattr(rs.state, "is_recording", True)
    monkeypatch.setattr(x11_desktop, "popup_ends_show_desktop", lambda: False)

    def transcribe(audio, language):
        heard.append(len(audio))
        return "so far so good"
    monkeypatch.setattr(engine, "transcribe", transcribe)
    return rec, window, heard


def test_the_words_so_far_are_shown_while_recording(talking):
    _rec, window, heard = talking
    live_text.tick(now=100.0)
    assert window.shown == ["so far so good"]
    assert heard == [3 * 16000]


def test_they_are_read_again_about_once_a_second(talking):
    _rec, window, _heard = talking
    for now in (100.0, 100.4, 100.9, 101.0, 101.5):
        live_text.tick(now=now)
    assert len(window.shown) == 2           # at 100.0 and 101.0


@pytest.mark.parametrize("why", ["off", "not recording", "voice edit", "a read running"])
def test_no_read_when_it_isnt_wanted(talking, monkeypatch, why):
    rec, window, _heard = talking
    if why == "off":
        monkeypatch.setattr(live, "live_text", False)
    elif why == "not recording":
        monkeypatch.setattr(rs.state, "is_recording", False)
    elif why == "voice edit":
        rec.edit_mode = True
    else:
        live_text._reading.set()
    try:
        live_text.tick(now=100.0)
    finally:
        live_text._reading.clear()
    assert window.shown == []


def test_a_password_field_never_shows_the_words(talking, monkeypatch):
    """Said into a password field, the words aren't typed; they mustn't
    show on screen either, and what was up before goes."""
    _rec, window, heard = talking
    live_text.tick(now=100.0)
    assert window.up
    monkeypatch.setattr(focused_window, "is_password", lambda budget=None: True)
    live_text.tick(now=101.0)
    assert window.shown == ["so far so good"] and not window.up
    assert heard == [3 * 16000]             # the password audio was never read


def test_cinnamons_show_desktop_isnt_ended_by_the_box(talking, monkeypatch):
    _rec, window, _heard = talking
    monkeypatch.setattr(x11_desktop, "popup_ends_show_desktop", lambda: True)
    live_text.tick(now=100.0)
    assert window.shown == []


def test_a_phrase_typed_at_a_pause_leaves_the_box(talking):
    """With Type at pause the words just typed mustn't stay up as if unsaid."""
    rec, window, _heard = talking
    live_text.tick(now=100.0)
    rec.chunks_submitted += 1               # the phrase was cut and typed
    live_text.tick(now=100.2)
    assert not window.up


def test_a_read_of_a_phrase_already_typed_shows_nothing(talking):
    """A read handed the audio before a cut, finishing after it."""
    rec, window, _heard = talking
    live_text._read(rec, _speech(2), 16000, rec.chunks_submitted - 1)
    assert window.shown == []


def test_a_read_after_the_stop_shows_nothing(talking, monkeypatch):
    rec, window, _heard = talking
    monkeypatch.setattr(rs.state, "is_recording", False)
    live_text._read(rec, _speech(2), 16000, rec.chunks_submitted)
    assert window.shown == []


def test_when_nothing_is_heard_old_words_dont_stay_up(talking, monkeypatch):
    _rec, window, _heard = talking
    live_text.tick(now=100.0)
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: None)
    live_text.tick(now=101.0)
    assert not window.up


def test_fillers_left_out_of_the_typed_text_are_left_out_here_too(talking, monkeypatch):
    _rec, window, _heard = talking
    monkeypatch.setattr(live, "remove_fillers", True)
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: "so um we should go")
    live_text.tick(now=100.0)
    assert window.shown == ["so we should go"]


def test_a_slow_read_ends_live_text_for_that_phrase_only(talking, monkeypatch):
    """A read takes the worker the typed text needs: one longer than the
    budget stops the reads until the next phrase."""
    rec, window, heard = talking
    monkeypatch.setattr(live_text, "READ_BUDGET_S", -1.0)     # every read is too slow
    live_text.tick(now=100.0)
    assert window.shown == [] and not window.up
    live_text.tick(now=101.0)
    assert len(heard) == 1                  # no more reads for this phrase
    monkeypatch.setattr(live_text, "READ_BUDGET_S", 60.0)
    rec.chunks_submitted += 1
    live_text.tick(now=102.0)
    assert window.shown == ["so far so good"]


def test_a_long_dictation_reads_only_its_newest_audio(talking):
    """Without Type at pause the audio grows with the whole dictation;
    reading it all every second would get slower and slower."""
    rec, _window, heard = talking
    rec.frames = _speech(live_text.MAX_AUDIO_S + 30)
    live_text.tick(now=100.0)
    assert heard == [int(live_text.MAX_AUDIO_S * 16000)]


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
    def broken(audio, language):
        raise RuntimeError("engine hiccup")
    monkeypatch.setattr(engine, "transcribe", broken)
    with pytest.raises(RuntimeError):
        live_text.tick(now=100.0)
    assert not live_text._reading.is_set()


def test_stopping_closes_the_window(talking):
    _rec, window, _heard = talking
    live_text.tick(now=100.0)
    live_text.hide()
    assert not window.up


@pytest.fixture
def real_worker(monkeypatch):
    """The real dictation worker, one thread running jobs in order."""
    worker = dictation_worker.DictationWorker()
    monkeypatch.setattr(dictation_worker, "_instance", worker)
    yield worker
    assert worker.drain(5)


def test_no_read_is_queued_behind_a_dictation_being_typed(talking, real_worker):
    _rec, window, heard = talking
    typing, release = threading.Event(), threading.Event()
    real_worker.submit(lambda: (typing.set(), release.wait(5)))
    assert typing.wait(5)
    live_text.tick(now=100.0)
    release.set()
    assert real_worker.drain(5)
    assert heard == [] and not live_text._reading.is_set()


def test_a_phrase_cut_during_a_read_is_typed_right_after_it(talking, real_worker,
                                                            monkeypatch):
    """The read already running finishes, its words (now typed) aren't
    shown, and the phrase's own job runs next."""
    rec, window, _heard = talking
    reading, release, order = threading.Event(), threading.Event(), []

    def slow_transcribe(audio, language):
        reading.set()
        release.wait(5)
        order.append("read")
        return "the phrase"
    monkeypatch.setattr(engine, "transcribe", slow_transcribe)
    live_text.tick(now=100.0)
    assert reading.wait(5)
    rec.chunks_submitted += 1               # Type at pause cut it meanwhile
    real_worker.submit(lambda: order.append("typed"))
    release.set()
    assert real_worker.drain(5)
    assert order == ["read", "typed"] and window.shown == []


def test_submit_if_idle_queues_nothing_while_the_worker_has_work(real_worker):
    release = threading.Event()
    real_worker.submit(lambda: release.wait(5))
    assert not real_worker.submit_if_idle(lambda: None)
    release.set()
    assert real_worker.drain(5)
    ran = threading.Event()
    assert real_worker.submit_if_idle(ran.set)
    assert ran.wait(5)


def test_live_reads_leave_the_log_alone_but_not_its_warnings():
    """A line of text every second, from every engine, would be most of the
    log: the log file's own handler leaves them out."""
    handler = tt_logger._shared_handler()

    def kept(level):
        return bool(handler.filter(logging.LogRecord(
            "tuxtalk.parakeet_engine", level, __file__, 1, "Raw transcription", None, None)))
    with tt_logger.quietly():
        with tt_logger.quietly():
            pass
        assert not kept(logging.INFO)           # still quiet after an inner block
        assert kept(logging.WARNING)
    assert kept(logging.INFO)


def test_the_labs_switch_turns_it_on(prefs_window):
    """Off until it's ticked; ticking it changes the setting to be saved."""
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
