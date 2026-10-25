"""Ending a dictation with "press tab" presses the real Tab key (key_commands.py),
except in Claude Desktop, where it types four spaces.

Forms are filled one box at a time ("John, press tab"). A tab character at
the end of the text would be stripped with the trailing space, and pasted
into a web form it stays in the box instead of moving to the next one.
Mid-sentence "press tab" still types a tab character (normalize.py).
"""
import numpy as np
import pytest

from tuxtalk import app, libei_ctypes
from tuxtalk import recording_state as rs
from tuxtalk.key_commands import split_key_command
from tuxtalk import live_settings as live


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
                        lambda text, mode, **kw: events.append(("typed", text)) or "ok")
    monkeypatch.setattr(app, "_press_key",
                        lambda key, why, claim=None: events.append((key, why)) or True)
    frames = [(np.ones(app.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    app._transcribe_and_inject(frames, app.SAMPLE_RATE, beeps_on=False,
                               smart_quotes=False, notify_on=False)
    return events


@pytest.fixture
def no_auto_send(monkeypatch):
    monkeypatch.setattr(live, "press_enter_after", False)


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


@pytest.mark.parametrize("raw, typed", [
    ("John press tab", [("typed", "John"), ("typed", "    ")]),
    ("Press tab.", [("typed", "    ")]),
])
def test_press_tab_types_four_spaces_in_claude_desktop(monkeypatch, no_auto_send, raw, typed):
    """Its Tab key jumps from the message box to the Send button, and the next
    space typed would send the message: four spaces, as a tab said
    mid-sentence there gets."""
    assert _dictate(monkeypatch, raw, window="com.anthropic.Claude") == typed


def test_press_tab_still_presses_the_key_elsewhere(monkeypatch, no_auto_send):
    assert _dictate(monkeypatch, "John press tab", window="org.kde.kwrite") == [
        ("typed", "John"), ("TAB", 'said "press tab"')]


def test_sending_automatically_doesnt_send_a_box_left_with_press_tab(monkeypatch):
    """The user is moving on to the next box, not submitting the form."""
    monkeypatch.setattr(live, "press_enter_after", True)
    assert _dictate(monkeypatch, "John press tab") == [
        ("typed", "John"), ("TAB", 'said "press tab"')]


def test_a_box_ending_on_and_is_not_a_cut_off_dictation(monkeypatch):
    """ "Salt and press tab" was said in full: the next box doesn't carry on
    in lowercase, as it would after a dictation cut off on "and"."""
    monkeypatch.setattr(live, "press_enter_after", True)
    monkeypatch.setattr(rs.state, "continue_mid_sentence", False)
    assert _dictate(monkeypatch, "Salt and press tab") == [
        ("typed", "Salt and"), ("TAB", 'said "press tab"')]
    assert rs.state.continue_mid_sentence is False


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
    monkeypatch.setattr(rs.state, "last_inserted_text", "John")
    monkeypatch.setattr(rs.state, "sent", None)
    assert app._press_key("TAB", 'said "press tab"') is True
    assert pressed == [[((), "TAB")]]
    assert rs.state.last_inserted_text == "" and rs.state.sent is None


@pytest.mark.parametrize("key", ["TAB", "ENTER"])
def test_a_key_that_couldnt_be_pressed_leaves_undo_and_no_send(monkeypatch, key):
    """The focus never moved and nothing was sent: undo still reaches the
    text, and "edit that" mustn't say it's already sent."""
    class Backend:
        def press_keys(self, chords, claim=None):
            return False
    monkeypatch.setattr(app, "_output_backend", lambda: Backend())
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    monkeypatch.setattr(rs.state, "last_inserted_text", "John")
    monkeypatch.setattr(rs.state, "sent", None)
    assert app._press_key(key, "test") is False
    assert rs.state.last_inserted_text == "John" and rs.state.sent is None


def test_both_typing_paths_know_the_tab_key():
    assert app._nav_keycode("TAB") == 15
    assert libei_ctypes._CHORD_KEYS["TAB"] == 15


# --- what Help's "Filling in a form" says, held to what TuxTalk does -------------

@pytest.mark.parametrize("raw, done", [
    # One box: the words with no period, then Tab to the next box.
    ("John Smith, press tab", [("typed", "John Smith"), ("TAB", 'said "press tab"')]),
    # "press enter" ends it like a sentence: a period, then Enter.
    ("Smith press enter", [("typed", "Smith."), ("ENTER", 'said "press enter"')]),
    # "tab" on its own is the word.
    ("open a new tab", [("typed", "Open a new tab. ")]),
])
def test_help_on_forms_says_what_happens(monkeypatch, no_auto_send, raw, done):
    assert _dictate(monkeypatch, raw) == done


def test_help_explains_forms_with_your_own_key(monkeypatch):
    from tuxtalk import config, help_text
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(hotkey="F6"))
    writing = next(text for title, text in help_text.tabs() if "Writing" in title)
    form = writing[writing.index("<b>Filling in a form</b>"):]
    form = form[:form.index("<b>Optional: cleaner text</b>")]
    assert 'Hold <b>F6</b>, say "John Smith, press tab", let go' in form
    assert "F8" not in form
    assert "Claude Desktop" in form and "password box" in form
    assert 'hold\n    <b>F6</b>, say only "press tab", let go' in form


def test_press_tab_alone_in_a_claude_desktop_password_box_presses_the_key(monkeypatch,
                                                                          no_auto_send):
    """Spaces would be refused there, so the Tab is the real key, as in any
    password field."""
    from tuxtalk import focused_window
    monkeypatch.setattr(focused_window, "is_password", lambda: True)
    assert _dictate(monkeypatch, "Press tab.", window="com.anthropic.Claude") == [
        ("TAB", 'said "press tab"')]


def test_four_spaces_kept_back_by_a_stuck_key_say_so(monkeypatch):
    """A real Tab kept back says so; the four spaces Claude Desktop gets
    instead must too, or the box looks as if nothing was heard."""
    from tuxtalk import delivery_notices
    notes = []
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "com.anthropic.Claude")
    monkeypatch.setattr(app, "_inject_text", lambda text, mode, **kw: "key_held")
    monkeypatch.setattr(app, "_notify", lambda title, body: notes.append((title, body)))
    app._press_said("TAB", None)
    assert notes == [delivery_notices.key_not_pressed("TAB")]
