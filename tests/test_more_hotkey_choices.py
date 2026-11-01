"""Dictation keys that don't need Fn: Right Ctrl, Right Alt and Scroll Lock.

Many laptops and compact keyboards send F1-F12 only with Fn held
(Discussion #18). These pin what someone picking one of the other keys
notices: the key starts and stops dictation, Right Ctrl and Right Alt are
hidden from the desktop before TuxTalk types anything, Right Alt never
opens an app's menu bar, the desktops hold Scroll Lock back from the app,
and Preferences says what each key gives up.
"""
import pytest
from evdev import ecodes

from conftest import KeyEvent
from tuxtalk import (cosmic_hotkeys, delivery_notices, focused_window, gnome_hotkeys,
                     keyboard_layout, modifier_hotkeys, send_countdown, virtual_keyboard,
                     x11_hotkeys)
from tuxtalk import hotkey_claims
from tuxtalk.config import HOTKEY_CHOICES
from tuxtalk.hotkey_keys import keycode_from_name, track_modifier
from tuxtalk.hotkey_labels import hotkey_label, layout_label
from tuxtalk import recording_state as rs
from tuxtalk import live_settings as live
from tuxtalk import keyboard_grab
from tuxtalk import keyboard_output
from tuxtalk import tray_link
from tuxtalk import engine
from tuxtalk import delivery
from tuxtalk import pipeline
from tuxtalk import recording
from tuxtalk import hotkeys

RCTRL, RALT, LCTRL = ecodes.KEY_RIGHTCTRL, ecodes.KEY_RIGHTALT, ecodes.KEY_LEFTCTRL


def named(wm_class):
    """A look at a window of *wm_class* that the desktop names, as KDE and
    GNOME do (automatic sends go only there), one per app."""
    return wm_class, ("tray", f"{{{wm_class}}}")


KWRITE = named("kwrite")
F8 = ecodes.KEY_F8


class Cfg:
    beeps = notify = smart_quotes = False
    language = None
    auto_space = auto_period = True
    injection_mode = "type"


@pytest.fixture
def service(monkeypatch):
    """The key handler with recording, grabs and typing recorded, not done."""
    done = []
    monkeypatch.setattr(hotkeys, "_hotkey_test_active", lambda: False)
    def grab(devices, codes):
        done.append("grab")
        modifier_hotkeys.note_hidden(codes)
        modifier_hotkeys.note_compose_hidden(codes)

    def let_go():
        done.append("let go")
        modifier_hotkeys.note_hidden(())

    monkeypatch.setattr(keyboard_grab, "grab_keyboards_hiding", grab)
    monkeypatch.setattr(keyboard_grab, "release_all", let_go)
    monkeypatch.setattr(modifier_hotkeys, "_hidden", (frozenset(), ()))
    monkeypatch.setattr(keyboard_output, "send_keys", lambda events, **k: done.append(("keys", events)) or True)
    monkeypatch.setattr(modifier_hotkeys, "guard_alt_tap", lambda: done.append("Ctrl guard"))
    monkeypatch.setattr(modifier_hotkeys, "_compose_keys", frozenset())
    monkeypatch.setattr(modifier_hotkeys.virtual_keyboard, "send",
                        lambda events, *a, **k: done.append(("pressed", events)) or True)

    def start(*a, **k):
        done.append("start")
        rs.state.is_recording = True
        return rs.state.current

    def stop(*a, **k):
        done.append("stop")
        rs.state.is_recording = False

    monkeypatch.setattr(recording, "start_recording", start)
    monkeypatch.setattr(pipeline, "stop_recording", stop)
    rs.state.is_recording = False
    send_countdown._pending = send_countdown._owed = None
    rs.state.hidden_key = None
    yield done
    rs.state.is_recording = False
    rs.state.hidden_key = None


def key(code, value, held, hold=F8, toggle=None):
    """One key event as the main loop delivers it: modifier state first."""
    track_modifier(held, KeyEvent(code, value))
    hotkeys._handle_key_event(KeyEvent(code, value), hold, toggle,
                          None, held, [], Cfg(), 0)


# --- the service --------------------------------------------------------------------

@pytest.mark.parametrize("name,code", [("Right Ctrl", RCTRL), ("right alt", RALT),
                                       ("Scroll Lock", ecodes.KEY_SCROLLLOCK), ("F8", F8)])
def test_the_service_listens_for_the_key_picked(name, code):
    assert keycode_from_name(name) == code


def test_pause_is_not_a_dictation_key():
    """Most keyboards send Pause's release as soon as it's pressed: it can't be held."""
    assert "Pause" not in HOTKEY_CHOICES
    assert keycode_from_name("Pause") is None


def test_holding_right_ctrl_dictates_and_letting_go_types(service):
    held = set()
    key(RCTRL, 1, held, hold=RCTRL)
    assert service == ["grab", "start"]
    key(RCTRL, 0, held, hold=RCTRL)
    assert service[-2:] == ["let go", "stop"]


def test_right_ctrl_held_with_left_ctrl_is_someone_elses_shortcut(service):
    held = set()
    key(LCTRL, 1, held, hold=RCTRL)
    key(RCTRL, 1, held, hold=RCTRL)
    assert "start" not in service


def test_letting_go_of_alt_f8_leaves_a_toggle_recording_running(service):
    """Alt+F8 resizes the window (KDE, GNOME): its press is the desktop's,
    so its release mustn't stop what F9 is recording."""
    held = set()
    key(ecodes.KEY_F9, 1, held, toggle=ecodes.KEY_F9)
    key(ecodes.KEY_F9, 0, held, toggle=ecodes.KEY_F9)
    assert service == ["start"]
    key(ecodes.KEY_LEFTALT, 1, held, toggle=ecodes.KEY_F9)
    key(F8, 1, held, toggle=ecodes.KEY_F9)
    key(F8, 0, held, toggle=ecodes.KEY_F9)
    key(ecodes.KEY_LEFTALT, 0, held, toggle=ecodes.KEY_F9)
    assert "stop" not in service and rs.state.is_recording


def test_a_plain_f8_tap_still_stops_a_toggle_recording(service):
    held = set()
    key(ecodes.KEY_F9, 1, held, toggle=ecodes.KEY_F9)
    key(ecodes.KEY_F9, 0, held, toggle=ecodes.KEY_F9)
    key(F8, 1, held, toggle=ecodes.KEY_F9)
    key(F8, 0, held, toggle=ecodes.KEY_F9)
    assert service[-1] == "stop"


def test_right_alt_presses_ctrl_before_hiding_so_no_menu_bar_opens(service):
    """A lone Alt tap puts Firefox's menu bar in focus, and the dictation
    would be typed into it."""
    key(RALT, 1, set(), hold=RALT)
    assert service[:3] == ["Ctrl guard", "grab", "start"]


def test_right_alt_with_another_modifier_held_presses_no_ctrl(service):
    """Left Ctrl+Right Alt isn't dictating, and letting go of the guard's
    Ctrl could let go of the user's."""
    held = set()
    key(LCTRL, 1, held, hold=RALT)
    key(RALT, 1, held, hold=RALT)
    assert "Ctrl guard" not in service


def test_right_ctrl_needs_no_ctrl_press(service):
    key(RCTRL, 1, set(), hold=RCTRL)
    assert "Ctrl guard" not in service


