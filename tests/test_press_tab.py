"""Ending a dictation with "press tab" presses the real Tab key (key_commands.py).

Forms are filled one box at a time ("John, press tab"). A tab character at
the end of the text would be stripped with the trailing space, and pasted
into a web form it stays in the box instead of moving to the next one.
Mid-sentence "press tab" still types a tab character (normalize.py).
"""
import numpy as np
import pytest

from tuxtalk import app, libei_ctypes
from tuxtalk.key_commands import split_key_command


@pytest.mark.parametrize("raw, expected", [
    ("John press tab", ("John", "TAB")),
    ("John, press tab.", ("John", "TAB")),
    # The engine's period goes: a name in a form box doesn't end in one.
    ("John. Press tab.", ("John", "TAB")),
    ("Is it ready? Press tab", ("Is it ready?", "TAB")),
    ("Hit tab.", ("", "TAB")),
    ("press tab", ("", "TAB")),
    ("Press the tab key.", ("", "TAB")),
    # Not at the end: normalize.py makes a tab character of it.
    ("name press tab address", ("name press tab address", "")),
    ("every tab shows", ("every tab shows", "")),
    # normalize.py types the words for these.
    ("Type literal press tab", ("Type literal press tab", "")),
    ("Type the word press tab", ("Type the word press tab", "")),
])
def test_split_press_tab(raw, expected):
    assert split_key_command(raw) == expected


def _dictate(monkeypatch, raw, window="brave-browser"):
    """One dictation through _transcribe_and_inject with the real text
    cleanup; what was typed and which keys were pressed, in order."""
    events = []
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, language: raw)
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: window)
    monkeypatch.setattr(app, "_inject_text",
                        lambda text, mode, t0: events.append(("typed", text)) or "ok")
    monkeypatch.setattr(app, "_press_key", lambda key, why: events.append((key, why)))
    frames = [(np.ones(app.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    app._transcribe_and_inject(frames, app.SAMPLE_RATE, beeps_on=False,
                               smart_quotes=False, notify_on=False)
    return events


@pytest.fixture
def no_auto_send(monkeypatch):
    monkeypatch.setattr(app, "_press_enter_after", False)


def test_a_form_box_gets_the_words_then_the_tab_key(monkeypatch, no_auto_send):
    """No auto-period and no auto-space: "John" in the box, then Tab."""
    assert _dictate(monkeypatch, "John press tab") == [
        ("typed", "John"), ("TAB", 'said "press tab"')]


def test_the_engines_period_before_press_tab_stays_out_of_the_box(monkeypatch, no_auto_send):
    assert _dictate(monkeypatch, "John. Press tab.") == [
        ("typed", "John"), ("TAB", 'said "press tab"')]


def test_press_tab_alone_only_presses_tab(monkeypatch, no_auto_send):
    assert _dictate(monkeypatch, "Press tab.") == [("TAB", 'said "press tab"')]


def test_press_tab_mid_sentence_still_types_a_tab_character(monkeypatch, no_auto_send):
    assert _dictate(monkeypatch, "name press tab address") == [("typed", "Name\taddress. ")]


def test_literal_press_tab_at_the_end_types_the_words(monkeypatch, no_auto_send):
    assert _dictate(monkeypatch, "Type literal press tab") == [("typed", "Type press tab. ")]


def test_press_tab_presses_the_key_in_claude_desktop_too(monkeypatch, no_auto_send):
    """Typed tabs become spaces there (a Tab would jump to the Send button),
    but saying it at the end asks for the key itself."""
    assert _dictate(monkeypatch, "John press tab", window="com.anthropic.Claude") == [
        ("typed", "John"), ("TAB", 'said "press tab"')]


def test_sending_automatically_doesnt_send_a_box_left_with_press_tab(monkeypatch):
    """The user is moving on to the next box, not submitting the form."""
    monkeypatch.setattr(app, "_press_enter_after", True)
    assert _dictate(monkeypatch, "John press tab") == [
        ("typed", "John"), ("TAB", 'said "press tab"')]


def test_a_box_ending_on_and_is_not_a_cut_off_dictation(monkeypatch):
    """ "Salt and press tab" was said in full: the next box doesn't carry on
    in lowercase, as it would after a dictation cut off on "and"."""
    monkeypatch.setattr(app, "_press_enter_after", True)
    monkeypatch.setattr(app.state, "continue_mid_sentence", False)
    assert _dictate(monkeypatch, "Salt and press tab") == [
        ("typed", "Salt and"), ("TAB", 'said "press tab"')]
    assert app.state.continue_mid_sentence is False


def test_pressing_tab_goes_through_the_output_backend_and_forgets_undo(monkeypatch):
    """Undo would backspace in the next box, and "edit that" there says
    nothing about a sent message."""
    pressed = []

    class Backend:
        def press_keys(self, chords, claim=None):
            pressed.append(chords)
            return True
    monkeypatch.setattr(app, "_output_backend", lambda: Backend())
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    monkeypatch.setattr(app.state, "last_inserted_text", "John")
    monkeypatch.setattr(app.state, "sent", None)
    assert app._press_key("TAB", 'said "press tab"') is True
    assert pressed == [[((), "TAB")]]
    assert app.state.last_inserted_text == "" and app.state.sent is None


@pytest.mark.parametrize("key", ["TAB", "ENTER"])
def test_a_key_that_couldnt_be_pressed_leaves_undo_and_no_send(monkeypatch, key):
    """The focus never moved and nothing was sent: undo still reaches the
    text, and "edit that" mustn't say it's already sent."""
    class Backend:
        def press_keys(self, chords, claim=None):
            return False
    monkeypatch.setattr(app, "_output_backend", lambda: Backend())
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    monkeypatch.setattr(app.state, "last_inserted_text", "John")
    monkeypatch.setattr(app.state, "sent", None)
    assert app._press_key(key, "test") is False
    assert app.state.last_inserted_text == "John" and app.state.sent is None


def test_both_typing_paths_know_the_tab_key():
    assert app._nav_keycode("TAB") == 15
    assert libei_ctypes._CHORD_KEYS["TAB"] == 15
