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

    assert keyboard_output.type_text(MARKER.join(["one", "two", "three"])) is False
    assert typed == ["one", "two"]


def test_typing_stops_when_a_line_break_fails(monkeypatch, quiet):
    typed = []
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: typed.append(t) or True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: False)

    assert keyboard_output.type_text(MARKER.join(["one", "two"])) is False
    assert typed == ["one"]


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
    from tuxtalk import send_countdown
    earlier, refused = send_countdown.Message(), send_countdown.Message()
    earlier.shown = "Hi Sam. We meet at three. "
    monkeypatch.setattr(rs.state, "last_message", earlier)
    monkeypatch.setattr(focused_window, "destination",
                        lambda: focused_window.Destination("kwrite", None, password=True))
    assert delivery.inject_text("swordfish", "type", message=refused) == "password_field"
    assert rs.state.last_message is earlier and earlier.shown == "Hi Sam. We meet at three. "