COMPOSE_AGAIN = ("pressed", [(RCTRL, 1), (RCTRL, 0)])


@pytest.fixture
def right_ctrl_is_compose(monkeypatch):
    """Pop!_OS's keyboard options: Right Ctrl is the Compose key."""
    monkeypatch.setattr(modifier_hotkeys, "_compose_keys", frozenset({RCTRL}))
    monkeypatch.setattr(modifier_hotkeys, "_compose_begun", None)
    monkeypatch.setattr(modifier_hotkeys, "_compose_hidden", False)
    monkeypatch.setattr(modifier_hotkeys, "_hidden", (frozenset(), ()))


def test_letting_go_of_a_compose_right_ctrl_presses_compose_again(service, right_ctrl_is_compose):
    """Its press began a compose in the focused app, which swallowed the
    paste's Ctrl+V. A second Compose ends it, once the desktop takes the key
    to be up: a compositor drops a second press of a key it holds down."""
    held = set()
    key(RCTRL, 1, held, hold=RCTRL)
    assert service == ["grab", "start"]
    key(RCTRL, 0, held, hold=RCTRL)
    assert service[2:] == [COMPOSE_AGAIN, "let go", "stop"]


def test_each_tap_of_a_compose_toggle_ends_its_own_compose(service, right_ctrl_is_compose):
    """Also a quick double tap for hands-free: nothing is held back."""
    held = set()
    for _ in range(2):
        key(RCTRL, 1, held, toggle=RCTRL)
        key(RCTRL, 0, held, toggle=RCTRL)
    assert service.count(COMPOSE_AGAIN) == 2
    assert "start" in service and "stop" in service


def test_a_toggle_stop_tap_no_longer_hidden_waits_before_compose_again(
        service, right_ctrl_is_compose, monkeypatch):
    """The stop tap lets go of the grab as it's pressed, so when it comes up
    the desktop may still see it down for a moment: Compose waits first."""
    slept = []
    monkeypatch.setattr(modifier_hotkeys.time, "sleep", slept.append)
    modifier_hotkeys.note_compose_key(KeyEvent(RCTRL, 1), (RCTRL,))
    modifier_hotkeys.note_compose_hidden({RCTRL})   # hidden as it's pressed...
    modifier_hotkeys.note_hidden(())                # ...and let go of as it stops
    modifier_hotkeys.note_compose_key(KeyEvent(RCTRL, 0), (RCTRL,))
    assert slept == [modifier_hotkeys._SETTLE_S] and service == [COMPOSE_AGAIN]


def test_a_compose_key_press_left_to_the_desktop_isnt_ended(service, right_ctrl_is_compose,
                                                           monkeypatch):
    """The hotkey test, a tap that only holds a send, the user's own
    shortcut: nothing hid the press, so it's the desktop's, and through a
    remapper Compose pressed again would come back as the dictation key."""
    monkeypatch.setattr(hotkeys, "_hotkey_test_active", lambda: True)
    monkeypatch.setattr(tray_link, "notify_hotkey_pressed", lambda which: None)
    held = set()
    key(RCTRL, 1, held, toggle=RCTRL)
    key(RCTRL, 0, held, toggle=RCTRL)
    assert COMPOSE_AGAIN not in service and modifier_hotkeys._compose_begun is None


def test_compose_pressed_again_that_didnt_go_out_is_tried_before_the_next_key(
        keyboard, no_hotkeys_noted, right_ctrl_is_compose, monkeypatch):
    modifier_hotkeys.note([Keyboard(lambda look: False)], RCTRL, ecodes.KEY_F9)
    modifier_hotkeys.note_compose_key(KeyEvent(RCTRL, 1), (RCTRL,))
    modifier_hotkeys.note_hidden({RCTRL})
    modifier_hotkeys.note_compose_hidden({RCTRL})
    real_send = modifier_hotkeys.virtual_keyboard.send
    monkeypatch.setattr(modifier_hotkeys.virtual_keyboard, "send", lambda *a, **k: False)
    modifier_hotkeys.end_compose()
    monkeypatch.setattr(modifier_hotkeys.virtual_keyboard, "send", real_send)
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert keyboard.typed_keys() == [RCTRL, ecodes.KEY_A]


def test_compose_pressed_again_that_keeps_failing_is_given_up(
        keyboard, no_hotkeys_noted, right_ctrl_is_compose, monkeypatch):
    """A keyboard that can't be made again mustn't hold typing in a loop."""
    modifier_hotkeys.note([Keyboard(lambda look: False)], RCTRL, ecodes.KEY_F9)
    modifier_hotkeys.note_compose_key(KeyEvent(RCTRL, 1), (RCTRL,))
    modifier_hotkeys.note_hidden({RCTRL})
    modifier_hotkeys.note_compose_hidden({RCTRL})
    tries = []
    monkeypatch.setattr(modifier_hotkeys.virtual_keyboard, "send",
                        lambda *a, **k: tries.append(1) and False)
    modifier_hotkeys.end_compose()
    modifier_hotkeys.end_compose()
    modifier_hotkeys.end_compose()
    assert len(tries) == 2 and modifier_hotkeys._compose_begun is None


def test_typing_waits_for_a_compose_being_ended_on_another_thread(keyboard, no_hotkeys_noted,
                                                                  right_ctrl_is_compose,
                                                                  monkeypatch):
    """The key let go while a phrase types: the phrase's next key goes only
    after Compose pressed again, never into the compose."""
    import threading
    modifier_hotkeys.note([Keyboard(lambda look: False)], RCTRL, ecodes.KEY_F9)
    modifier_hotkeys.note_compose_key(KeyEvent(RCTRL, 1), (RCTRL,))
    modifier_hotkeys.note_hidden({RCTRL})
    modifier_hotkeys.note_compose_hidden({RCTRL})
    sending, go_on = threading.Event(), threading.Event()
    real_send = modifier_hotkeys.virtual_keyboard.send

    def held_up_send(events, *a, **k):
        if threading.current_thread() is ender:     # only Compose pressed again is slow
            sending.set()
            go_on.wait(2)
        return real_send(events, *a, **k)

    monkeypatch.setattr(modifier_hotkeys.virtual_keyboard, "send", held_up_send)
    ender = threading.Thread(target=modifier_hotkeys.end_compose)
    ender.start()
    assert sending.wait(2)
    typer = threading.Thread(target=keyboard_output.send_keys,
                             args=([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)],), kwargs={"what": "a"})
    typer.start()
    typer.join(0.3)
    assert keyboard.typed_keys() == []          # the phrase waits for the compose to end
    go_on.set()
    ender.join(2)
    typer.join(2)
    assert keyboard.typed_keys() == [RCTRL, ecodes.KEY_A]


def test_scroll_lock_is_never_taken_for_compose():
    """A desktop holds Scroll Lock back from apps, so with compose:sclk its
    press begins no compose for TuxTalk to end."""
    modifier_hotkeys.note_compose_keys({ecodes.KEY_SCROLLLOCK, RCTRL})
    try:
        assert modifier_hotkeys._compose_keys == {RCTRL}
    finally:
        modifier_hotkeys.note_compose_keys(())


def test_a_compose_begun_by_a_key_no_longer_dictating_still_ends(service, right_ctrl_is_compose):
    """The dictation key changed while it was held: its release still ends
    the compose its press began."""
    held = set()
    key(RCTRL, 1, held, hold=RCTRL)
    key(RCTRL, 0, held, hold=F8)
    assert COMPOSE_AGAIN in service


