"""Everyday keys by voice (TuxTalk Labs): "new tab", "reload", "scroll down 3".
The focused window is a stand-in and the keyboard records, so no test presses
a real key; the terminal's own keys are pinned because Ctrl+C there kills the
running program."""
import pytest

from tuxtalk import everyday_keys, focused_window, keyboard_output, screen_message
from tuxtalk.command_phrases import KEY_NAMES, KeyRequest

FIREFOX = ("firefox", ("tray", "1"))
KONSOLE = ("org.kde.konsole", ("tray", "2"))


@pytest.fixture
def at_firefox(monkeypatch):
    monkeypatch.setattr(focused_window, "look", lambda *a, **k: FIREFOX)


@pytest.fixture
def at_konsole(monkeypatch):
    monkeypatch.setattr(focused_window, "look", lambda *a, **k: KONSOLE)


@pytest.fixture
def floated(monkeypatch):
    """The labels floated over the active window, in order."""
    labels = []
    monkeypatch.setattr(screen_message, "show_over_active_window",
                        lambda text, *a, **k: labels.append(text))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    return labels


def test_new_tab_presses_ctrl_t(keyboard, at_firefox):
    assert everyday_keys.press(KeyRequest("new tab"), FIREFOX, False) is True
    assert keyboard.typed_keys() == [29, keyboard_output.nav_keycode("T")]


def test_terminal_tab_commands_use_the_terminals_keys():
    assert everyday_keys.chords_for("new tab", "org.kde.konsole") == [(("CTRL", "SHIFT"), "T")]
    assert everyday_keys.chords_for("next tab", "kitty") == [(("CTRL",), "PAGEDOWN")]


def test_copy_in_a_terminal_never_sends_ctrl_c():
    assert everyday_keys.chords_for("copy", "konsole") == [(("CTRL", "SHIFT"), "C")]
    assert everyday_keys.chords_for("paste", "konsole") == [(("CTRL", "SHIFT"), "V")]


@pytest.mark.parametrize("name", ["select all", "save", "find", "reopen tab"])
def test_some_keys_dont_apply_in_a_terminal(name, keyboard, at_konsole, floated):
    assert everyday_keys.press(KeyRequest(name), KONSOLE, False) is False
    assert keyboard.events == [] and floated == ["Doesn't apply in a terminal"]


UNKNOWN = (None, None)     # GNOME without the extension: no class, no window id


@pytest.fixture
def at_unknown(monkeypatch):
    monkeypatch.setattr(focused_window, "look", lambda *a, **k: UNKNOWN)


@pytest.mark.parametrize("name", ["copy", "paste", "save", "find", "select all", "new tab",
                                  "close tab", "next tab", "previous tab", "reopen tab"])
def test_a_key_a_terminal_has_its_own_of_is_never_guessed(name, keyboard, at_unknown, floated):
    """With the window's class unknown it may be a terminal, where Ctrl+C
    stops the running program and Ctrl+S freezes it: nothing is pressed."""
    assert everyday_keys.press(KeyRequest(name), UNKNOWN, False) is False
    assert keyboard.events == [] and floated == ["Couldn't tell which window"]


@pytest.mark.parametrize("name", ["page down", "go to the top", "escape"])
def test_a_key_the_same_everywhere_works_with_the_class_unknown(name, keyboard, at_unknown):
    assert everyday_keys.press(KeyRequest(name), UNKNOWN, False) is True
    assert keyboard.typed_keys()


def test_scroll_down_three_turns_the_wheel_fifteen_clicks(keyboard, at_firefox):
    everyday_keys.press(KeyRequest("scroll down", 3), FIREFOX, False)
    assert keyboard.wheel == [-1] * 15


def test_go_to_the_top_presses_ctrl_home(keyboard, at_firefox):
    everyday_keys.press(KeyRequest("go to the top"), FIREFOX, False)
    assert keyboard.typed_keys() == [29, 102]


def test_keys_go_nowhere_once_another_window_has_the_focus(keyboard, at_konsole, floated):
    assert everyday_keys.press(KeyRequest("new tab"), FIREFOX, False) is False
    assert keyboard.events == [] and floated == ["Window changed, nothing done"]


def test_every_key_name_has_keys_outside_a_terminal():
    for name in KEY_NAMES - {"scroll up", "scroll down"}:
        assert everyday_keys.chords_for(name, "firefox"), name
