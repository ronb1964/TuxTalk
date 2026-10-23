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

from tuxtalk import app, cosmic_hotkeys, gnome_hotkeys, keyboard_layout, x11_hotkeys
from tuxtalk.hotkey_keys import keycode_from_name, track_modifier
from tuxtalk import kwin_hotkeys as kh
from tuxtalk.config import HOTKEY_CHOICES
from tuxtalk.prefs import hotkey_label

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
    app._handle_key_event(Event(code, value), "hold", hold, toggle,
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
    assert service == ["grab", "held"]
    key(RCTRL, 0, held, toggle=RCTRL)
    assert "start" not in service


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
    monkeypatch.setattr(app, "_modifier_hotkeys", ([], frozenset()))
    monkeypatch.setattr(app.time, "sleep", lambda s: None)


def test_typing_waits_while_right_ctrl_is_pressed_mid_text(keyboard, no_hotkeys_noted):
    """Typing on the hotkey loop's thread, TuxTalk can't hide a Right Ctrl
    pressed meanwhile: each key waits for it, or the rest of the paragraph
    would arrive as Ctrl shortcuts (Ctrl+W closes the tab)."""
    written_at_look = {}

    def pressed(look):
        written_at_look[look] = len(keyboard.events)
        return 3 <= look <= 6           # pressed before the third key, held a while

    app._note_modifier_hotkeys([Keyboard(pressed)], RCTRL, ecodes.KEY_F9)
    assert app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)] * 4, what="aaaa")
    assert written_at_look[3] == written_at_look[6] == written_at_look[7] == 4
    assert keyboard.typed_keys() == [ecodes.KEY_A] * 4


def test_f_keys_never_hold_typing_up(keyboard, no_hotkeys_noted):
    hotkeys = Keyboard(lambda look: True, code=F8)
    app._note_modifier_hotkeys([hotkeys], F8, ecodes.KEY_F9)
    assert app._send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")
    assert hotkeys.looks == 0


def test_the_right_alt_guard_goes_while_right_alt_is_held(keyboard, no_hotkeys_noted):
    app._note_modifier_hotkeys([Keyboard(lambda look: True, code=RALT)], RALT, None)
    app._guard_alt_tap()
    assert keyboard.events == [(LCTRL, 1), (LCTRL, 0)]


# --- the desktops hold Scroll Lock back from the focused app ------------------------

def test_scroll_lock_is_claimed_on_every_desktop():
    assert kh.qt_key_sequence("Scroll Lock") == "ScrollLock"
    assert kh.qt_key_sequence("Shift+Scroll Lock") == "Shift+ScrollLock"
    assert kh.qt_key_code("Scroll Lock") == 0x01000026        # Qt::Key_ScrollLock
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


@pytest.mark.parametrize("layout,lost", [(("us", ""), None), (("de", ""), ("@", "€")),
                                         (("gb", ""), ("€",)), (("pl", ""), ("€",))])
def test_right_alt_warns_where_the_layout_makes_it_altgr(monkeypatch, layout, lost):
    """On a German keyboard @ and € are AltGr+Q and AltGr+E; on a UK one @
    has a key of its own."""
    monkeypatch.setattr(keyboard_layout, "active_layout", lambda: layout)
    assert keyboard_layout.altgr_characters() == lost