def test_an_old_libxkbcommon_means_no_compose_key_not_a_crash(monkeypatch):
    """Before 1.0 a function TuxTalk loads is missing, which raises
    AttributeError; the service reads the Compose keys as it starts."""
    def old():
        raise AttributeError("xkb_keymap_key_get_mods_for_level")
    monkeypatch.setattr(keyboard_layout, "planner_for", lambda names: old())
    monkeypatch.setattr(keyboard_layout, "active_layout", lambda: None)
    assert keyboard_layout.compose_keys() == frozenset()
    assert keyboard_layout.altgr_characters() is None


def test_the_compose_keys_are_read_when_the_keys_are_watched_not_on_each_press(monkeypatch):
    from tuxtalk import hotkey_keys
    asked = []
    monkeypatch.setattr(keyboard_layout, "compose_keys",
                        lambda: asked.append(1) or frozenset({RCTRL}))
    for name in ("_claim_desktop_hotkeys", "_helper_watch"):
        monkeypatch.setattr(hotkeys, name, lambda *a, **k: None)
    monkeypatch.setattr(modifier_hotkeys, "_compose_keys", frozenset())
    keys = hotkey_keys.Hotkeys(RCTRL, ecodes.KEY_F9, "", None, None)
    hotkeys._watch(Cfg(), [], keys)
    assert modifier_hotkeys._compose_keys == {RCTRL} and asked == [1]
    hotkeys._watch(Cfg(), [], hotkey_keys.Hotkeys(F8, ecodes.KEY_F9, "", None, None))
    assert modifier_hotkeys._compose_keys == frozenset() and asked == [1]


def test_a_right_ctrl_that_isnt_compose_presses_nothing_more(service):
    held = set()
    key(RCTRL, 1, held, hold=RCTRL)
    key(RCTRL, 0, held, hold=RCTRL)
    assert COMPOSE_AGAIN not in service


def test_a_compose_key_that_isnt_a_dictation_key_is_left_alone(service, right_ctrl_is_compose):
    held = set()
    key(RCTRL, 1, held, hold=F8)
    key(RCTRL, 0, held, hold=F8)
    assert COMPOSE_AGAIN not in service


def test_a_right_ctrl_toggle_is_hidden_before_anything_is_typed(service):
    """The tap that stops a dictation types it at once: under a Ctrl the
    desktop still saw held, the letters would be shortcuts (Ctrl+W closes
    the tab)."""
    held = set()
    key(RCTRL, 1, held, toggle=RCTRL)
    key(RCTRL, 0, held, toggle=RCTRL)
    assert service == ["grab", "start", "let go"]
    service.clear()
    key(RCTRL, 1, held, toggle=RCTRL)
    assert service.index("grab") < service.index("stop")
    key(RCTRL, 0, held, toggle=RCTRL)
    assert service[-1] == "let go"


def test_a_right_ctrl_hold_key_pressed_during_hands_free_is_hidden(service):
    """Hands-free types each phrase as it goes; a Right Ctrl held then
    would turn it into shortcuts."""
    rs.state.is_recording = True
    held = set()
    key(RCTRL, 1, held, hold=RCTRL, toggle=ecodes.KEY_F9)
    assert service == ["grab"]
    key(RCTRL, 0, held, hold=RCTRL, toggle=ecodes.KEY_F9)
    assert service == ["grab", "let go", "let go", "stop"]


def test_a_right_ctrl_toggle_holds_a_send_counting_down_when_pressed(service, monkeypatch):
    """Held on its press, like F9, and before it's hidden: the Enter waiting
    for the key would go out the moment it was hidden, ahead of the hold."""
    monkeypatch.setattr(send_countdown, "hold", lambda announce: service.append("held") or True)
    held = set()
    key(RCTRL, 1, held, toggle=RCTRL)
    assert service == ["held"]
    key(RCTRL, 0, held, toggle=RCTRL)
    assert "start" not in service


def test_a_right_ctrl_toggle_in_the_users_own_shortcut_stays_shown(service):
    """Left Alt+Right Ctrl+T is Ctrl+Alt+T (a terminal): hidden, the desktop
    saw Alt+T."""
    held = set()
    key(ecodes.KEY_LEFTALT, 1, held, toggle=RCTRL)
    key(RCTRL, 1, held, toggle=RCTRL)
    assert service == []


def test_a_right_alt_toggle_presses_ctrl_then_hides(service):
    key(RALT, 1, set(), toggle=RALT)
    assert service[:3] == ["Ctrl guard", "grab", "start"]


def test_the_hotkey_test_sees_a_right_ctrl_toggle_and_nothing_else_happens(service, monkeypatch):
    reported = []
    monkeypatch.setattr(hotkeys, "_hotkey_test_active", lambda: True)
    monkeypatch.setattr(tray_link, "notify_hotkey_pressed", reported.append)
    held = set()
    key(RCTRL, 1, held, toggle=RCTRL)
    key(RCTRL, 0, held, toggle=RCTRL)
    assert reported == ["toggle"] and service == []


class Keyboard:
    """A real keyboard as the hotkey loop sees it, with Right Ctrl down while
    *pressed(look)* says so; *looks* counts how often TuxTalk checked."""

    def __init__(self, pressed, code=RCTRL):
        self.pressed, self.code, self.looks = pressed, code, 0

    def capabilities(self, verbose=False):
        return {ecodes.EV_KEY: [ecodes.KEY_A, ecodes.KEY_M, ecodes.KEY_Z]}

    def active_keys(self):
        self.looks += 1
        return [self.code] if self.pressed(self.looks) else []


@pytest.fixture
def no_hotkeys_noted(monkeypatch):
    monkeypatch.setattr(modifier_hotkeys, "_noted", ([], frozenset()))
    monkeypatch.setattr(modifier_hotkeys.time, "sleep", lambda s: None)


def test_typing_waits_while_right_ctrl_is_pressed_mid_text(keyboard, no_hotkeys_noted):
    """While text is typed, TuxTalk can't hide a Right Ctrl pressed
    meanwhile: each key waits for it, or the rest of the paragraph would
    arrive as Ctrl shortcuts (Ctrl+W closes the tab)."""
    written_at_look = {}

    def pressed(look):
        written_at_look[look] = len(keyboard.events)
        return 3 <= look <= 6           # pressed before the third key, held a while

    modifier_hotkeys.note([Keyboard(pressed)], RCTRL, ecodes.KEY_F9)
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)] * 4, what="aaaa")
    assert written_at_look[3] == written_at_look[6] == written_at_look[7] == 4
    assert keyboard.typed_keys() == [ecodes.KEY_A] * 4


def test_typing_under_a_held_compose_right_ctrl_ends_the_compose_first(keyboard,
                                                                      no_hotkeys_noted,
                                                                      right_ctrl_is_compose):
    """Type at pause types a phrase while the key is still held, hidden from
    the desktop: Compose pressed again goes before the phrase, and only once."""
    modifier_hotkeys.note([Keyboard(lambda look: False)], RCTRL, ecodes.KEY_F9)
    modifier_hotkeys.note_compose_key(KeyEvent(RCTRL, 1), (RCTRL, ecodes.KEY_F9))
    modifier_hotkeys.note_hidden({RCTRL})
    modifier_hotkeys.note_compose_hidden({RCTRL})
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)] * 2, what="aa")
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    modifier_hotkeys.note_compose_key(KeyEvent(RCTRL, 0), (RCTRL, ecodes.KEY_F9))
    assert keyboard.typed_keys() == [RCTRL] + [ecodes.KEY_A] * 3


