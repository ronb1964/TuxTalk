"""A line break has to be delivered differently in a terminal than in a chat app.

"new line" becomes a marker that paste mode delivers by splitting the text and
sending Shift+Enter between the chunks. That is exactly right for Claude Desktop
and other chat inputs, where a literal newline would SUBMIT the message rather
than break the line.

It is inert in a terminal. Terminals bind no soft-newline key — Enter means "run
this" — so Konsole simply ignores the keystroke and everything lands on one
line, even though "Sending Shift+Enter after part 1" is logged.

Terminals accept the line break the other way round: a newline inside pasted text
is inserted literally, because bracketed paste stops the shell executing at the
first one. So in a terminal the marker becomes a real newline in one paste, and
so it does in KDE's editors, where Shift+Enter is Insert Smart Newline. Everywhere
else the chunk-and-keystroke path is untouched.

This relies on knowing the focused window (on KDE the KWin script reports it);
without that a terminal is indistinguishable from anything else.
"""

import pytest

from tuxtalk import focused_window
from tuxtalk import delivery
from tuxtalk import keyboard_output


@pytest.fixture
def paste_spy(monkeypatch):
    """Capture what reaches the clipboard and how many keystrokes were sent."""
    pasted = []
    breaks = []

    monkeypatch.setattr(keyboard_output, "paste_text", lambda t: pasted.append(t) or True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: breaks.append(1) or True)
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: pasted.append(("typed", t)) or True)
    return pasted, breaks


class TestDeliveryPlan:
    """The decision, isolated from the injection machinery."""

    def test_a_terminal_gets_one_paste_with_a_real_newline(self):
        plan = delivery.plan_line_breaks("first\xa7SHIFT_ENTER\xa7second", one_paste=True)
        assert plan == ["first\nsecond"]

    def test_a_chat_app_gets_chunks_split_for_keystrokes(self):
        plan = delivery.plan_line_breaks("first\xa7SHIFT_ENTER\xa7second", one_paste=False)
        assert plan == ["first", "second"]

    def test_a_blank_line_survives_in_a_terminal(self):
        """"new paragraph" is two markers in a row."""
        text = "a\xa7SHIFT_ENTER\xa7\xa7SHIFT_ENTER\xa7b"
        assert delivery.plan_line_breaks(text, one_paste=True) == ["a\n\nb"]

    def test_a_blank_line_still_becomes_two_keystrokes_elsewhere(self):
        text = "a\xa7SHIFT_ENTER\xa7\xa7SHIFT_ENTER\xa7b"
        assert delivery.plan_line_breaks(text, one_paste=False) == ["a", "", "b"]

    def test_plain_text_is_one_paste_either_way(self):
        assert delivery.plan_line_breaks("hello", one_paste=True) == ["hello"]
        assert delivery.plan_line_breaks("hello", one_paste=False) == ["hello"]

    def test_an_existing_newline_is_kept_as_a_newline_in_a_terminal(self):
        assert delivery.plan_line_breaks("a\nb", one_paste=True) == ["a\nb"]

    def test_an_existing_newline_still_splits_for_a_chat_app(self):
        assert delivery.plan_line_breaks("a\nb", one_paste=False) == ["a", "b"]


class TestNoKeystrokesInATerminal:
    def test_a_terminal_receives_no_shift_enter_at_all(self, paste_spy):
        """The keystroke is inert there; sending it is pure noise."""
        pasted, breaks = paste_spy
        plan = delivery.plan_line_breaks("one\xa7SHIFT_ENTER\xa7two", one_paste=True)

        assert len(plan) == 1, "a terminal must not be split into chunks"
        assert "\n" in plan[0]

    def test_a_chat_app_still_needs_one_break_between_chunks(self):
        plan = delivery.plan_line_breaks("one\xa7SHIFT_ENTER\xa7two", one_paste=False)
        assert len(plan) - 1 == 1


@pytest.mark.parametrize("window", ["org.kde.kwrite", "org.kde.kate"])
def test_new_line_in_kwrite_is_pasted_not_a_shift_enter(window, monkeypatch):
    """KWrite and Kate take Shift+Enter as Insert Smart Newline, which copies
    the symbols the line above starts with: "$45", new line, "$25" came out
    as "$$25". A newline inside the paste is a plain one. A plain Enter
    would not do: in Kate's terminal panel it runs the line."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr(focused_window, "look", lambda: (window, None))
    pasted, keys = [], []
    monkeypatch.setattr(keyboard_output, "paste_text", lambda t: pasted.append(t) or True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: keys.append("shift+enter") or True)

    assert delivery.inject_text("$45.\xa7SHIFT_ENTER\xa7$25. ", "auto") == "ok"
    assert pasted == ["$45.\n$25. "]
    assert keys == []


def test_new_line_typed_into_kwrite_is_a_pasted_newline_too(monkeypatch):
    """Keystroke Typing (or what's typed after a failed paste) still sent
    Shift+Enter there, so "$45", new line, "$25" came out as "$$25"."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(focused_window, "look", lambda: ("org.kde.kwrite", None))
    monkeypatch.setattr(keyboard_output, "is_wayland_session", lambda: True)
    sent = []
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: sent.append(t) or True)
    monkeypatch.setattr(keyboard_output, "paste_text", lambda t: sent.append(("paste", t)) or True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: sent.append("shift+enter") or True)

    assert delivery.inject_text("$45.\xa7SHIFT_ENTER\xa7$25. ", "type") == "ok"
    assert sent == ["$45.", ("paste", "\n"), "$25. "]


def test_new_line_in_a_chat_app_is_still_shift_enter(monkeypatch):
    """A newline pasted into a chat box like Claude Desktop sends the message."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr(focused_window, "look", lambda: ("discord", None))
    pasted, keys = [], []
    monkeypatch.setattr(keyboard_output, "paste_text", lambda t: pasted.append(t) or True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: keys.append("shift+enter") or True)

    assert delivery.inject_text("one\xa7SHIFT_ENTER\xa7two", "auto") == "ok"
    assert pasted == ["one", "two"]
    assert keys == ["shift+enter"]


def test_a_paste_with_line_breaks_asks_which_window_only_before_and_after(monkeypatch):
    """Each question is a D-Bus call to the tray from the thread that types;
    the one made right before the paste already said it's KWrite."""
    looks = []
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr(focused_window, "look",
                        lambda: looks.append(1) or ("org.kde.kwrite", None))
    monkeypatch.setattr(keyboard_output, "paste_text", lambda t: True)
    assert delivery.inject_text("$45.\xa7SHIFT_ENTER\xa7$25. ", "auto") == "ok"
    assert len(looks) == 2
