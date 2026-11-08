"""Left Ctrl+Space as a dictation key (hotkey helper 1.14, and 1.15 for a
Left Ctrl kept down after a hold): Space is reported while Left Ctrl is
held, and Left Ctrl's own press and release to the client that watches that
pair. No other key is ever reported under Left Ctrl, and Space never on its
own.

Keyboards are pipes, as in test_hotkey_helper.py (hotkey_helper_fakes).
"""
import time

import pytest

from hotkey_helper_fakes import FakeKeyboard, ready_client
from tuxtalk import hotkey_helper as hh
from tuxtalk import secure_input

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


def test_the_helper_reports_every_key_tuxtalk_offers():
    """A key needing a newer helper than the one TuxTalk brings could never work."""
    newest = secure_input._version(hh.VERSION)
    assert all(secure_input._version(since) <= newest
               for since in secure_input.KEYS_SINCE.values())


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

def _virtual(helper):
    """A keyboard a program made through uinput, in a group of its own."""
    virtual = FakeKeyboard("/dev/input/event-virtual")
    virtual.group = "virtual:13:99"
    helper.keyboards[virtual.path] = virtual
    return virtual


def test_a_virtual_keyboards_left_ctrl_cant_unlock_the_real_space(setup):
    helper, kb, connect, say, heard, _seat = setup
    kb.group = "real"
    virtual = _virtual(helper)
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
    virtual = _virtual(helper)
    sock = _ready(connect, say, heard)
    virtual.press(LCTRL)
    virtual.press(SPACE)
    assert heard(sock) == ["DOWN 29 -", "DOWN 57 29"]


@pytest.mark.parametrize("specs, ctrl, key", [("L:57", LCTRL, SPACE), ("97 67", RCTRL, 67)])
def test_a_programs_ctrl_let_go_doesnt_say_the_real_one_came_up(setup, specs, ctrl, key):
    """The user holds Ctrl; a program presses its own Ctrl and a hotkey, then
    lets go. The Ctrl reported stays down until the user lets go: an early
    UP would let TuxTalk type while the desktop still holds Ctrl."""
    helper, kb, connect, say, heard, _seat = setup
    kb.group = "real"
    virtual = _virtual(helper)
    sock = _ready(connect, say, heard, specs)
    kb.press(ctrl)
    virtual.press(ctrl)
    _tap(virtual, key)
    virtual.press(ctrl, 0)
    assert heard(sock) == [f"DOWN {ctrl} -", f"DOWN {key} {ctrl}", f"UP {key}"]
    kb.press(ctrl, 0)
    assert heard(sock) == [f"UP {ctrl}"]


def _second_real_keyboard(helper, kb):
    """Another real keyboard beside the setup's, both in the desktop's pool."""
    kb.group = "real"
    other = FakeKeyboard("/dev/input/event-second")
    other.group = "real"
    helper.keyboards[other.path] = other
    return other


def test_left_ctrl_held_on_two_keyboards_is_up_only_when_both_let_go(setup):
    """The desktop sees Ctrl down while either keyboard holds it: an UP at the
    first release would let TuxTalk type Ctrl shortcuts (Ctrl+W)."""
    helper, kb, connect, say, heard, _seat = setup
    other = _second_real_keyboard(helper, kb)
    sock = _ready(connect, say, heard)
    kb.press(LCTRL)
    assert heard(sock) == ["DOWN 29 -"]
    other.press(LCTRL)
    assert heard(sock) == []
    kb.press(LCTRL, 0)
    assert heard(sock) == []
    other.press(LCTRL, 0)
    assert heard(sock) == ["UP 29"]


def test_left_ctrl_kept_on_a_second_keyboard_still_unlocks_space_once_up(setup):
    """After the first keyboard's Ctrl comes up, the pair's state follows the
    second: one UP for the press, when the second lets go."""
    helper, kb, connect, say, heard, _seat = setup
    other = _second_real_keyboard(helper, kb)
    sock = _ready(connect, say, heard)
    said = []
    # One event at a time: the helper reads each keyboard in turn.
    for keyboard, code, value in ((kb, LCTRL, 1), (other, LCTRL, 1), (kb, LCTRL, 0),
                                  (kb, SPACE, 1), (kb, SPACE, 0), (other, LCTRL, 0)):
        keyboard.press(code, value)
        said += heard(sock)
    assert said == ["DOWN 29 -", "DOWN 57 29", "UP 57", "UP 29"]


def test_a_keyboard_given_uaccess_mid_hold_doesnt_say_its_left_ctrl_came_up(setup):
    """Its Ctrl, reported on its own and owed as Space's modifier, is still
    down for the desktop: the UP waits for its release."""
    helper, kb, connect, say, heard, _seat = setup
    kb.group = "real"
    sock = _ready(connect, say, heard)
    kb.press(LCTRL)
    kb.press(SPACE)
    assert heard(sock) == ["DOWN 29 -", "DOWN 57 29"]
    kb.udev_says = "own:/dev/input/event-fake"
    helper.reclassify(kb)
    assert heard(sock) == []
    kb.press(SPACE, 0)
    kb.press(LCTRL, 0)
    assert heard(sock) == ["UP 57", "UP 29"]


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