def test_typing_that_waits_for_right_alt_doesnt_hold_up_the_menu_guard(keyboard,
                                                                       no_hotkeys_noted):
    """A phrase of Type at pause waits for a Right Alt toggle tapped while it
    types. The hotkey loop's Ctrl that keeps the tap from opening the menu
    bar went out only after the wait, too late: Firefox's menu bar took the
    rest of the phrase as menu letters."""
    import threading
    looked, up = threading.Event(), threading.Event()

    def pressed(look):
        looked.set()        # the first look is made with the keyboard taken
        return not up.wait(0.002)
    modifier_hotkeys.note([Keyboard(pressed, code=RALT)], RALT, None)
    typer = threading.Thread(target=keyboard_output.send_keys, args=([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)],))
    typer.start()
    try:
        assert looked.wait(2)
        guard = threading.Thread(target=modifier_hotkeys.guard_alt_tap)
        guard.start()
        guard.join(timeout=2)
        assert not guard.is_alive(), "the Ctrl waited for the typing"
        assert (LCTRL, 0) in keyboard.events and (ecodes.KEY_A, 1) not in keyboard.events
    finally:
        up.set()
        typer.join(timeout=2)
    assert keyboard.events[-2:] == [(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)]


def test_a_shift_held_for_a_capital_is_let_go_while_typing_waits(keyboard, no_hotkeys_noted):
    """Others may type while it waits; under our Shift their letters would be capitals."""
    shift = ecodes.KEY_LEFTSHIFT
    modifier_hotkeys.note([Keyboard(lambda look: look in (2, 3))], RCTRL, None)
    assert keyboard_output.send_keys([(shift, 1), (ecodes.KEY_B, 1), (ecodes.KEY_B, 0), (shift, 0)],
                          what="B")
    assert keyboard.events == [(shift, 1), (shift, 0), (shift, 1), (ecodes.KEY_B, 1),
                               (ecodes.KEY_B, 0), (shift, 0)]


def test_f_keys_never_hold_typing_up(keyboard, no_hotkeys_noted):
    hotkeys = Keyboard(lambda look: True, code=F8)
    modifier_hotkeys.note([hotkeys], F8, ecodes.KEY_F9)
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert hotkeys.looks == 0


def test_an_unplugged_keyboard_never_holds_typing_up(keyboard, no_hotkeys_noted):
    class Unplugged(Keyboard):
        def active_keys(self):
            raise OSError(19, "No such device")

    modifier_hotkeys.note([Unplugged(lambda look: True)], RCTRL, ecodes.KEY_F9)
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert keyboard.typed_keys() == [ecodes.KEY_A]


@pytest.fixture
def stuck_right_ctrl(keyboard, monkeypatch):
    """Right Ctrl reads as down from the *start*-th look on, as a KVM switch
    or a remapper can leave it, and comes up only after minutes (so typing
    that waits for it fails a test instead of hanging it); the waits run on
    the keyboard's fake clock."""
    monkeypatch.setattr(modifier_hotkeys, "_noted", ([], frozenset()))
    monkeypatch.setattr(modifier_hotkeys, "_stuck", set())
    monkeypatch.setattr(modifier_hotkeys, "time", keyboard.clock)

    def stick(start=1):
        board = Keyboard(lambda look: start <= look < 10_000)
        modifier_hotkeys.note([board], RCTRL, ecodes.KEY_F9)
        return board
    return stick


def test_a_stuck_right_ctrl_stops_typing_after_a_while(keyboard, stuck_right_ctrl, caplog):
    """Typing used to wait for it forever, with nothing in the log."""
    stuck_right_ctrl()
    with caplog.at_level("WARNING", logger="tuxtalk.modifier_hotkeys"):
        assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a") is False
    assert keyboard.events == []
    assert modifier_hotkeys.HELD_LIMIT_S <= keyboard.clock.now < modifier_hotkeys.HELD_LIMIT_S + 1
    assert "Right Ctrl has been down" in caplog.text


def test_a_key_stuck_partway_lets_go_of_shift_and_says_partway(keyboard, stuck_right_ctrl):
    """The first letter went out, so the caller must not type it all again,
    and the Shift held for the capital doesn't stay down."""
    stuck_right_ctrl(start=3)
    shift = ecodes.KEY_LEFTSHIFT
    events = [(ecodes.KEY_A, 1), (ecodes.KEY_A, 0), (shift, 1), (ecodes.KEY_B, 1),
              (ecodes.KEY_B, 0), (shift, 0)]
    with pytest.raises(virtual_keyboard.StoppedPartway):
        keyboard_output.send_keys(events, what="aB", raise_if_partial=True)
    assert keyboard.typed_keys() == [ecodes.KEY_A, shift]
    assert keyboard.events[-1] == (shift, 0)


def test_while_the_key_stays_stuck_the_next_typing_gives_up_at_once(keyboard, stuck_right_ctrl):
    """A paste that fails and falls back to typing would wait twice."""
    stuck_right_ctrl()
    keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    waited = keyboard.clock.now
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a") is False
    assert keyboard.clock.now - waited < 1


def test_a_stuck_key_let_go_and_pressed_again_is_waited_for(keyboard, stuck_right_ctrl,
                                                             monkeypatch):
    """Once the hotkey loop reads its release it isn't stuck: the tap that
    stops the next dictation, still held as typing starts, is waited for
    rather than giving up on the dictation at once."""
    board = stuck_right_ctrl()
    keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    monkeypatch.setattr(hotkeys, "_hotkey_test_active", lambda: False)
    hotkeys._handle_key_event(KeyEvent(RCTRL, 0), F8, RCTRL, None, set(), [board], Cfg(), 0)
    looked = board.looks
    board.pressed = lambda look: look <= looked + 3     # down again, for a moment
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert keyboard.typed_keys() == [ecodes.KEY_A]


