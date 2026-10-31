"""What a dictation that only partly arrives leaves behind.

Two things have to stay true when injection stops partway: nothing may be
typed that the user didn't dictate (a plain Enter in a terminal runs the
half-dictated command), and the undo buffer may never be longer than what
is really in the document, or "undo that" deletes the user's own writing.
"""

import pytest

from tuxtalk import focused_window
from tuxtalk import delivery
from tuxtalk import keyboard_output
from tuxtalk import recording_state as rs
from tuxtalk import virtual_keyboard

MARKER = "\xa7SHIFT_ENTER\xa7"


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    rs.state.last_inserted_text = ""
    yield
    rs.state.last_inserted_text = ""


# --- typing stops at the first failure ---------------------------------------

def test_typing_stops_at_the_first_part_that_fails(monkeypatch, quiet):
    """Typing on after a failed line would run the later lines into the earlier ones."""
    typed = []
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: typed.append(t) or t != "two")
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: True)

    with pytest.raises(virtual_keyboard.StoppedPartway):    # "one" is in: partway
        keyboard_output.type_text(MARKER.join(["one", "two", "three"]))
    assert typed == ["one", "two"]


def test_typing_stops_when_a_line_break_fails(monkeypatch, quiet):
    typed = []
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: typed.append(t) or True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: False)

    with pytest.raises(virtual_keyboard.StoppedPartway):
        keyboard_output.type_text(MARKER.join(["one", "two"]))
    assert typed == ["one"]


def test_a_line_break_that_fails_after_the_first_line_says_only_part_was_typed(
        monkeypatch, quiet):
    """KWrite's line break is a paste, which can fail: the first line was
    in, and "Couldn't type the dictation" had the user paste it all again
    over it."""
    monkeypatch.setattr(focused_window, "look", lambda: ("org.kde.kwrite", None))
    monkeypatch.setattr(keyboard_output, "is_wayland_session", lambda: True)
    typed = []
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: typed.append(t) or True)
    monkeypatch.setattr(keyboard_output, "paste_text", lambda t: False)
    assert delivery.inject_text(MARKER.join(["Dear Sam,", "Thanks"]), "type") == "partial"
    assert typed == ["Dear Sam,"]
    assert rs.state.last_inserted_text == ""        # undo deletes nothing it can't count


def test_a_dictation_starting_with_a_line_break_that_fails_was_not_typed_at_all(
        monkeypatch, quiet):
    """"New line, thanks": the line break failed first, and nothing was in
    the app, but it said only part of the dictation was typed."""
    monkeypatch.setattr(focused_window, "look", lambda: ("org.kde.kwrite", None))
    monkeypatch.setattr(keyboard_output, "is_wayland_session", lambda: True)
    typed = []
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: typed.append(t) or True)
    monkeypatch.setattr(keyboard_output, "paste_text", lambda t: False)
    assert delivery.inject_text(MARKER + "Thanks", "type") == "failed"
    assert typed == []


def test_wtype_isnt_run_into_a_password_field(monkeypatch, quiet):
    """Without TuxTalk's keyboard, wtype types the dictation, and no claim
    of the virtual keyboard's asks about the password field first."""
    from tuxtalk import virtual_keyboard
    ran = []
    monkeypatch.setattr(virtual_keyboard, "_open", lambda: None)
    monkeypatch.setattr(keyboard_output, "which", lambda name: name == "wtype" and "/usr/bin/wtype")
    monkeypatch.setattr(keyboard_output.subprocess, "Popen", lambda *a, **k: ran.append(a) or 1 / 0)
    monkeypatch.setattr("tuxtalk.keyboard_layout.layout_key_events", lambda t: [(30, 1), (30, 0)])
    monkeypatch.setattr(focused_window, "look", lambda: ("org.kde.kwrite", None))
    answers = iter([False])                 # a text box when the dictation was made
    monkeypatch.setattr(focused_window, "is_password", lambda **k: next(answers, True))
    assert delivery.inject_text("swordfish", "type") == "password_field"
    assert ran == []


# --- a terminal's paste falling back to typing --------------------------------

