"""Everyday keys by voice (TuxTalk Labs): "new tab", "reload", "scroll down 3".
The focused window is a stand-in and the keyboard records, so no test presses
a real key; the terminal's own keys are pinned because Ctrl+C there kills the
running program."""
import pytest

from desktop_fakes import FakeShell
from tuxtalk import everyday_keys, focused_window, keyboard_output, screen_message
from tuxtalk.command_phrases import KEY_PHRASES, KeyRequest

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


_C, _CS, _A = ("CTRL",), ("CTRL", "SHIFT"), ("ALT",)
# Every key command: its shortcut outside a terminal, and in one (None: it
# doesn't apply there). A terminal's Ctrl+C stops the running program, and
# Alt+Left or F5 leave escape-sequence leftovers on its command line.
SHORTCUTS = {
    "new tab": ((_C, "T"), (_CS, "T")), "close tab": ((_C, "W"), (_CS, "W")),
    "next tab": ((_C, "TAB"), (_C, "PAGEDOWN")),
    "previous tab": ((_CS, "TAB"), (_C, "PAGEUP")), "reopen tab": ((_CS, "T"), None),
    "go back": ((_A, "LEFT"), None), "go forward": ((_A, "RIGHT"), None),
    "reload": (((), "F5"), None),
    "page up": (((), "PAGEUP"),) * 2, "page down": (((), "PAGEDOWN"),) * 2,
    "go to the top": ((_C, "HOME"),) * 2, "go to the bottom": ((_C, "END"),) * 2,
    "find": ((_C, "F"), None), "save": ((_C, "S"), None), "select all": ((_C, "A"), None),
    "copy": ((_C, "C"), (_CS, "C")), "paste": ((_C, "V"), (_CS, "V")),
    "escape": (((), "ESC"),) * 2,
}


@pytest.mark.parametrize("name", sorted(SHORTCUTS))
def test_each_key_command_presses_its_shortcut(name):
    general, terminal = SHORTCUTS[name]
    assert everyday_keys.chords_for(name, "firefox") == [general]
    assert everyday_keys.chords_for(name, "org.kde.konsole") == (
        [terminal] if terminal else None)


def test_every_key_command_has_its_shortcut_pinned():
    assert set(KEY_PHRASES.values()) == set(SHORTCUTS)


@pytest.mark.parametrize("name", ["select all", "save", "find", "reopen tab", "go back",
                                  "go forward", "reload"])
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


@pytest.mark.parametrize("desktop, advice", [("GNOME", "Turn on the TuxTalk GNOME extension"),
                                              ("KDE", "Click the window and try again")])
def test_the_note_says_what_would_tell_the_window(desktop, advice, keyboard, at_unknown,
                                                  floated, monkeypatch):
    """GNOME says which window has the focus only through TuxTalk's
    extension: there, clicking the window wouldn't help."""
    sentences = []
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, text, *a, **k:
                        sentences.append(text))
    everyday_keys.press(KeyRequest("copy"), UNKNOWN, False)
    assert len(sentences) == 1 and advice in sentences[0]


def test_on_gnome_with_the_extension_on_the_note_doesnt_ask_for_it(keyboard, at_unknown,
                                                                   floated, monkeypatch):
    """The extension is running, so turning it on wouldn't help."""
    sentences = []
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, text, *a, **k:
                        sentences.append(text))
    shell = FakeShell()
    try:
        everyday_keys.press(KeyRequest("copy"), UNKNOWN, False)
    finally:
        shell.stop()
    assert len(sentences) == 1 and "extension" not in sentences[0]
    assert "Click the window and try again" in sentences[0]


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