def test_a_dictation_stopped_by_a_stuck_key_stays_in_recent_dictations(
        keyboard, stuck_right_ctrl, monkeypatch):
    import numpy as np
    from tuxtalk import history
    stuck_right_ctrl()
    kept, notes = [], []
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: "hello there")
    monkeypatch.setattr(delivery, "determine_injection_method", lambda mode: ("type", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    monkeypatch.setattr(focused_window, "look", lambda: (None, None))
    monkeypatch.setattr(focused_window, "is_password", lambda **k: False)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: notes.append(body))
    monkeypatch.setattr(live, "press_enter_after", False)
    monkeypatch.setattr(history, "add_entry", lambda text, replaces=None: kept.append(text))
    frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    pipeline.transcribe_and_inject(frames, engine.SAMPLE_RATE, beeps_on=False, smart_quotes=False,
                               notify_on=False, message=rs.state.current.message)
    assert keyboard.events == []
    assert kept == ["Hello there. "]
    assert any("Recent Dictations" in note for note in notes)


def test_a_stuck_key_types_nothing_another_way(keyboard, stuck_right_ctrl, monkeypatch):
    """wtype would type the text as the shortcuts the wait was there to
    prevent (and put it in a process's arguments)."""
    import subprocess

    stuck_right_ctrl()
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")   # KWrite on KDE, as below
    ran = []
    monkeypatch.setattr(keyboard_output, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: ran.append(a) or 1 / 0)
    monkeypatch.setattr(delivery, "determine_injection_method", lambda mode: ("type", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "kwrite")
    monkeypatch.setattr(focused_window, "look", lambda: KWRITE)
    assert delivery.inject_text("hello there", "type") == "key_held"
    assert ran == [] and keyboard.events == []
    assert "Right Ctrl" in delivery_notices.NOTICES["key_held"]


@pytest.mark.parametrize("enter", ["said", "sent right away"])
def test_a_stuck_key_that_keeps_back_the_enter_says_so(keyboard, stuck_right_ctrl, monkeypatch,
                                                       enter):
    """"See you at noon, press enter" with Right Ctrl stuck, or "Send each
    dictation automatically" right away: the text is in the box, and only
    the log said the Enter wasn't pressed."""
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    stuck_right_ctrl()
    monkeypatch.setattr(keyboard_output, "output_backend", VirtualKeyboardOutputBackend)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(focused_window, "destination",
                        lambda: focused_window.Destination(*KWRITE, False))
    notes, notices = [], []
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: notes.append((title, body)))
    monkeypatch.setattr(send_countdown, "show_notice", notices.append)
    if enter == "said":
        pipeline.press_said("ENTER", None)
        assert notes == [delivery_notices.key_not_pressed("ENTER")]
        assert notes[0][0] == "Enter not pressed: a dictation key stayed down"
    else:
        monkeypatch.setattr(send_countdown, "_delay", 0.0)
        message = rs.Message(focused_window.Destination(*KWRITE))
        send_countdown.send_automatically("test", message, keyboard_output.press_key, None)
        assert notices == [send_countdown.HELD_NOTICE]
    assert keyboard.events == []


@pytest.fixture
def typing_into_kwrite(monkeypatch):
    monkeypatch.setattr(delivery, "determine_injection_method", lambda mode: ("type", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "kwrite")
    monkeypatch.setattr(focused_window, "look", lambda: KWRITE)
    monkeypatch.setattr("time.sleep", lambda s: None)


def test_a_key_stuck_after_the_first_line_says_only_part_was_typed(keyboard, stuck_right_ctrl,
                                                                   typing_into_kwrite,
                                                                   monkeypatch):
    """The first line is in the app: "Not typed" would have the user paste it
    all again over it. In an editor whose line break is Shift+Enter (KWrite's
    is pasted, through the clipboard)."""
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "xed")
    board = stuck_right_ctrl()
    board.pressed = lambda look: len(keyboard.events) >= 2      # once "a" is typed
    assert delivery.inject_text("a\xa7SHIFT_ENTER\xa7b", "type") == "partial"
    assert keyboard.typed_keys() == [ecodes.KEY_A]


def test_a_key_stuck_partway_isnt_blamed_on_the_keyboard(keyboard, stuck_right_ctrl,
                                                          typing_into_kwrite, caplog):
    stuck_right_ctrl(start=3)       # a dictation's first key is looked at twice (its claim)
    with caplog.at_level("INFO", logger="tuxtalk.app"):
        assert delivery.inject_text("ab", "type") == "partial"
    assert keyboard.typed_keys() == [ecodes.KEY_A]
    assert "virtual keyboard stopped" not in caplog.text
    assert "Typing stopped partway through the dictation (key_held)" in caplog.text


def test_typing_that_cant_go_in_leaves_the_clipboard_alone(monkeypatch):
    """With no virtual keyboard and no wtype, the dictation went on the
    clipboard, over what the user had copied and without the mark that keeps
    it out of clipboard managers. Recent Dictations has it, as the notice says."""
    import subprocess
    import sys
    import types
    ran = []
    # pyperclip, the clipboard library that fallback used, and wl-copy itself.
    monkeypatch.setitem(sys.modules, "pyperclip", types.SimpleNamespace(copy=ran.append))
    monkeypatch.setattr("tuxtalk.keyboard_layout.layout_key_events", lambda text: [(35, 1), (35, 0)])
    monkeypatch.setattr(keyboard_output, "send_keys", lambda *a, **k: False)
    monkeypatch.setattr(keyboard_output, "which", lambda name: name == "wl-copy" and "/usr/bin/wl-copy")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: ran.append(a) or 1 / 0)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a) or 1 / 0)
    assert keyboard_output.type_text_raw("hello") is False
    assert ran == []


def test_undo_under_a_stuck_key_deletes_nothing_and_says_so(keyboard, stuck_right_ctrl):
    stuck_right_ctrl()
    assert keyboard_output.send_backspaces(3) is False
    assert keyboard.events == []


@pytest.mark.parametrize("now", [("firefox", None, False), ("kwrite", None, True)])
def test_typing_that_waited_stops_if_the_focus_moved_meanwhile(keyboard, no_hotkeys_noted,
                                                              monkeypatch, now):
    """Clicked into another window or a password field while Right Ctrl was
    down: the text was checked for the window it was meant for, not this one."""
    answers = iter([("kwrite", None, False), now])
    monkeypatch.setattr(focused_window, "destination", lambda: next(answers))
    modifier_hotkeys.note([Keyboard(lambda look: look <= 3)], RCTRL, None)
    with pytest.raises(virtual_keyboard.GaveUp) as stop:
        keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a", raise_if_partial=True)
    assert stop.value.reason == "moved" and keyboard.events == []
    assert "Recent Dictations" in delivery_notices.NOTICES["moved"]


def test_typing_that_waited_stops_if_another_window_of_the_app_has_the_focus(
        keyboard, no_hotkeys_noted, monkeypatch):
    """Two KWrite windows: the class is the same, the window isn't."""
    windows = iter([("tray", "{4b1e}"), ("tray", "{9c07}")])
    monkeypatch.setattr(focused_window, "look", lambda: ("kwrite", next(windows)))
    monkeypatch.setattr(focused_window, "is_password", lambda **k: False)
    modifier_hotkeys.note([Keyboard(lambda look: look <= 3)], RCTRL, None)
    with pytest.raises(virtual_keyboard.GaveUp) as stop:
        keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a", raise_if_partial=True)
    assert stop.value.reason == "moved" and keyboard.events == []


@pytest.mark.parametrize("now", [(None, ("tray", "{4b1e}"), False), ("kwrite", None, False)])
def test_a_focus_answer_that_didnt_come_isnt_a_move(keyboard, no_hotkeys_noted, monkeypatch,
                                                     now):
    """A D-Bus question that timed out says None: not known, not another window."""
    answers = iter([("kwrite", ("tray", "{4b1e}"), False), now])
    monkeypatch.setattr(focused_window, "destination", lambda: next(answers))
    modifier_hotkeys.note([Keyboard(lambda look: look <= 3)], RCTRL, None)
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert keyboard.typed_keys() == [ecodes.KEY_A]


def test_typing_that_waited_goes_on_where_it_was_going(keyboard, no_hotkeys_noted, monkeypatch):
    monkeypatch.setattr(focused_window, "destination", lambda: ("kwrite", None, False))
    modifier_hotkeys.note([Keyboard(lambda look: look <= 3)], RCTRL, None)
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert keyboard.typed_keys() == [ecodes.KEY_A]


