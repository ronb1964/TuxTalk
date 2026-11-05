"""What the hotkey loop's tests share (test_more_hotkey_choices.py and
test_left_ctrl_space.py): the key handler with recording, grabs and typing
recorded rather than done, and keyboards that say which keys are down."""
import pytest
from evdev import ecodes

from tuxtalk import (hotkeys, keyboard_grab, keyboard_output, modifier_hotkeys, pipeline,
                     recording, send_countdown)
from tuxtalk import recording_state as rs


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
        modifier_hotkeys.note_compose_hidden(codes, modifier_hotkeys.compose_press())

    def let_go():
        done.append("let go")
        modifier_hotkeys.note_hidden(())

    monkeypatch.setattr(keyboard_grab, "grab_keyboards_hiding", grab)
    monkeypatch.setattr(keyboard_grab, "release_all", let_go)
    monkeypatch.setattr(modifier_hotkeys, "_hidden", (frozenset(), ()))
    monkeypatch.setattr(keyboard_output, "send_keys", lambda events, **k: done.append(("keys", events)) or True)
    monkeypatch.setattr(modifier_hotkeys, "guard_alt_tap", lambda: done.append("Ctrl guard"))
    monkeypatch.setattr(modifier_hotkeys, "_compose_keys", frozenset())
    # A thread of its own could outlive the test's other replacements.
    monkeypatch.setattr(modifier_hotkeys, "_look_later", modifier_hotkeys._look)
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


class Keyboard:
    """A real keyboard as the hotkey loop sees it, with Right Ctrl down while
    *pressed(look)* says so; *looks* counts how often TuxTalk checked."""

    def __init__(self, pressed, code=ecodes.KEY_RIGHTCTRL):
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


class Grabbable(Keyboard):
    """A keyboard the hotkey loop can grab, as a hold recording does; with
    *grabs* False the grab fails (another program holds it)."""
    name = path = "keyboard"

    def __init__(self, pressed, code=ecodes.KEY_RIGHTCTRL, grabs=True):
        super().__init__(pressed, code)
        self.grabs = grabs

    def write(self, *event):
        pass

    def grab(self):
        if not self.grabs:
            raise OSError(16, "Device or resource busy")

    def ungrab(self):
        pass
