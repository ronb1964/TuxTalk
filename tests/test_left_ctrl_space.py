"""Left Ctrl+Space as a dictation key (Discussion #28).

The service listens for Space, and counts it only while Left Ctrl is held:
plain Space is typing, wherever the keys are read. These pin what someone
holding or tapping the pair notices: it dictates, letting go of either key
ends a hold once, a double tap goes hands-free, and nothing TuxTalk types
turns into a Ctrl shortcut or gains a stray space.
"""
from types import SimpleNamespace

import pytest
from evdev import ecodes

from conftest import KeyEvent
from hotkey_loop_fakes import Cfg, Grabbable, Keyboard
from tuxtalk import (ctrl_space, hotkeys, keyboard_grab, keyboard_layout, keyboard_output,
                     modifier_hotkeys, pipeline, recording)
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs
from tuxtalk.hotkey_helper_client import HelperKeyboard
from tuxtalk.hotkey_keys import Hotkeys

SPACE, LCTRL, RCTRL = ecodes.KEY_SPACE, ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL
LALT, LSHIFT = ecodes.KEY_LEFTALT, ecodes.KEY_LEFTSHIFT
F8, F9 = ecodes.KEY_F8, ecodes.KEY_F9
A_KEY = [(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)]


@pytest.fixture(autouse=True)
def _no_pair_down(monkeypatch):
    """Each test starts with the pair up, as the service does."""
    monkeypatch.setattr(hotkeys, "_ctrl_space", ctrl_space.Presses())


@pytest.fixture
def svc():
    return SimpleNamespace(last_activity=0.0, input_device_idx=0)


def feed(event, held, svc, hold=SPACE, toggle=F9, devices=()):
    """One key event through the hotkey loop, as _read_key_events hands it on."""
    hotkeys._on_key_event(event, Hotkeys(hold, toggle, "", None, None), held, list(devices),
                          Cfg(), svc, 1.0)


def ev(code, value, held, svc, hold=SPACE, toggle=F9):
    feed(KeyEvent(code, value), held, svc, hold, toggle)


@pytest.fixture
def hidden(service, monkeypatch):
    """The keys each grab is asked to hide from the desktop."""
    asked = []
    grab = keyboard_grab.grab_keyboards_hiding

    def grab_and_note(devices, codes):
        asked.append(set(codes))
        grab(devices, codes)
    monkeypatch.setattr(keyboard_grab, "grab_keyboards_hiding", grab_and_note)
    return asked


# --- holding and tapping the pair -------------------------------------------------

def test_holding_left_ctrl_then_space_dictates_and_letting_go_types(service, svc):
    held = set()
    ev(LCTRL, 1, held, svc)
    ev(SPACE, 1, held, svc)
    assert service == ["grab", "start"]
    ev(SPACE, 0, held, svc)
    assert service[-2:] == ["let go", "stop"]


def test_letting_go_of_left_ctrl_first_ends_the_dictation_once(service, svc):
    held = set()
    for code, value in ((LCTRL, 1), (SPACE, 1), (LCTRL, 0)):
        ev(code, value, held, svc)
    assert service.count("stop") == 1 and not rs.state.is_recording
    ev(SPACE, 0, held, svc)
    assert service.count("stop") == 1


@pytest.mark.parametrize("hold,toggle", [(SPACE, F9), (F8, SPACE)])
def test_space_alone_is_typing_and_not_activity(service, svc, hold, toggle):
    """Read below the desktop (no hotkey helper), every Space typed reaches
    the loop: it neither dictates nor keeps Power Saving awake."""
    held = set()
    for value in (1, 2, 0):
        ev(SPACE, value, held, svc, hold, toggle)
    assert service == [] and svc.last_activity == 0.0


def test_right_ctrl_space_and_space_then_ctrl_dictate_nothing(service, svc):
    held = set()
    for code, value in ((RCTRL, 1), (SPACE, 1), (SPACE, 0), (RCTRL, 0),
                        (SPACE, 1), (LCTRL, 1), (SPACE, 2), (SPACE, 0), (LCTRL, 0)):
        ev(code, value, held, svc)
    assert service == []


def test_alt_with_the_pair_is_someone_elses_shortcut(service, svc):
    held = set()
    for code, value in ((LALT, 1), (LCTRL, 1), (SPACE, 1), (SPACE, 0)):
        ev(code, value, held, svc)
    assert "start" not in service


def test_the_hold_hides_space_and_left_ctrl(service, svc, hidden):
    """Hidden, the desktop takes Ctrl to be up, so a phrase typed at a pause
    isn't a row of Ctrl shortcuts."""
    held = set()
    ev(LCTRL, 1, held, svc)
    ev(SPACE, 1, held, svc)
    assert hidden == [{SPACE, LCTRL}]


def test_shift_with_the_pair_is_a_voice_edit_when_voice_editing_is_on(service, svc, hidden,
                                                                     monkeypatch):
    monkeypatch.setattr(live, "voice_editing", True)
    held = set()
    for code in (LSHIFT, LCTRL, SPACE):
        ev(code, 1, held, svc)
    assert hidden == [{SPACE, LCTRL, LSHIFT}] and "start" in service