def _send_to_kwrite():
    """A send counting down to Enter in KWrite, as send_countdown keeps it."""
    return {"to": focused_window.Destination(*KWRITE), "why": "test",
            "press": keyboard_output.press_key, "space": "", "mode": "type",
            "timer": None, "ticks": []}


def test_a_tap_while_a_due_send_waits_for_right_ctrl_still_holds_it(keyboard, no_hotkeys_noted,
                                                                    monkeypatch):
    """The send stays pending while it waits: the tap of a Right Ctrl toggle
    the hotkey loop reads meanwhile holds it. Taken before the wait, the
    tap found nothing to hold and the Enter went out after it."""
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "kwrite")
    monkeypatch.setattr(focused_window, "look", lambda: KWRITE)
    held = []

    def pressed(look):
        if look == 2:                   # the hotkey loop reads the tap
            held.append(send_countdown.hold(announce=True))
        return look <= 3
    modifier_hotkeys.note([Keyboard(pressed)], ecodes.KEY_F8, RCTRL)
    pending = _send_to_kwrite()
    monkeypatch.setattr(send_countdown, "_cancel_timers", lambda pending: None)
    send_countdown._pending = pending
    send_countdown._send(pending)
    assert held == [True] and keyboard.events == []


@pytest.fixture
def due_send(keyboard, no_hotkeys_noted, monkeypatch):
    """A send counting down to Enter in KWrite, its timers left out; Right
    Ctrl is the toggle key, down while *pressed(look)* says so. Which window
    has the focus is focused_window.look's, KWrite unless a test says else."""
    monkeypatch.setattr(focused_window, "look", lambda: KWRITE)
    monkeypatch.setattr(send_countdown, "_cancel_timers", lambda pending: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    pending = _send_to_kwrite()
    send_countdown._pending = pending

    def fire(pressed):
        modifier_hotkeys.note([Keyboard(pressed)], ecodes.KEY_F8, RCTRL)
        send_countdown._send(pending)
    yield fire
    send_countdown._pending = None


def test_a_tap_while_the_enter_itself_waits_for_right_ctrl_still_holds_it(keyboard, due_send,
                                                                          monkeypatch):
    """Right Ctrl went down after the first wait, as the Enter was about to
    go: the Enter waits again, and a tap read meanwhile still holds the send.
    Taken before the Enter, the tap found nothing to hold, and Enter went."""
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "kwrite")
    monkeypatch.setattr(focused_window, "look", lambda: KWRITE)
    held = []

    def pressed(look):
        if look == 3:                   # the hotkey loop reads the tap
            held.append(send_countdown.hold(announce=True))
        return look in (2, 3)
    due_send(pressed)
    assert held == [True] and keyboard.events == []


def test_where_the_enter_goes_is_asked_once_it_has_waited(keyboard, due_send, monkeypatch):
    """Clicked into another window while the Enter waited for Right Ctrl:
    asked before the wait, the answer was out of date."""
    focus = ["kwrite"]
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: focus[0])
    monkeypatch.setattr(focused_window, "look", lambda: named(focus[0]))

    def pressed(look):
        if look == 3:
            focus[0] = "firefox"
        return look in (2, 3)
    due_send(pressed)
    assert keyboard.events == [] and send_countdown._pending is None


def test_a_send_whose_wait_ended_in_another_window_says_so(keyboard, due_send, monkeypatch):
    """The Enter waited for Right Ctrl, and the user clicked into Firefox
    meanwhile: nothing is sent, and the notice says why, not "Press Enter
    when you're ready" as for a key that stayed down."""
    focus = ["kwrite"]
    monkeypatch.setattr(focused_window, "look", lambda: named(focus[0]))
    monkeypatch.setattr(focused_window, "destination",
                        lambda: focused_window.Destination(*named(focus[0]), False))
    notices = []
    monkeypatch.setattr(send_countdown, "show_notice", notices.append)

    def pressed(look):
        if look == 4:                   # the wait has asked where the keys go
            focus[0] = "firefox"
        return look in (2, 3, 4)
    due_send(pressed)
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


def test_a_tap_during_the_focus_questions_holds_the_send(keyboard, due_send, monkeypatch):
    """The questions before the Enter (which window, a password field) can
    take a moment; the send stays pending through them, so a tap read
    meanwhile holds it. Taken before them, the Enter went out after the tap."""
    held = []

    def focused():
        held.append(send_countdown.hold(announce=True))
        return "kwrite"
    monkeypatch.setattr("tuxtalk.focused_window.window_class", focused)
    monkeypatch.setattr(focused_window, "look", lambda: named(focused()))
    monkeypatch.setattr(focused_window, "is_password", lambda **k: False)
    due_send(lambda look: False)
    assert held[0] is True and keyboard.events == []


def test_a_tap_while_the_enter_waits_for_the_keyboard_holds_the_send(keyboard, due_send,
                                                                       monkeypatch):
    """Another dictation's typing has the keyboard as the questions end: the
    send is taken only once the Enter has the keyboard, so a tap read while
    it waited still holds it."""
    import threading
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "kwrite")
    monkeypatch.setattr(focused_window, "look", lambda: KWRITE)
    monkeypatch.setattr(focused_window, "is_password", lambda **k: False)
    held, typing, checked = [], threading.Event(), threading.Event()

    def other_typing():
        with virtual_keyboard._lock:
            typing.set()
            assert checked.wait(2)
            held.append(send_countdown.hold(announce=True))

    real_claim = send_countdown._claim

    def claim(pending):
        threading.Thread(target=other_typing).start()
        assert typing.wait(2)
        take = real_claim(pending)
        checked.set()
        return take
    monkeypatch.setattr(send_countdown, "_claim", claim)
    due_send(lambda look: False)
    assert held == [True] and keyboard.events == []


def test_keys_sent_by_another_typing_during_the_checks_stop_the_enter(keyboard, due_send,
                                                                     monkeypatch, caplog):
    """Another dictation, or Paste Last Dictation, typed into the window
    while the checks before the Enter were made, the keyboard let go for
    them. Gone before the Enter took the keyboard back, it left nothing to
    wait for, and the Enter sent the message with that text in it."""
    def kwrite_while_another_types():
        if not keyboard.events:
            virtual_keyboard.send([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)])
        return KWRITE
    monkeypatch.setattr(focused_window, "look", kwrite_while_another_types)
    notices = []
    monkeypatch.setattr(send_countdown, "show_notice", notices.append)
    with caplog.at_level("INFO", logger="tuxtalk.send_countdown"):
        due_send(lambda look: False)
    assert keyboard.typed_keys() == [ecodes.KEY_A]
    assert notices == [send_countdown.HELD_NOTICE] and send_countdown._pending is None
    assert "Not sent: other keys went out while it was checked" in caplog.text


def test_a_right_ctrl_pressed_during_the_focus_questions_makes_the_enter_wait(
        keyboard, due_send, monkeypatch):
    """Pressed while the questions were asked, it was down as the Enter went:
    the desktop got Ctrl+Enter, another command in a browser form or
    LibreOffice."""
    down_from, written_while_down = [], []

    def focused():
        down_from.append(board_looks[0] + 1)
        return "kwrite"

    def pressed(look):
        board_looks[0] = look
        down = bool(down_from) and look < down_from[0] + 3
        if down:
            written_while_down.append(len(keyboard.events))
        return down
    board_looks = [0]
    monkeypatch.setattr("tuxtalk.focused_window.window_class", focused)
    monkeypatch.setattr(focused_window, "look", lambda: named(focused()))
    monkeypatch.setattr(focused_window, "is_password", lambda **k: False)
    due_send(pressed)
    assert written_while_down and set(written_while_down) == {0}
    assert keyboard.typed_keys() == [ecodes.KEY_ENTER]


