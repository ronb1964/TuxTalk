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

from tuxtalk import (app, cosmic_hotkeys, gnome_hotkeys, keyboard_layout, modifier_hotkeys,
                     virtual_keyboard, x11_hotkeys)
from tuxtalk import kwin_hotkeys as kh
from tuxtalk.config import HOTKEY_CHOICES
from tuxtalk.hotkey_keys import keycode_from_name, track_modifier
from tuxtalk.hotkey_labels import hotkey_label, layout_label

RCTRL, RALT, LCTRL = ecodes.KEY_RIGHTCTRL, ecodes.KEY_RIGHTALT, ecodes.KEY_LEFTCTRL
F8 = ecodes.KEY_F8


class Event:
    type = ecodes.EV_KEY

    def __init__(self, code, value):
        self.code, self.value = code, value


class Cfg:
    beeps = notify = smart_quotes = False
    language = None
    auto_space = auto_period = True
    injection_mode = "type"


@pytest.fixture
def service(monkeypatch):
    """The key handler with recording, grabs and typing recorded, not done."""
    done = []
    monkeypatch.setattr(app, "_hotkey_test_active", lambda: False)
    monkeypatch.setattr(app, "_grab_keyboards_hiding", lambda devices, codes: done.append("grab"))
    monkeypatch.setattr(app, "_release_all_grabs", lambda: done.append("let go"))
    monkeypatch.setattr(app, "_send_keys", lambda events, **k: done.append(("keys", events)) or True)

    def start(*a, **k):
        done.append("start")
        app.state.is_recording = True
        return True

    def stop(*a, **k):
        done.append("stop")
        app.state.is_recording = False

    monkeypatch.setattr(app, "start_recording", start)
    monkeypatch.setattr(app, "stop_recording", stop)
    app.state.is_recording = False
    app.state.pending_send = app.state.owed_space = None
    app.state.hidden_key = None
    yield done
    app.state.is_recording = False
    app.state.hidden_key = None


def key(code, value, held, hold=F8, toggle=None):
    """One key event as the main loop delivers it: modifier state first."""
    track_modifier(held, Event(code, value))
    app._handle_key_event(Event(code, value), hold, toggle,
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


def test_right_alt_presses_ctrl_before_hiding_so_no_menu_bar_opens(service):
    """A lone Alt tap puts Firefox's menu bar in focus, and the dictation
    would be typed into it."""
    key(RALT, 1, set(), hold=RALT)
    assert service[:3] == [("keys", [(LCTRL, 1), (LCTRL, 0)]), "grab", "start"]


def test_right_alt_with_another_modifier_held_presses_no_ctrl(service):
    """Left Ctrl+Right Alt isn't dictating, and letting go of the guard's
    Ctrl could let go of the user's."""
    held = set()
    key(LCTRL, 1, held, hold=RALT)
    key(RALT, 1, held, hold=RALT)
    assert not [d for d in service if d[0] == "keys"]


def test_right_ctrl_needs_no_ctrl_press(service):
    key(RCTRL, 1, set(), hold=RCTRL)
    assert not [d for d in service if d[0] == "keys"]


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
    app.state.is_recording = True
    held = set()
    key(RCTRL, 1, held, hold=RCTRL, toggle=ecodes.KEY_F9)
    assert service == ["grab"]
    key(RCTRL, 0, held, hold=RCTRL, toggle=ecodes.KEY_F9)
    assert service == ["grab", "let go", "let go", "stop"]


def test_a_right_ctrl_toggle_holds_a_send_counting_down_when_pressed(service, monkeypatch):
    """Held on its press, like F9: the Enter can't come while Ctrl is down."""
    monkeypatch.setattr(app, "_hold_pending_send", lambda announce: service.append("held") or True)
    held = set()
    key(RCTRL, 1, held, toggle=RCTRL)
    assert service == ["grab", "held", "let go"]     # not left for the backstop to find
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
    assert service[:3] == [("keys", [(LCTRL, 1), (LCTRL, 0)]), "grab", "start"]


def test_the_hotkey_test_sees_a_right_ctrl_toggle_and_nothing_else_happens(service, monkeypatch):
    reported = []
    monkeypatch.setattr(app, "_hotkey_test_active", lambda: True)
    monkeypatch.setattr(app, "_notify_tray_hotkey_pressed", reported.append)
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
    """Typing on the hotkey loop's thread, TuxTalk can't hide a Right Ctrl
    pressed meanwhile: each key waits for it, or the rest of the paragraph
    would arrive as Ctrl shortcuts (Ctrl+W closes the tab)."""
    written_at_look = {}

    def pressed(look):
        written_at_look[look] = len(keyboard.events)
        return 3 <= look <= 6           # pressed before the third key, held a while

    modifier_hotkeys.note([Keyboard(pressed)], RCTRL, ecodes.KEY_F9)
    assert app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)] * 4, what="aaaa")
    assert written_at_look[3] == written_at_look[6] == written_at_look[7] == 4
    assert keyboard.typed_keys() == [ecodes.KEY_A] * 4