def test_after_a_hold_space_again_needs_left_ctrl_pressed_again(service, svc):
    """The hold told the desktop Left Ctrl went up, so a Space pressed again
    under it reaches the app as a space, and the hotkey helper doesn't
    report it: it mustn't start a dictation as well. Left Ctrl pressed
    again is the pair again."""
    held = set()
    for code, value in ((LCTRL, 1), (SPACE, 1), (SPACE, 0), (SPACE, 1), (SPACE, 0)):
        ev(code, value, held, svc)
    assert service == ["grab", "start", "let go", "stop"]
    for code, value in ((LCTRL, 0), (LCTRL, 1), (SPACE, 1)):
        ev(code, value, held, svc)
    assert service[-2:] == ["grab", "start"]


def test_the_toggle_starts_and_stops_and_hides_nothing(service, svc, monkeypatch):
    monkeypatch.setattr(recording, "toggle_pressed_while_recording", lambda beeps: False)
    held = set()
    for _ in range(2):
        for code, value in ((LCTRL, 1), (SPACE, 1), (SPACE, 0), (LCTRL, 0)):
            ev(code, value, held, svc, hold=F8, toggle=SPACE)
    assert "grab" not in service and service.count("start") == service.count("stop") == 1


def test_a_double_tap_with_left_ctrl_kept_down_goes_hands_free(service, svc, monkeypatch):
    asked = []
    monkeypatch.setattr(recording, "toggle_pressed_while_recording",
                        lambda beeps: asked.append(beeps) or True)
    held = set()
    for code, value in ((LCTRL, 1), (SPACE, 1), (SPACE, 0), (SPACE, 1), (SPACE, 0)):
        ev(code, value, held, svc, hold=F8, toggle=SPACE)
    assert len(asked) == 1 and "stop" not in service and rs.state.is_recording


# --- through the hotkey helper ------------------------------------------------------

def helper(tmp_path):
    """The hotkey helper's stand-in keyboard, not connected to any helper."""
    return HelperKeyboard(str(tmp_path / "none.sock"))


def said(kb, line, held, svc, **keys):
    """The helper says *line*; the loop reads what it turned into."""
    kb._handle(line.split())
    events = list(kb.queue)
    kb.queue.clear()
    for event in events:
        feed(event, held, svc, **keys)


def test_through_the_helper_left_ctrl_up_ends_the_hold(service, svc, tmp_path):
    kb, held = helper(tmp_path), set()
    said(kb, "DOWN 57 29", held, svc)
    said(kb, "UP 29", held, svc)
    assert service == ["grab", "start", "let go", "stop"]
    said(kb, "UP 57", held, svc)
    assert service == ["grab", "start", "let go", "stop"]


def test_through_the_helper_a_space_without_left_ctrl_dictates_nothing(service, svc,
                                                                       tmp_path):
    """Only a helper that knows the pair reports Space, and only under Left
    Ctrl; whatever a helper says, a Space without it is never the key."""
    kb, held = helper(tmp_path), set()
    said(kb, "DOWN 57 -", held, svc)
    said(kb, "UP 57", held, svc)
    assert service == []


def test_the_helper_is_asked_for_space_only_under_left_ctrl(tmp_path):
    """A helper older than 1.14 refuses "L:57" and reports nothing for it:
    the key waits for the update, never falling back to plain Space."""
    kb = helper(tmp_path)
    hotkeys._helper_watch([kb], SPACE, F9, None, None)
    assert "L:57" in kb.specs and "57" not in kb.specs


# --- what typing waits for -----------------------------------------------------------

@pytest.fixture
def watched(monkeypatch):
    """hotkeys._watch for Left Ctrl+Space as the hold key, the desktop and
    the helper left out; the keyboard options are never read."""
    monkeypatch.setattr(modifier_hotkeys, "_noted", ([], frozenset()))
    monkeypatch.setattr(modifier_hotkeys, "_compose_keys", frozenset())
    monkeypatch.setattr(modifier_hotkeys, "_stuck", set())
    monkeypatch.setattr(hotkeys, "_claim_desktop_hotkeys", lambda cfg: None)
    monkeypatch.setattr(hotkeys, "_helper_watch", lambda *a: None)

    def options_read():
        raise AssertionError("the keyboard options were read")
    monkeypatch.setattr(keyboard_layout, "compose_keys", options_read)

    def watch(devices, hold=SPACE, toggle=F9):
        hotkeys._watch(Cfg(), devices, Hotkeys(hold, toggle, "", None, None))
    return watch


@pytest.mark.parametrize("hold,toggle", [(SPACE, F9), (F8, SPACE)])
def test_the_watch_notes_left_ctrl_as_a_dictation_modifier(watched, hold, toggle):
    watched([Keyboard(lambda look: False, code=LCTRL)], hold, toggle)
    assert modifier_hotkeys._noted[1] == {LCTRL}