def test_a_key_stuck_through_a_due_send_holds_it_and_says_so(keyboard, due_send, monkeypatch):
    monkeypatch.setattr(modifier_hotkeys, "time", keyboard.clock)
    monkeypatch.setattr(modifier_hotkeys, "_stuck", set())
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "kwrite")
    monkeypatch.setattr(focused_window, "look", lambda: KWRITE)
    notices = []
    monkeypatch.setattr(send_countdown, "show_notice", notices.append)
    due_send(lambda look: look < 10_000)
    assert keyboard.events == [] and notices == [send_countdown.HELD_NOTICE]
    assert send_countdown._pending is None


def test_a_due_send_with_nothing_held_presses_enter(keyboard, due_send, monkeypatch):
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "kwrite")
    monkeypatch.setattr(focused_window, "look", lambda: KWRITE)
    due_send(lambda look: False)
    assert keyboard.typed_keys() == [ecodes.KEY_ENTER] and send_countdown._pending is None


class Grabbable(Keyboard):
    """A keyboard the hotkey loop can grab, as a hold recording does; with
    *grabs* False the grab fails (another program holds it)."""
    name = path = "keyboard"

    def __init__(self, pressed, code=RCTRL, grabs=True):
        super().__init__(pressed, code)
        self.grabs = grabs

    def write(self, *event):
        pass

    def grab(self):
        if not self.grabs:
            raise OSError(16, "Device or resource busy")

    def ungrab(self):
        pass


@pytest.fixture
def real_grabs(keyboard, stuck_right_ctrl, monkeypatch):
    """The key handler's real grabs, on keyboards a test makes; the waits run
    on the keyboard's fake clock."""
    monkeypatch.setattr(hotkeys, "_hotkey_test_active", lambda: False)
    monkeypatch.setattr(keyboard_grab, "grabbed_devices", [])
    monkeypatch.setattr(modifier_hotkeys, "_hidden", (frozenset(), ()))
    rs.state.is_recording = False
    yield
    keyboard_grab.release_all()
    rs.state.is_recording = False


@pytest.fixture
def holding(real_grabs, monkeypatch):
    """Hold *code* as the hold key, through the real key handler and grab,
    on one keyboard; recording itself is left out."""
    monkeypatch.setattr(recording, "start_recording",
                        lambda *a, **k: setattr(rs.state, "is_recording", True) or rs.state.current)

    def hold(code, grabs=True):
        board = Grabbable(lambda look: True, code, grabs)
        modifier_hotkeys.note([board], code, ecodes.KEY_F9)
        hotkeys._handle_key_event(KeyEvent(code, 1), code, ecodes.KEY_F9, None, {code},
                              [board], Cfg(), 0)
        assert rs.state.is_recording
    return hold


@pytest.mark.parametrize("code", [RCTRL, RALT])
def test_type_at_pause_types_while_a_hidden_hold_key_is_held(keyboard, holding, code):
    """The recording's grab has told the desktop the key is up, so a phrase
    typed at a pause needn't wait for it; waiting typed nothing until the key
    came up and dropped the phrases after 30 s as a stuck key."""
    holding(code)
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert keyboard.typed_keys()[-1] == ecodes.KEY_A
    assert keyboard.clock.now < 1


def test_a_hold_key_the_grab_couldnt_hide_still_holds_typing_up(keyboard, holding):
    """The desktop still sees it down: letters typed now would be Ctrl shortcuts."""
    holding(RCTRL, grabs=False)
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a") is False
    assert keyboard.events == []


def test_letting_go_of_the_grab_stops_hiding_the_key(keyboard, holding):
    holding(RCTRL)
    keyboard_grab.release_all()
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a") is False


@pytest.mark.parametrize("code", [RCTRL, RALT])
def test_a_double_tap_of_a_right_ctrl_or_alt_toggle_goes_hands_free(keyboard, real_grabs,
                                                                     monkeypatch, code):
    """The real key handler and recording start, the microphone left out:
    two quick taps start a recording that stops by itself when you go quiet,
    and the second tap's release doesn't stop it."""
    monkeypatch.setattr(live, "auto_stop_seconds", 2.0)
    monkeypatch.setattr(recording, "open_input_stream", lambda rec, idx: None)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *a: None)
    monkeypatch.setattr(recording, "indicator", None, raising=False)
    stopped = []
    monkeypatch.setattr(pipeline, "stop_recording", lambda *a, **k: stopped.append(a))
    board = Grabbable(lambda look: False, code)
    modifier_hotkeys.note([board], F8, code)
    held = set()
    for value in (1, 0, 1, 0):
        track_modifier(held, KeyEvent(code, value))
        hotkeys._handle_key_event(KeyEvent(code, value), F8, code, None, held, [board],
                              Cfg(), 0)
        assert rs.state.is_recording
    assert rs.state.current.silence is not None and stopped == []
    assert keyboard_grab.grabbed_devices == []
    rs.state.current.silence = None


def test_the_right_alt_guard_goes_while_right_alt_is_held(keyboard, no_hotkeys_noted):
    modifier_hotkeys.note([Keyboard(lambda look: True, code=RALT)], RALT, None)
    modifier_hotkeys.guard_alt_tap()
    assert keyboard.events == [(LCTRL, 1), (LCTRL, 0)]


def test_typing_that_waits_for_right_alt_presses_the_guard_itself(keyboard, no_hotkeys_noted):
    """A Right Alt pressed for the next dictation while a paragraph is
    typed: typing can get to it before the hotkey loop guards it, and the
    desktop saw a lone Alt tap; the rest of the paragraph went into
    Firefox's menu bar as menu letters."""
    seen = []

    def pressed(look):
        seen.append((look <= 4, list(keyboard.events)))
        return look <= 4
    modifier_hotkeys.note([Keyboard(pressed, code=RALT)], RALT, None)
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert keyboard.events == [(LCTRL, 1), (LCTRL, 0), (ecodes.KEY_A, 1), (ecodes.KEY_A, 0)]
    assert any(down and (LCTRL, 0) in events for down, events in seen)


def test_the_guard_goes_before_the_slow_questions_about_the_window(keyboard, no_hotkeys_noted,
                                                                     monkeypatch):
    """Asking which window has the focus, and whether it's a password field,
    can take a moment in LibreOffice or Firefox: a quick tap of Right Alt
    let go meanwhile reached the menu bar before the guard's Ctrl."""
    guarded_when_asked = []

    def destination():
        guarded_when_asked.append((LCTRL, 0) in keyboard.events)
        return ("soffice", None, False)
    monkeypatch.setattr(focused_window, "destination", destination)
    modifier_hotkeys.note([Keyboard(lambda look: look <= 4, code=RALT)], RALT, None)
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert guarded_when_asked and guarded_when_asked[0] is True


def test_typing_waits_for_right_alt_without_a_guard_when_ctrl_is_held_too(keyboard,
                                                                          no_hotkeys_noted):
    """Letting go of the guard's Ctrl could let go of the user's Ctrl too."""
    class BothDown(Keyboard):
        def active_keys(self):
            self.looks += 1
            return [RALT, LCTRL] if self.looks <= 4 else []
    modifier_hotkeys.note([BothDown(None, code=RALT)], RALT, None)
    assert keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert keyboard.events == [(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)]