def test_typing_that_waits_for_right_alt_doesnt_hold_up_the_menu_guard(keyboard,
                                                                       no_hotkeys_noted):
    """A phrase of Type at pause waits for a Right Alt toggle tapped while it
    types. The hotkey loop's Ctrl that keeps the tap from opening the menu
    bar went out only after the wait, too late: Firefox's menu bar took the
    rest of the phrase as menu letters."""
    import threading
    up = threading.Event()
    modifier_hotkeys.note([Keyboard(lambda look: not up.wait(0.002), code=RALT)],
                          RALT, None)
    typer = threading.Thread(target=app._send_keys, args=([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)],))
    typer.start()
    try:
        guard = threading.Thread(target=app._guard_alt_tap)
        guard.start()
        guard.join(timeout=2)
        assert not guard.is_alive(), "the Ctrl waited for the typing"
        assert keyboard.events == [(LCTRL, 1), (LCTRL, 0)]
    finally:
        up.set()
        typer.join(timeout=2)
    assert keyboard.events[-2:] == [(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)]


def test_a_shift_held_for_a_capital_is_let_go_while_typing_waits(keyboard, no_hotkeys_noted):
    """Others may type while it waits; under our Shift their letters would be capitals."""
    shift = ecodes.KEY_LEFTSHIFT
    modifier_hotkeys.note([Keyboard(lambda look: look in (2, 3))], RCTRL, None)
    assert app._send_keys([(shift, 1), (ecodes.KEY_B, 1), (ecodes.KEY_B, 0), (shift, 0)],
                          what="B")
    assert keyboard.events == [(shift, 1), (shift, 0), (shift, 1), (ecodes.KEY_B, 1),
                               (ecodes.KEY_B, 0), (shift, 0)]


def test_f_keys_never_hold_typing_up(keyboard, no_hotkeys_noted):
    hotkeys = Keyboard(lambda look: True, code=F8)
    modifier_hotkeys.note([hotkeys], F8, ecodes.KEY_F9)
    assert app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert hotkeys.looks == 0


def test_an_unplugged_keyboard_never_holds_typing_up(keyboard, no_hotkeys_noted):
    class Unplugged(Keyboard):
        def active_keys(self):
            raise OSError(19, "No such device")

    modifier_hotkeys.note([Unplugged(lambda look: True)], RCTRL, ecodes.KEY_F9)
    assert app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
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
        assert app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a") is False
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
        app._send_keys(events, what="aB", raise_if_partial=True)
    assert keyboard.typed_keys() == [ecodes.KEY_A, shift]
    assert keyboard.events[-1] == (shift, 0)


def test_while_the_key_stays_stuck_the_next_typing_gives_up_at_once(keyboard, stuck_right_ctrl):
    """A paste that fails and falls back to typing would wait twice."""
    stuck_right_ctrl()
    app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    waited = keyboard.clock.now
    assert app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a") is False
    assert keyboard.clock.now - waited < 1


