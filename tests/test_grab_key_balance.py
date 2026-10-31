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

from conftest import KeyEvent
from tuxtalk import hotkeys
from tuxtalk import keyboard_grab
from tuxtalk import recording_state as rs
from tuxtalk import keyboard_devices
from tuxtalk import recording
from tuxtalk import pipeline

F8 = ecodes.KEY_F8


class StampedEvent(KeyEvent):
    """A key event with the kernel's time on it, as evdev's InputEvent has."""

    def __init__(self, code, value, at):
        super().__init__(code, value)
        self.at = at

    def timestamp(self):
        return self.at


class KernelKeyboard:
    """One keyboard with two readers: the desktop (libinput) and TuxTalk."""

    def __init__(self):
        self.path, self.name = "/dev/input/event-test", "Test Keyboard"
        self.down = set()
        self.grabbed = False
        self.desktop_count = {}       # libinput: presses minus releases per key
        self.talktype_queue = []

    # -- the kernel ------------------------------------------------------
    def _deliver(self, code, value, at=None):
        if (value == 1) == (code in self.down):
            return                    # no state change: the kernel drops it
        (self.down.add if value == 1 else self.down.discard)(code)
        self.talktype_queue.append(StampedEvent(code, value, time.time() if at is None else at))
        if not self.grabbed:
            self.desktop_count[code] = self.desktop_count.get(code, 0) + (1 if value == 1 else -1)

    def physical(self, code, value, at=None):
        self._deliver(code, value, at)

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
    monkeypatch.setattr(keyboard_devices, "is_keyboard_device", lambda d: True)
    monkeypatch.setattr(recording, "start_recording",
                        lambda *a, **k: setattr(rs.state, "is_recording", True) or rs.state.current)
    stops = []
    monkeypatch.setattr(pipeline, "stop_recording",
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
        hotkeys._handle_key_event(event, F8, None, None, set(), [kb], Cfg(), 0)


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


def test_a_key_let_go_just_after_the_grab_looked_still_ends_the_recording(kb, monkeypatch):
    """F8 comes up between the look at which keys are down and the release
    sent for the desktop: the kernel drops that release, and the real one
    was taken for it. The recording then never ended and the keyboard
    stayed held."""
    looked = kb.active_keys

    def look_then_let_go():
        down = looked()
        kb.physical(F8, 0, at=time.time() - 0.001)   # stamped before our release
        return down
    monkeypatch.setattr(kb, "active_keys", look_then_let_go)
    kb.physical(F8, 1)
    pump(kb)
    assert rs.state.is_recording
    pump(kb)
    pump(kb)
    assert kb.stops == [1] and not rs.state.is_recording and not kb.grabbed
    assert F8 not in kb.down                          # the next press is heard
    assert kb.desktop_count.get(F8, 0) == 0


def test_stale_injections_expire_and_never_swallow_real_keys(kb, monkeypatch):
    sent = time.time() - 3
    keyboard_grab._injected_pending.append((kb.path, F8, 0, sent, sent + 2))   # already expired
    assert keyboard_grab.is_own_injected_event(kb, KeyEvent(F8, 0)) is False


def test_a_failed_write_falls_back_to_the_old_behaviour(kb, monkeypatch):
    def refuse(*a):
        raise PermissionError("no write access")
    monkeypatch.setattr(kb, "write", refuse)
    kb.physical(F8, 1); pump(kb)
    assert rs.state.is_recording
    kb.physical(F8, 0); pump(kb)
    assert kb.stops == [1]
