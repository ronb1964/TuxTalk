"""The desktop must never be left thinking the hotkey is held.

TuxTalk grabs the keyboard after the hotkey goes down, so the desktop
(libinput) sees the press and must also be shown the release. libinput counts
presses minus releases; otherwise F8 stays "held" for the whole session, and a
VM window or remote-desktop client that asks which keys are down auto-repeats
it forever (endless "~" in a terminal).

These tests run hold-to-talk against a small model of the kernel's rules:
- while a device is grabbed, only the grabbing handle gets its events;
- the kernel drops a key event that doesn't change the key's state
  (a release for a key already up, a press for a key already down);
- events written to the device are delivered the same way as real ones.
"""
import time

import pytest
from evdev import ecodes

from tuxtalk import app
from tuxtalk import keyboard_grab
from tuxtalk import recording_state as rs
from tuxtalk import uinput_helper

F8 = ecodes.KEY_F8


class Event:
    def __init__(self, code, value):
        self.type, self.code, self.value = ecodes.EV_KEY, code, value


class KernelKeyboard:
    """One keyboard with two readers: the desktop (libinput) and TuxTalk."""

    def __init__(self):
        self.path, self.name = "/dev/input/event-test", "Test Keyboard"
        self.down = set()
        self.grabbed = False
        self.desktop_count = {}       # libinput: presses minus releases per key
        self.talktype_queue = []

    # -- the kernel ------------------------------------------------------
    def _deliver(self, code, value):
        if (value == 1) == (code in self.down):
            return                    # no state change: the kernel drops it
        (self.down.add if value == 1 else self.down.discard)(code)
        self.talktype_queue.append(Event(code, value))
        if not self.grabbed:
            self.desktop_count[code] = self.desktop_count.get(code, 0) + (1 if value == 1 else -1)

    def physical(self, code, value):
        self._deliver(code, value)

    # -- what python-evdev offers TuxTalk ------------------------------
    def write(self, etype, code, value):
        if etype == ecodes.EV_KEY:
            self._deliver(code, value)

    def active_keys(self):
        return list(self.down)

    def grab(self):
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False

    def read(self):
        q, self.talktype_queue = self.talktype_queue, []
        return q


@pytest.fixture
def kb(monkeypatch):
    dev = KernelKeyboard()
    keyboard_grab._injected_pending.clear()
    keyboard_grab.grabbed_devices.clear()
    monkeypatch.setattr(uinput_helper, "is_keyboard_device", lambda d: True)
    monkeypatch.setattr(app, "start_recording",
                        lambda *a, **k: setattr(rs.state, "is_recording", True) or True)
    stops = []
    monkeypatch.setattr(app, "stop_recording",
                        lambda *a, **k: (stops.append(1), setattr(rs.state, "is_recording", False)))
    dev.stops = stops
    rs.state.is_recording = False
    yield dev
    rs.state.is_recording = False
    keyboard_grab.grabbed_devices.clear()
    keyboard_grab._injected_pending.clear()


class Cfg:
    beeps = smart_quotes = notify = auto_space = auto_period = False
    language, injection_mode = None, "type"


def pump(kb):
    """Run TuxTalk's read loop over whatever the kernel delivered to it."""
    for event in kb.read():
        if keyboard_grab.is_own_injected_event(kb, event):
            continue
        app._handle_key_event(event, F8, None, None, set(), [kb], Cfg(), 0)


def test_hold_to_talk_leaves_the_desktop_balanced(kb):
    kb.physical(F8, 1)
    pump(kb)
    assert rs.state.is_recording and kb.grabbed
    assert kb.desktop_count.get(F8, 0) == 0          # desktop saw press + release

    kb.physical(F8, 0)                                # user lets go (grabbed)
    pump(kb)
    assert kb.stops == [1]                            # TuxTalk still hears the release
    assert not rs.state.is_recording and not kb.grabbed
    assert kb.desktop_count.get(F8, 0) == 0


def test_many_dictations_never_accumulate(kb):
    for _ in range(25):
        kb.physical(F8, 1); pump(kb)
        kb.physical(F8, 0); pump(kb)
    assert len(kb.stops) == 25
    assert kb.desktop_count.get(F8, 0) == 0


def test_the_injected_release_does_not_stop_the_recording(kb):
    kb.physical(F8, 1)
    pump(kb)
    pump(kb)                                          # any leftover self-injected events
    assert rs.state.is_recording and kb.stops == []


def test_a_tap_released_before_the_grab_needs_no_balancing(kb):
    kb.physical(F8, 1)
    kb.physical(F8, 0)                                # both happen before TuxTalk acts
    pump(kb)
    assert kb.desktop_count.get(F8, 0) == 0
    assert keyboard_grab._injected_pending == []                # nothing was injected


def test_without_the_fix_the_desktop_is_left_holding_f8(kb, monkeypatch):
    """A plain grab, without _grab_keyboards_hiding, leaves F8 held."""
    monkeypatch.setattr(keyboard_grab, "grab_keyboards_hiding",
                        lambda devices, codes: keyboard_grab.grab_all_devices(devices))
    kb.physical(F8, 1); pump(kb)
    kb.physical(F8, 0); pump(kb)
    assert kb.desktop_count[F8] == 1                  # stuck "held"


def test_stale_injections_expire_and_never_swallow_real_keys(kb, monkeypatch):
    keyboard_grab._injected_pending.append((kb.path, F8, 0, time.time() - 1))   # already expired
    assert keyboard_grab.is_own_injected_event(kb, Event(F8, 0)) is False


def test_a_failed_write_falls_back_to_the_old_behaviour(kb, monkeypatch):
    def refuse(*a):
        raise PermissionError("no write access")
    monkeypatch.setattr(kb, "write", refuse)
    kb.physical(F8, 1); pump(kb)
    assert rs.state.is_recording
    kb.physical(F8, 0); pump(kb)
    assert kb.stops == [1]