def test_a_dictation_stopped_by_a_stuck_key_stays_in_recent_dictations(
        keyboard, stuck_right_ctrl, monkeypatch):
    import numpy as np
    from tuxtalk import history
    stuck_right_ctrl()
    kept, notes = [], []
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, language: "hello there")
    monkeypatch.setattr(app, "_determine_injection_method", lambda mode: ("type", "test"))
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: None)
    monkeypatch.setattr(app, "_focused_is_password", lambda: False)
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_notify", lambda title, body: notes.append(body))
    monkeypatch.setattr(app, "_press_enter_after", False)
    monkeypatch.setattr(history, "add_entry", lambda text, replaces=None: kept.append(text))
    frames = [(np.ones(app.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    app._transcribe_and_inject(frames, app.SAMPLE_RATE, beeps_on=False, smart_quotes=False,
                               notify_on=False)
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
    ran = []
    monkeypatch.setattr(app, "_which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: ran.append(a) or 1 / 0)
    monkeypatch.setattr(pyperclip, "copy", lambda text: ran.append(text))
    monkeypatch.setattr(app, "_determine_injection_method", lambda mode: ("type", "test"))
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "kwrite")
    assert app._inject_text("hello there", "type", 0.0) == "key_held"
    assert ran == [] and keyboard.events == []
    assert "Right Ctrl" in app._UNDELIVERED_NOTICES["key_held"]


def test_a_clipboard_fallback_that_fails_says_why_in_the_log(monkeypatch, caplog):
    """With no virtual keyboard and no wtype, the last way out is the
    clipboard; when that failed too, the log said only that nothing worked."""
    import pyperclip
    monkeypatch.setattr(app, "_send_keys", lambda *a, **k: False)
    monkeypatch.setattr(app, "_which", lambda name: name == "wl-copy" and "/usr/bin/wl-copy")
    monkeypatch.setattr(pyperclip, "copy", lambda text: 1 / 0)
    with caplog.at_level("WARNING", logger="tuxtalk.app"):
        assert app._type_text_raw("hello") is False
    assert "clipboard" in caplog.text and "division by zero" in caplog.text


def test_undo_under_a_stuck_key_deletes_nothing_and_says_so(keyboard, stuck_right_ctrl):
    stuck_right_ctrl()
    assert app._send_backspaces(3) is False
    assert keyboard.events == []


@pytest.mark.parametrize("now", [("firefox", False), ("kwrite", True)])
def test_typing_that_waited_stops_if_the_focus_moved_meanwhile(keyboard, no_hotkeys_noted,
                                                              monkeypatch, now):
    """Clicked into another window or a password field while Right Ctrl was
    down: the text was checked for the window it was meant for, not this one."""
    answers = iter([("kwrite", False), now])
    monkeypatch.setattr(app, "_destination", lambda: next(answers))
    modifier_hotkeys.note([Keyboard(lambda look: look <= 3)], RCTRL, None)
    with pytest.raises(virtual_keyboard.GaveUp) as stop:
        app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a", raise_if_partial=True)
    assert stop.value.reason == "moved" and keyboard.events == []
    assert "Recent Dictations" in app._UNDELIVERED_NOTICES["moved"]


def test_typing_that_waited_goes_on_where_it_was_going(keyboard, no_hotkeys_noted, monkeypatch):
    monkeypatch.setattr(app, "_destination", lambda: ("kwrite", False))
    modifier_hotkeys.note([Keyboard(lambda look: look <= 3)], RCTRL, None)
    assert app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert keyboard.typed_keys() == [ecodes.KEY_A]


def test_a_tap_while_a_due_send_waits_for_right_ctrl_still_holds_it(keyboard, no_hotkeys_noted,
                                                                    monkeypatch):
    """The send stays pending while it waits: the tap of a Right Ctrl toggle
    the hotkey loop reads meanwhile holds it. Taken before the wait, the
    tap found nothing to hold and the Enter went out after it."""
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "kwrite")
    held = []

    def pressed(look):
        if look == 2:                   # the hotkey loop reads the tap
            held.append(app._hold_pending_send(announce=True))
        return look <= 3
    modifier_hotkeys.note([Keyboard(pressed)], ecodes.KEY_F8, RCTRL)
    pending = {"window": "kwrite", "why": "test", "space": "", "mode": "type",
               "timer": None, "ticks": []}
    monkeypatch.setattr(app, "_cancel_timers", lambda pending: None)
    app.state.pending_send = pending
    app._send_when_due(pending)
    assert held == [True] and keyboard.events == []


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
    monkeypatch.setattr(app, "_hotkey_test_active", lambda: False)
    monkeypatch.setattr(app, "_grabbed_devices", [])
    monkeypatch.setattr(modifier_hotkeys, "_hidden", frozenset())
    app.state.is_recording = False
    yield
    app._release_all_grabs()
    app.state.is_recording = False


@pytest.fixture
def holding(real_grabs, monkeypatch):
    """Hold *code* as the hold key, through the real key handler and grab,
    on one keyboard; recording itself is left out."""
    monkeypatch.setattr(app, "start_recording",
                        lambda *a, **k: setattr(app.state, "is_recording", True) or True)

    def hold(code, grabs=True):
        board = Grabbable(lambda look: True, code, grabs)
        modifier_hotkeys.note([board], code, ecodes.KEY_F9)
        app._handle_key_event(Event(code, 1), code, ecodes.KEY_F9, None, {code},
                              [board], Cfg(), 0)
        assert app.state.is_recording
    return hold


@pytest.mark.parametrize("code", [RCTRL, RALT])
def test_type_at_pause_types_while_a_hidden_hold_key_is_held(keyboard, holding, code):
    """The recording's grab has told the desktop the key is up, so a phrase
    typed at a pause needn't wait for it; waiting typed nothing until the key
    came up and dropped the phrases after 30 s as a stuck key."""
    holding(code)
    assert app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert keyboard.typed_keys()[-1] == ecodes.KEY_A
    assert keyboard.clock.now < 1


def test_a_hold_key_the_grab_couldnt_hide_still_holds_typing_up(keyboard, holding):
    """The desktop still sees it down: letters typed now would be Ctrl shortcuts."""
    holding(RCTRL, grabs=False)
    assert app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a") is False
    assert keyboard.events == []


def test_letting_go_of_the_grab_stops_hiding_the_key(keyboard, holding):
    holding(RCTRL)
    app._release_all_grabs()
    assert app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a") is False


@pytest.mark.parametrize("code", [RCTRL, RALT])
def test_a_double_tap_of_a_right_ctrl_or_alt_toggle_goes_hands_free(keyboard, real_grabs,
                                                                     monkeypatch, code):
    """The real key handler and recording start, the microphone left out:
    two quick taps start a recording that stops by itself when you go quiet,
    and the second tap's release doesn't stop it."""
    monkeypatch.setattr(app, "_auto_stop_seconds", 2.0)
    monkeypatch.setattr(app, "_open_input_stream", lambda idx: None)
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_notify_tray_recording_state", lambda *a: None)
    monkeypatch.setattr(app, "recording_indicator", None, raising=False)
    stopped = []
    monkeypatch.setattr(app, "stop_recording", lambda *a, **k: stopped.append(a))
    board = Grabbable(lambda look: False, code)
    modifier_hotkeys.note([board], F8, code)
    held = set()
    for value in (1, 0, 1, 0):
        track_modifier(held, Event(code, value))
        app._handle_key_event(Event(code, value), F8, code, None, held, [board],
                              Cfg(), 0)
        assert app.state.is_recording
    assert app.state.silence is not None and stopped == []
    assert app._grabbed_devices == []
    app.state.silence = None


def test_the_right_alt_guard_goes_while_right_alt_is_held(keyboard, no_hotkeys_noted):
    modifier_hotkeys.note([Keyboard(lambda look: True, code=RALT)], RALT, None)
    app._guard_alt_tap()
    assert keyboard.events == [(LCTRL, 1), (LCTRL, 0)]


# --- the desktops hold Scroll Lock back from the focused app ------------------------

def test_scroll_lock_is_claimed_on_every_desktop():
    assert kh.qt_key_sequence("Scroll Lock") == "ScrollLock"
    assert kh.qt_key_sequence("Shift+Scroll Lock") == "Shift+ScrollLock"
    assert kh.qt_key_code("ScrollLock") == 0x01000026         # Qt::Key_ScrollLock
    assert gnome_hotkeys.accelerator("Shift+ScrollLock") == "<Shift>Scroll_Lock"
    assert cosmic_hotkeys.binding("ScrollLock") == ((), "Scroll_Lock")
    assert x11_hotkeys.x_grab("ScrollLock") == (ecodes.KEY_SCROLLLOCK + 8, 0)


@pytest.mark.parametrize("name", ["Right Ctrl", "Right Alt", "Shift+Right Ctrl"])
def test_right_ctrl_and_alt_are_left_to_the_desktop(name):
    """A modifier on its own can't be a desktop shortcut, and types nothing."""
    assert kh.qt_key_sequence(name) is None


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
    window = prefs_window()
    assert window.hotkey_combo.list_mnemonic_labels() == [window.hotkey_label]
    assert window.toggle_combo.list_mnemonic_labels() == [window.toggle_label]


@pytest.mark.parametrize("layout,lost", [(("us", ""), None), (("de", ""), ("@", "€")),
                                         (("gb", ""), ("€",)), (("pl", ""), ("€",))])
def test_right_alt_warns_where_the_layout_makes_it_altgr(monkeypatch, layout, lost):
    """On a German keyboard @ and € are AltGr+Q and AltGr+E; on a UK one @
    has a key of its own."""
    monkeypatch.setattr(keyboard_layout, "active_layout", lambda: layout)
    assert keyboard_layout.altgr_characters() == lost


def test_the_first_run_hotkey_test_says_what_right_alt_gives_up(monkeypatch):
    """Picked there, Right Alt took a German keyboard's @ and € away unsaid."""
    monkeypatch.setattr(keyboard_layout, "active_layout", lambda: ("de", ""))
    assert layout_label("Right Alt") == "Right Alt (your AltGr key: stops typing @ and €)"
    assert layout_label("F8") == "F8"
    monkeypatch.setattr(keyboard_layout, "active_layout", lambda: ("us", ""))
    assert layout_label("Right Alt") == "Right Alt (stops working as Alt)"