def test_a_left_ctrl_held_with_f8_is_hidden_with_it(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(LCTRL)
    kb.press(66)
    assert heard(sock) == ["DOWN 29 -", "DOWN 66 29"]
    say(sock, "HIDE 66 29")
    assert heard(sock) == ["OK HIDE"]
    assert kb.calls[:3] == [("inject", 66, 0), ("inject", LCTRL, 0), ("grab", True)]


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


# --- Left Ctrl kept down after a hold -------------------------------------------------

def _hold_the_pair(kb, connect, say, heard):
    """Hold Left Ctrl, then Space, as a hold dictation hidden from the
    desktop; the client's socket, with what the keyboard was asked forgotten."""
    kb.monotonic = True         # its events carry the kernel's time (was_injected)
    sock = _ready(connect, say, heard)
    kb.press(LCTRL)
    kb.press(SPACE)
    heard(sock)
    say(sock, "HIDE 57 29")
    assert heard(sock) == ["OK HIDE"]
    kb.calls.clear()
    return sock


def test_a_left_ctrl_still_held_after_the_hold_is_pressed_again(setup):
    """Space let go first, Ctrl kept down: the hide told the desktop Ctrl
    went up, so without pressing it again Ctrl+Z typed "z"."""
    helper, kb, connect, say, heard, _seat = setup
    sock = _hold_the_pair(kb, connect, say, heard)
    kb.press(SPACE, 0)
    say(sock, "UNHIDE")
    assert heard(sock) == ["UP 57", "OK UNHIDE"]
    assert kb.calls == [("grab", False), ("inject", LCTRL, 0), ("inject", LCTRL, 1)]
    assert LCTRL in kb.kernel and helper.injected == []
    kb.press(LCTRL, 0)                  # and its release is the user's
    assert heard(sock) == ["UP 29"]


def test_the_same_left_ctrl_starts_the_next_left_ctrl_space(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _hold_the_pair(kb, connect, say, heard)
    kb.press(SPACE, 0)
    say(sock, "UNHIDE")
    heard(sock)
    kb.press(SPACE)
    assert heard(sock) == ["DOWN 57 29"]
    say(sock, "HIDE 57 29")
    assert heard(sock) == ["OK HIDE"]


def test_a_left_ctrl_let_go_during_the_hold_isnt_pressed_again(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _hold_the_pair(kb, connect, say, heard)
    kb.press(LCTRL, 0)
    kb.press(SPACE, 0)
    say(sock, "UNHIDE")
    assert heard(sock) == ["UP 29", "UP 57", "OK UNHIDE"]
    assert ("inject", LCTRL, 1) not in kb.calls and LCTRL not in kb.kernel


def test_a_left_ctrl_let_go_as_it_is_pressed_again_is_put_back_up(setup):
    """It comes up after the helper looked and before its "up", which the
    kernel then drops: the "down" after it would leave Ctrl held for the
    desktop, every key after it a Ctrl shortcut."""
    helper, kb, connect, say, heard, _seat = setup
    sock = _hold_the_pair(kb, connect, say, heard)
    real_kernel_down = kb.kernel_down

    def releasing_kernel_down():
        down = real_kernel_down()
        kb.kernel_down = real_kernel_down
        kb.press(LCTRL, 0)              # the user lets go right now
        time.sleep(0.001)               # the kernel stamps it before the helper's events
        return down
    kb.kernel_down = releasing_kernel_down
    kb.press(SPACE, 0)
    assert heard(sock) == ["UP 57", "UP 29"]
    assert LCTRL not in kb.kernel and kb.calls[-1] == ("inject", LCTRL, 0)
    assert helper.injected == []


def test_without_the_kernels_times_left_ctrl_isnt_pressed_again(setup):
    """A release can't be told from the helper's own "up" then."""
    _helper, kb, connect, say, heard, _seat = setup
    sock = _hold_the_pair(kb, connect, say, heard)
    kb.monotonic = False
    kb.press(SPACE, 0)
    say(sock, "UNHIDE")
    assert kb.calls == [("grab", False)]


def test_left_ctrl_is_pressed_again_when_tuxtalk_goes_away_mid_hold(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _hold_the_pair(kb, connect, say, heard)
    sock.close()
    for _ in range(4):
        helper.run_once(timeout=0)
    assert helper.hide is None and helper.clients == {}
    assert kb.calls == [("grab", False), ("inject", LCTRL, 0), ("inject", LCTRL, 1)]


def test_a_left_ctrl_that_cant_be_pressed_again_is_left_up(setup, monkeypatch):
    """The desktop then takes it to be up, as before 1.15: Space under it
    types a space there, so it isn't the pair either."""
    _helper, kb, connect, say, heard, _seat = setup
    sock = _hold_the_pair(kb, connect, say, heard)
    monkeypatch.setattr(kb, "inject", lambda code, *values: False)
    kb.press(SPACE, 0)
    say(sock, "UNHIDE")
    assert heard(sock) == ["UP 57", "OK UNHIDE"]
    kb.press(SPACE)
    assert heard(sock) == []


def test_a_shift_held_for_a_voice_edit_isnt_pressed_again(setup):
    """TuxTalk's Ctrl+C after a voice edit would be Ctrl+Shift+C."""
    _helper, kb, connect, say, heard, _seat = setup
    kb.monotonic = True
    sock = _ready(connect, say, heard, "66")
    kb.press(LSHIFT)
    kb.press(66)
    assert heard(sock) == ["DOWN 66 42"]
    say(sock, "HIDE 66 42")
    assert heard(sock) == ["OK HIDE"]
    kb.press(66, 0)
    say(sock, "UNHIDE")
    assert ("inject", LSHIFT, 1) not in kb.calls[kb.calls.index(("grab", False)):]