def test_terminal_remainder_never_presses_a_plain_enter(monkeypatch, quiet):
    """A terminal gets one paste with real newlines. If that paste fails, the
    remainder is typed with its line breaks as spaces: Enter would run the
    command, and terminals such as gnome-terminal treat Shift+Enter as Enter."""
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "konsole")
    monkeypatch.setattr(keyboard_output, "paste_text", lambda t: False)
    typed, shift_enters = [], []
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: typed.append(t) or True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: shift_enters.append(1) or True)

    outcome = delivery.inject_text(f"ls{MARKER}foo", "auto")

    assert outcome == "ok"
    assert not any("\n" in t for t in typed), "a plain Enter was typed in a terminal"
    assert shift_enters == [], "Shift+Enter was pressed in a terminal"


def test_auto_mode_types_on_x11(monkeypatch, quiet):
    """Paste can't work without Wayland, so auto goes straight to typing."""
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert delivery.determine_injection_method("auto")[0] == "type"


def test_typed_snippet_newline_is_shift_enter_on_x11(monkeypatch, quiet):
    """A snippet keeps real newlines. Typed as a plain Enter, the first line of
    a signature would send a chat message or run a terminal command."""
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "discord")
    keys = []
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: keys.append(t) or True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: keys.append("SHIFT+ENTER") or True)

    outcome = delivery.inject_text("Best regards,\nRon ", "auto")

    assert outcome == "ok"
    assert keys == ["Best regards,", "SHIFT+ENTER", "Ron "]
    assert rs.state.last_inserted_text == "Best regards,\nRon "


# --- the undo buffer after a partial failure ----------------------------------

def test_failed_typing_empties_the_undo_buffer(monkeypatch, quiet):
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("type", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: False)
    rs.state.last_inserted_text = "the previous dictation"

    assert delivery.inject_text("a dictation that stopped partway", "type") == "failed"
    assert rs.state.last_inserted_text == ""


def test_smart_paste_records_the_line_break_that_landed(monkeypatch, quiet):
    """Part 1 and the break after it land, then part 2's paste fails. The
    buffer must include that break, or an undo leaves a stray blank line."""
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    monkeypatch.setattr(keyboard_output, "paste_text", lambda t: t == "first")
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: True)
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: False)

    outcome = delivery.inject_text(f"first{MARKER}second", "paste")

    assert outcome == "partial"
    assert rs.state.last_inserted_text == "first\n"


def test_smart_paste_leaves_out_a_line_break_that_failed(monkeypatch, quiet):
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    monkeypatch.setattr(keyboard_output, "paste_text", lambda t: True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: False)
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: False)

    delivery.inject_text(f"first{MARKER}second", "paste")

    assert rs.state.last_inserted_text == "first"


def test_typing_into_electron_records_the_line_break_that_landed(monkeypatch, quiet):
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "Claude")
    monkeypatch.setattr(focused_window, "look", lambda: ("Claude", None))
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: t == "first")
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: True)

    outcome = delivery.inject_text(f"first{MARKER}second", "paste")

    assert outcome == "partial"
    assert rs.state.last_inserted_text == "first\n"


def test_an_unexpected_typing_error_forgets_the_dictation_before(monkeypatch, quiet):
    """Some of the new text may be in the app: "undo that" counted back over
    the dictation before it, and deleted that many characters of whatever
    was there."""
    rs.state.last_inserted_text = "See you at noon. "
    monkeypatch.setattr(rs.state, "nav_context", ("kwrite", "See you at noon. "))
    monkeypatch.setattr(rs.state, "continue_mid_sentence", True)
    monkeypatch.setattr(rs.state.last_message, "shown", "See you at noon. ")
    monkeypatch.setattr(focused_window, "destination", lambda: ("kwrite", None, False))
    monkeypatch.setattr(delivery, "deliver_text", lambda *a, **k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        delivery.inject_text("How are you?", "type")
    assert rs.state.last_inserted_text == "" and rs.state.nav_context is None
    assert not rs.state.continue_mid_sentence and rs.state.last_message.shown is None


def test_a_dictation_refused_before_its_first_key_leaves_the_last_one_to_fix(monkeypatch):
    """A Type at pause recording, then a dictation refused for a password
    field before anything was typed: "fix that word" back in the first
    window got only the last phrase, as if the refused one were the last."""
    earlier, refused = rs.Message(), rs.Message()
    earlier.shown = "Hi Sam. We meet at three. "
    monkeypatch.setattr(rs.state, "last_message", earlier)
    monkeypatch.setattr(focused_window, "destination",
                        lambda: focused_window.Destination("kwrite", None, password=True))
    assert delivery.inject_text("swordfish", "type", message=refused) == "password_field"
    assert rs.state.last_message is earlier and earlier.shown == "Hi Sam. We meet at three. "
