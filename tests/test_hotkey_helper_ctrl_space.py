"""Left Ctrl+Space as a dictation key (hotkey helper 1.14): Space is reported
while Left Ctrl is held, and Left Ctrl's own press and release to the client
that watches that pair. No other key is ever reported under Left Ctrl, and
Space never on its own.

Keyboards are pipes, as in test_hotkey_helper.py (hotkey_helper_fakes).
"""
import pytest

from hotkey_helper_fakes import FakeKeyboard, ready_client
from tuxtalk import hotkey_helper as hh

SPACE, LCTRL, RCTRL, LALT, LSHIFT, A = 57, 29, 97, 56, 42, 30
WITH_PAIR = "66 67 1 CA:25 L:57"
WITHOUT_PAIR = "66 67 1 CA:25"


def _ready(connect, say, heard, specs=WITH_PAIR):
    return ready_client(connect, say, heard, specs)


def _tap(keyboard, code):
    keyboard.press(code)
    keyboard.press(code, 0)


# --- what may be watched --------------------------------------------------------------

@pytest.mark.parametrize("spec", ["L:57", "LS:57"])
def test_space_under_left_ctrl_can_be_watched(spec):
    assert hh.parse_spec(spec)[:2] == ("combo", 57)


@pytest.mark.parametrize("spec", [
    "57",       # Space alone is typing
    "C:57",     # either Ctrl: Right Ctrl can be made a third-level key
    "A:57",
    "M:57",
    "S:57",
    "L:30",     # a letter under Left Ctrl
    "L:28",     # Enter
    "L:2",      # a number
    "L:66",     # even an F-key: Left Ctrl names Space and nothing else
    "LC:30",
    "L:29",     # Left Ctrl itself
    "LL:57",
])
def test_left_ctrl_unlocks_space_and_nothing_else(spec):
    with pytest.raises(ValueError):
        hh.parse_spec(spec)


def test_the_helper_says_it_is_1_14():
    assert hh.VERSION == "1.14"


# --- what is reported -----------------------------------------------------------------