def test_typing_waits_for_a_left_ctrl_the_desktop_sees(keyboard, watched, monkeypatch):
    """After a toggle's stop tap, Left Ctrl is often still down: what's typed
    then would be Ctrl shortcuts (Ctrl+W closes the tab)."""
    monkeypatch.setattr(modifier_hotkeys.time, "sleep", lambda s: None)
    written_at_look = {}

    def pressed(look):
        written_at_look[look] = len(keyboard.events)
        return look <= 3

    watched([Keyboard(pressed, code=LCTRL)], hold=F8, toggle=SPACE)
    assert keyboard_output.send_keys(A_KEY, what="a")
    assert written_at_look[3] == 0 and keyboard.typed_keys() == [ecodes.KEY_A]


def test_a_left_ctrl_pressed_mid_text_holds_typing_until_it_is_let_go(keyboard, watched,
                                                                      monkeypatch):
    """Pressed to start the next dictation while the last is still typed."""
    monkeypatch.setattr(modifier_hotkeys.time, "sleep", lambda s: None)
    written_at_look = {}
    sent_while_down = []

    def pressed(look):
        written_at_look[look] = len(keyboard.events)
        down = 3 <= look <= 6
        if down:
            sent_while_down.append(len(keyboard.events))
        return down

    watched([Keyboard(pressed, code=LCTRL)])
    assert keyboard_output.send_keys(A_KEY * 4, what="aaaa")
    assert written_at_look[3] == written_at_look[6]
    assert len(set(sent_while_down)) == 1
    assert keyboard.typed_keys() == [ecodes.KEY_A] * 4


def test_through_the_helper_a_left_ctrl_pressed_mid_text_holds_typing(tmp_path, watched):
    kb = helper(tmp_path)
    watched([kb])
    kb._handle(["DOWN", "29", "-"])
    assert modifier_hotkeys.wait_while_held()() is not None
    kb._handle(["UP", "29"])
    assert modifier_hotkeys.wait_while_held()() is None


@pytest.fixture
def holding_the_pair(keyboard, watched, monkeypatch):
    """Left Ctrl+Space held through the real key handler and grab, on one
    keyboard that reads Left Ctrl as down; recording itself is left out."""
    monkeypatch.setattr(hotkeys, "_hotkey_test_active", lambda: False)
    monkeypatch.setattr(keyboard_grab, "grabbed_devices", [])
    monkeypatch.setattr(modifier_hotkeys, "_hidden", (frozenset(), ()))
    monkeypatch.setattr(modifier_hotkeys, "time", keyboard.clock)
    monkeypatch.setattr(recording, "start_recording",
                        lambda *a, **k: setattr(rs.state, "is_recording", True) or rs.state.current)
    monkeypatch.setattr(pipeline, "stop_recording",
                        lambda *a, **k: setattr(rs.state, "is_recording", False))
    board = Grabbable(lambda look: True, code=LCTRL)
    watched([board])
    svc, held = SimpleNamespace(last_activity=0.0, input_device_idx=0), set()
    for code in (LCTRL, SPACE):
        feed(KeyEvent(code, 1), held, svc, devices=[board])
    assert rs.state.is_recording
    yield lambda code, value: feed(KeyEvent(code, value), held, svc, devices=[board])
    keyboard_grab.release_all()
    rs.state.is_recording = False


def test_typing_goes_on_under_a_hidden_left_ctrl(keyboard, holding_the_pair):
    """Type at pause types each phrase while the pair is still held."""
    assert keyboard_output.send_keys(A_KEY, what="a")
    assert keyboard.typed_keys() == [ecodes.KEY_A] and keyboard.clock.now < 1


def test_after_the_hold_typing_waits_for_a_left_ctrl_still_held(keyboard, holding_the_pair):
    """Space let go first: the dictation is typed once Left Ctrl is up too."""
    holding_the_pair(SPACE, 0)
    assert not rs.state.is_recording
    assert keyboard_output.send_keys(A_KEY, what="a") is False
    assert keyboard.events == []


def test_a_stuck_left_ctrl_let_go_and_pressed_again_is_waited_for(keyboard, holding_the_pair,
                                                                  monkeypatch):
    """Once the loop reads its release it isn't stuck: the next typing waits
    for it again rather than giving up at once."""
    holding_the_pair(SPACE, 0)
    keyboard_output.send_keys(A_KEY, what="a")          # gives up: Ctrl stuck down
    board = modifier_hotkeys._noted[0][0]
    holding_the_pair(LCTRL, 0)
    looked = board.looks
    board.pressed = lambda look: look <= looked + 3      # down again, for a moment
    assert keyboard_output.send_keys(A_KEY, what="a")
    assert keyboard.typed_keys() == [ecodes.KEY_A]


def test_a_space_after_the_pair_was_lost_is_typing(service, svc):
    """The keyboard holding the pair went away (or the keys changed): the
    loop forgets the press, and a Space typed after it isn't its release."""
    held = set()
    ev(LCTRL, 1, held, svc)
    ev(SPACE, 1, held, svc)
    held.clear()                        # as the loop does for a keyboard gone
    hotkeys._forget_lost_presses([], Hotkeys(SPACE, F9, "", None, None))
    svc.last_activity = 0.0
    for value in (2, 0):
        ev(SPACE, value, held, svc)
    assert svc.last_activity == 0.0