# --- the desktops hold Scroll Lock back from the focused app ------------------------

def test_scroll_lock_is_claimed_on_every_desktop():
    assert hotkey_claims.qt_key_sequence("Scroll Lock") == "ScrollLock"
    assert hotkey_claims.qt_key_sequence("Shift+Scroll Lock") == "Shift+ScrollLock"
    assert hotkey_claims.qt_key_code("ScrollLock") == 0x01000026         # Qt::Key_ScrollLock
    assert gnome_hotkeys.accelerator("Shift+ScrollLock") == "<Shift>Scroll_Lock"
    assert cosmic_hotkeys.binding("ScrollLock") == ((), "Scroll_Lock")
    assert x11_hotkeys.x_grab("ScrollLock") == (ecodes.KEY_SCROLLLOCK + 8, 0)


@pytest.mark.parametrize("name", ["Right Ctrl", "Right Alt", "Shift+Right Ctrl"])
def test_right_ctrl_and_alt_are_left_to_the_desktop(name):
    """A modifier on its own can't be a desktop shortcut, and types nothing."""
    assert hotkey_claims.qt_key_sequence(name) is None


# --- Preferences says what each key gives up ----------------------------------------

def test_preferences_offers_the_keys_after_f1_to_f12():
    assert list(HOTKEY_CHOICES)[11:] == ["F12", "Right Ctrl", "Right Alt", "Scroll Lock"]


def test_the_dropdowns_say_what_right_ctrl_and_right_alt_give_up():
    assert hotkey_label("Right Ctrl") == "Right Ctrl (stops working as Ctrl)"
    assert hotkey_label("Right Alt") == "Right Alt (stops working as Alt)"
    assert hotkey_label("Right Alt", ("@", "€")) == "Right Alt (your AltGr key: stops typing @ and €)"
    assert hotkey_label("Right Alt", ()) == "Right Alt (your AltGr key)"
    assert hotkey_label("Scroll Lock") == "Scroll Lock"
    assert hotkey_label("Right Ctrl", compose=True) == \
        "Right Ctrl (your Compose key: stops working as Compose)"
    assert hotkey_label("Right Alt", None, compose=True) == \
        "Right Alt (your Compose key: stops working as Compose)"


@pytest.mark.parametrize("options,compose", [
    ("lv3:ralt_switch,compose:rctrl", {RCTRL}),     # every Pop!_OS install
    ("compose:ralt", {RALT}), ("", set())])
def test_the_compose_key_is_found_in_the_keyboard_options(options, compose):
    from tuxtalk.keyboard_layout import KeyPlanner
    assert KeyPlanner("us", "", options).compose_keys & {RCTRL, RALT} == compose


def test_the_dropdown_names_a_right_ctrl_that_is_compose(monkeypatch):
    from tuxtalk import hotkey_labels
    monkeypatch.setattr(keyboard_layout, "compose_keys", lambda: frozenset({RCTRL}))
    monkeypatch.setattr(keyboard_layout, "altgr_characters", lambda: None)
    labels = dict(hotkey_labels.dropdown_entries())
    assert labels["Right Ctrl"] == "Right Ctrl (your Compose key: stops working as Compose)"
    assert labels["Right Alt"] == "Right Alt (stops working as Alt)"
    assert hotkey_labels.layout_label("Right Ctrl") == labels["Right Ctrl"]


def test_a_screen_reader_names_each_hotkey_dropdown_by_its_label(prefs_window):
    """The two dropdowns sat beside their labels with nothing linking them,
    so a screen reader read out two unnamed lists of the same keys."""
    tab = prefs_window().general_tab
    assert tab.hotkey_combo.list_mnemonic_labels() == [tab.hotkey_label]
    assert tab.toggle_combo.list_mnemonic_labels() == [tab.toggle_label]


@pytest.mark.parametrize("layout,lost", [
    (("us", "", ""), None), (("de", "", ""), ("@", "€")), (("gb", "", ""), ("€",)),
    (("pl", "", ""), ("€",)),
    (("us", "", "lv3:ralt_switch"), ()), (("us", "", "lv3:ralt_switch,eurosign:e"), ("€",)),
    (("de", "", "compose:ralt"), None), (("de", "", "lv3:ralt_alt"), None)])
def test_right_alt_warns_where_the_layout_makes_it_altgr(monkeypatch, layout, lost):
    """On a German keyboard @ and € are AltGr+Q and AltGr+E; on a UK one @
    has a key of its own. The desktop's keyboard options count: they can
    make a US Right Alt AltGr, or a German one Compose or plain Alt."""
    monkeypatch.setattr(keyboard_layout, "active_layout", lambda: layout)
    assert keyboard_layout.altgr_characters() == lost


def test_right_alt_warns_about_euro_where_libxkbcommon_names_it_otherwise(monkeypatch):
    """Ubuntu 22.04's libxkbcommon names € 0x13a4 while layouts put 0x20ac
    on their keys, and the warning left € out there."""
    lib = keyboard_layout._lib()
    newer = lib.xkb_utf32_to_keysym
    monkeypatch.setattr(lib, "xkb_utf32_to_keysym",
                        lambda char: 0x13a4 if char == ord("€") else newer(char))
    assert keyboard_layout.KeyPlanner("de").altgr_only("@€") == ["@", "€"]
    assert keyboard_layout.KeyPlanner("gb").altgr_only("@€") == ["€"]


def test_the_first_run_hotkey_test_says_what_right_alt_gives_up(monkeypatch):
    """Picked there, or set already, Right Alt took a German keyboard's @
    and € away unsaid."""
    pytest.importorskip("gi")
    from gi.repository import Gdk, Gtk
    from gtk_text import widgets
    from test_hotkey_test_unreadable_keyboard import _key, _run_dialog
    monkeypatch.setattr(keyboard_layout, "active_layout", lambda: ("de", "", ""))
    seen = {}

    def labels(dialog):
        return [w.get_text() for w in widgets(dialog) if isinstance(w, Gtk.Label)]

    def pick_right_alt(dialog):
        seen["before"] = labels(dialog)
        [w for w in widgets(dialog) if isinstance(w, Gtk.Button)
         and w.get_label() == "Change Key"][0].clicked()
        _key(dialog, Gdk.KEY_Alt_R)
        seen["picked"] = labels(dialog)
    _run_dialog(monkeypatch, ["/dev/input/event3"], pick_right_alt)
    lost = "Right Alt (your AltGr key: stops typing @ and €)"
    assert "Push-to-talk: F8" in seen["before"]
    assert f"Push-to-talk: {lost}" in seen["picked"]

    _run_dialog(monkeypatch, ["/dev/input/event3"],
                lambda dialog: seen.update(set=labels(dialog)), hotkeys=("F8", "Right Alt"))
    assert f"Toggle mode: {lost}" in seen["set"]


def test_right_alt_on_a_us_keyboard_only_stops_being_alt(monkeypatch):
    monkeypatch.setattr(keyboard_layout, "active_layout", lambda: ("us", "", ""))
    assert layout_label("Right Alt") == "Right Alt (stops working as Alt)"
    assert layout_label("F8") == "F8"
