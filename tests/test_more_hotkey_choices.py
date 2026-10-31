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
    monkeypatch.setattr(keyboard_grab, "grab_keyboards_hiding", lambda devices, codes: done.append("grab"))
    monkeypatch.setattr(keyboard_grab, "release_all", lambda: done.append("let go"))
    monkeypatch.setattr(keyboard_output, "send_keys", lambda events, **k: done.append(("keys", events)) or True)
    monkeypatch.setattr(modifier_hotkeys, "guard_alt_tap", lambda: done.append("Ctrl guard"))

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
    monkeypatch.setattr(focused_window, "is_password", lambda: False)
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
    prevent (and put it in a process's arguments); the clipboard fallback
    would overwrite what the user had copied."""
    import subprocess

    import pyperclip
    stuck_right_ctrl()
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")   # KWrite on KDE, as below
    ran = []
    monkeypatch.setattr(keyboard_output, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: ran.append(a) or 1 / 0)
    monkeypatch.setattr(pyperclip, "copy", lambda text: ran.append(text))
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
    stuck_right_ctrl(start=2)
    with caplog.at_level("INFO", logger="tuxtalk.app"):
        assert delivery.inject_text("ab", "type") == "partial"
    assert keyboard.typed_keys() == [ecodes.KEY_A]
    assert "virtual keyboard stopped" not in caplog.text
    assert "Typing stopped partway through the dictation (key_held)" in caplog.text


def test_a_clipboard_fallback_that_fails_says_why_in_the_log(monkeypatch, caplog):
    """With no virtual keyboard and no wtype, the last way out is the
    clipboard; when that failed too, the log said only that nothing worked."""
    import pyperclip
    monkeypatch.setattr(keyboard_output, "send_keys", lambda *a, **k: False)
    monkeypatch.setattr(keyboard_output, "which", lambda name: name == "wl-copy" and "/usr/bin/wl-copy")
    monkeypatch.setattr(pyperclip, "copy", lambda text: 1 / 0)
    with caplog.at_level("WARNING", logger="tuxtalk.app"):
        assert keyboard_output.type_text_raw("hello") is False
    assert "clipboard" in caplog.text and "division by zero" in caplog.text


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
    monkeypatch.setattr(focused_window, "is_password", lambda: False)
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
    monkeypatch.setattr(focused_window, "is_password", lambda: False)
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
    monkeypatch.setattr(focused_window, "is_password", lambda: False)
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
    monkeypatch.setattr(focused_window, "is_password", lambda: False)
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