def test_left_ctrl_space_is_reported_down_and_up(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(LCTRL)
    kb.press(SPACE)
    kb.press(SPACE, 0)
    kb.press(LCTRL, 0)
    # One UP 29: Left Ctrl was reported on its own and owed as Space's modifier.
    assert heard(sock) == ["DOWN 29 -", "DOWN 57 29", "UP 57", "UP 29"]


def test_left_ctrl_alone_is_reported_to_a_client_watching_the_pair(setup):
    """So TuxTalk can hold it from the desktop while it types, as Right Ctrl."""
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    _tap(kb, LCTRL)
    kb.press(LSHIFT)
    kb.press(LCTRL)
    assert heard(sock) == ["DOWN 29 -", "UP 29", "DOWN 29 42"]


def test_a_client_not_watching_the_pair_never_hears_left_ctrl(setup):
    _helper, kb, connect, say, heard, _seat = setup
    plain = _ready(connect, say, heard, WITHOUT_PAIR)
    pair = _ready(connect, say, heard)
    _tap(kb, LCTRL)
    kb.press(LCTRL)
    _tap(kb, A)
    kb.press(LCTRL, 0)
    assert heard(plain) == []
    assert heard(pair) == ["DOWN 29 -", "UP 29", "DOWN 29 -", "UP 29"]
    say(pair, "WATCH 66")                    # the pair given up again
    assert heard(pair) == ["OK WATCH"]
    _tap(kb, LCTRL)
    kb.press(LCTRL)
    kb.press(SPACE)
    assert heard(pair) == []


def test_left_ctrl_alone_can_never_be_hidden(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(LCTRL)
    assert heard(sock) == ["DOWN 29 -"]
    say(sock, "HIDE 29")
    assert heard(sock) == ["ERR the hotkey isn't held"]
    assert ("grab", True) not in kb.calls and helper.hide is None


def test_space_alone_is_never_reported(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    for _ in range(3):
        _tap(kb, SPACE)
    assert heard(sock) == []


def test_right_ctrl_space_is_never_reported(setup):
    """Right Ctrl can be a Compose or third-level key: Space with it may type."""
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(RCTRL)
    kb.press(SPACE)
    assert heard(sock) == []


def test_typing_with_left_ctrl_held_is_never_reported(setup):
    """Every key but Space stays unreported under Left Ctrl, however many."""
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(LCTRL)
    assert heard(sock) == ["DOWN 29 -"]
    others = [code for code in range(1, hh.KEY_MAX + 1)
              if code not in hh.MODIFIERS | hh.BARE_KEYS | {SPACE}]
    for start in range(0, len(others), 50):     # the pipe holds only so much
        for code in others[start:start + 50]:
            _tap(kb, code)
        assert heard(sock) == []
    kb.press(SPACE)
    assert heard(sock) == ["DOWN 57 29"]
    for code in range(16, 26):
        _tap(kb, code)
    assert heard(sock) == []


def test_space_tapped_twice_under_one_left_ctrl_is_reported_twice(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(LCTRL)
    heard(sock)
    _tap(kb, SPACE)
    _tap(kb, SPACE)
    assert heard(sock) == ["DOWN 57 29", "UP 57", "DOWN 57 29", "UP 57"]


# --- whose Left Ctrl counts -----------------------------------------------------------

def test_a_virtual_keyboards_left_ctrl_cant_unlock_the_real_space(setup):
    helper, kb, connect, say, heard, _seat = setup
    virtual = FakeKeyboard("/dev/input/event-virtual")
    helper.keyboards[virtual.path] = virtual
    sock = _ready(connect, say, heard)
    virtual.press(LCTRL)
    assert heard(sock) == ["DOWN 29 -"]      # its own press, as Right Ctrl's would be
    kb.press(SPACE)
    assert heard(sock) == []


def test_a_writable_real_keyboards_left_ctrl_cant_unlock_space(setup):
    """A real keyboard with "uaccess" is one a program can press Ctrl on."""
    helper, kb, connect, say, heard, _seat = setup
    kb.group = "real"
    writable = FakeKeyboard("/dev/input/event-rgb")       # own:..., not virtual
    helper.keyboards[writable.path] = writable
    sock = _ready(connect, say, heard)
    writable.press(LCTRL)
    heard(sock)
    kb.press(SPACE)
    assert heard(sock) == []


def test_a_left_ctrl_down_before_the_keyboard_was_opened_doesnt_count(setup):
    helper, _kb, connect, say, heard, _seat = setup
    late = FakeKeyboard("/dev/input/event-late")
    late.down = {LCTRL}                   # already held when it appeared
    helper.keyboards[late.path] = late
    sock = _ready(connect, say, heard)
    _tap(late, SPACE)
    assert heard(sock) == []
    late.press(LCTRL, 0)
    late.press(LCTRL)
    late.press(SPACE)
    assert heard(sock) == ["DOWN 29 -", "DOWN 57 29"]


def test_left_ctrl_space_on_the_same_virtual_keyboard_is_reported(setup):
    """A remapper's keyboard (keyd, kanata) holding its own Ctrl."""
    helper, _kb, connect, say, heard, _seat = setup
    virtual = FakeKeyboard("/dev/input/event-virtual")
    helper.keyboards[virtual.path] = virtual
    sock = _ready(connect, say, heard)
    virtual.press(LCTRL)
    virtual.press(SPACE)
    assert heard(sock) == ["DOWN 29 -", "DOWN 57 29"]


# --- hiding it while TuxTalk records --------------------------------------------------

def test_a_held_left_ctrl_space_hides_both_keys(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(LCTRL)
    kb.press(SPACE)
    heard(sock)
    say(sock, "HIDE 57 29")
    assert heard(sock) == ["OK HIDE"]
    # A second injected release would sit in helper.injected and swallow the
    # real one, leaving Left Ctrl down for the helper after it came up.
    assert kb.calls.count(("inject", LCTRL, 0)) == 1
    assert kb.calls.count(("inject", SPACE, 0)) == 1
    assert ("grab", True) in kb.calls
    kb.press(SPACE, 0)
    say(sock, "UNHIDE")
    assert heard(sock) == ["UP 57", "OK UNHIDE"]
    assert helper.hide is None


def test_a_hide_naming_left_ctrl_twice_lets_it_up_once(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(LCTRL)
    kb.press(SPACE)
    heard(sock)
    say(sock, "HIDE 57 29 29")
    assert heard(sock) == ["OK HIDE"]
    assert kb.calls.count(("inject", LCTRL, 0)) == 1
    assert kb.calls.count(("inject", LCTRL, 1)) == 1
