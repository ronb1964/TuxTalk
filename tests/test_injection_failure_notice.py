"""A dictation that never reaches the window must not fail silently.

Transcription can work perfectly (Recent Dictations holds the text word for
word) while nothing reaches the window, for example when paste is unavailable
on X11 and typing fails too. Without a notice the app simply seems not to work.

_inject_text reports how it went ("ok", "partial" or "failed"), and
_transcribe_and_inject tells the user about anything but "ok": always with a
desktop notification, like a missing microphone, and with the low cancel beep
when beeps are on. The notification points at Recent Dictations, where the text
is saved before injection is even attempted.
"""

import numpy as np
import pytest

from tuxtalk import app, focused_window, window_classes
from tuxtalk import engine
from tuxtalk import live_settings as live
from tuxtalk import keyboard_output

MARKER = "\xa7SHIFT_ENTER\xa7"


@pytest.fixture
def quiet(monkeypatch):
    """No real tools, sleeps, focus lookups or Electron detection."""
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: True)
    monkeypatch.delenv("FLATPAK_ID", raising=False)


def _paste_mode(monkeypatch):
    monkeypatch.setattr(app, "_determine_injection_method", lambda m: ("paste", "test"))


# --- _inject_text reports its outcome ------------------------------------------

def test_a_paste_that_lands_is_ok(monkeypatch, quiet):
    _paste_mode(monkeypatch)
    monkeypatch.setattr(keyboard_output, "paste_text", lambda *a, **k: True)
    assert app._inject_text("hello", "auto") == "ok"


def test_nothing_landing_is_failed(monkeypatch, quiet):
    """Paste unavailable on X11, then typing fails too."""
    _paste_mode(monkeypatch)
    monkeypatch.setattr(keyboard_output, "paste_text", lambda *a, **k: False)
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: False)
    assert app._inject_text("Привіт", "auto") == "failed"


def test_typing_that_lands_after_a_refused_paste_is_ok(monkeypatch, quiet):
    _paste_mode(monkeypatch)
    monkeypatch.setattr(keyboard_output, "paste_text", lambda *a, **k: False)
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: True)
    assert app._inject_text("hello", "auto") == "ok"


def test_some_chunks_landing_is_partial(monkeypatch, quiet):
    _paste_mode(monkeypatch)
    monkeypatch.setattr(keyboard_output, "paste_text", lambda part, *a, **k: part != "third")
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: False)
    text = MARKER.join(["first", "second", "third"])
    assert app._inject_text(text, "auto") == "partial"


def test_type_mode_failure_is_failed(monkeypatch, quiet):
    monkeypatch.setattr(app, "_determine_injection_method", lambda m: ("type", "test"))
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: False)
    assert app._inject_text("hello", "type") == "failed"


@pytest.fixture
def electron(monkeypatch, quiet):
    """Claude Desktop focused: typed, not pasted."""
    cls = next(iter(window_classes.ELECTRON_PASTE_BROKEN_CLASSES))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: cls)
    monkeypatch.setattr(focused_window, "look", lambda: (cls, None))
    _paste_mode(monkeypatch)


def test_typing_into_electron_that_lands_is_ok(monkeypatch, electron):
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: True)
    assert app._inject_text("hello", "auto") == "ok"


def test_typing_into_electron_stopping_partway_is_partial(monkeypatch, electron):
    calls = []
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: calls.append(t) or len(calls) == 1)
    assert app._inject_text(MARKER.join(["first", "second"]), "auto") == "partial"


# --- _transcribe_and_inject tells the user --------------------------------------

@pytest.fixture
def dictation(monkeypatch):
    """A dictation that transcribes to "Hello there." with everything recorded."""
    notes, beeps = [], []
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: "hello there")
    monkeypatch.setattr(app, "_prepare_text", lambda raw, *a, **k: "Hello there. ")
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: notes.append(f"{title}\n{body}"))
    monkeypatch.setattr(app, "_beep", lambda enabled, *tone, **k: enabled and beeps.append(tone))
    monkeypatch.setattr("tuxtalk.history.add_entry", lambda text: None)
    monkeypatch.setattr(live, "usage_stats", False, raising=False)

    def run(outcome, beeps_on=True, notify_on=False):
        if isinstance(outcome, Exception):
            def boom(*a, **k):
                raise outcome
            monkeypatch.setattr(app, "_inject_text", boom)
        else:
            monkeypatch.setattr(app, "_inject_text", lambda text, mode, **kw: outcome)
        frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
        app._transcribe_and_inject(frames, engine.SAMPLE_RATE, beeps_on=beeps_on,
                                   smart_quotes=False, notify_on=notify_on)
        return notes, beeps

    return run


def test_a_failure_is_announced_even_with_notifications_off(dictation):
    notes, _ = dictation("failed", notify_on=False)
    assert len(notes) == 1
    assert "Recent Dictations" in notes[0] and "Ctrl+V" in notes[0]


def test_a_partial_delivery_is_announced_as_partial(dictation):
    notes, _ = dictation("partial")
    assert len(notes) == 1 and "part" in notes[0].lower()
    assert "Recent Dictations" in notes[0]


def test_a_failure_plays_the_low_beep_after_the_ready_beep(dictation):
    _, beeps = dictation("failed", beeps_on=True)
    assert beeps == [app.READY_BEEP, app.CANCEL_BEEP]


def test_no_beep_when_beeps_are_off(dictation):
    _, beeps = dictation("failed", beeps_on=False)
    assert beeps == []


def test_success_stays_quiet(dictation):
    notes, beeps = dictation("ok", beeps_on=True, notify_on=False)
    assert notes == [] and beeps == [app.READY_BEEP]


def test_an_exception_while_typing_is_a_failure_too(dictation):
    """It is a typing failure, not a "Transcription error" shown only with
    notifications on: the transcription worked."""
    notes, beeps = dictation(RuntimeError("the keyboard layout went away"), beeps_on=True)
    assert len(notes) == 1 and "Recent Dictations" in notes[0]
    assert beeps == [app.READY_BEEP, app.CANCEL_BEEP]
