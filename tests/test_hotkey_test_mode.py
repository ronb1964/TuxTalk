"""Hotkey test mode must never be able to leave dictation switched off.

A blind SIGUSR2 toggle (one signal in from Preferences, one out) can't promise
that: if Preferences crashes with the Test Hotkeys dialog open, the service
stays in test mode for good, and if the service restarts meanwhile, the "off"
signal puts the NEW one into test mode. Either way every hotkey is silently
ignored.

So Preferences keeps a flag file fresh while the dialog is open, and the
service ignores one that has gone stale.
"""

import os
import signal
import time

import pytest
from evdev import ecodes

from tuxtalk import app, service_launcher
from tuxtalk import recording_state as rs
from tuxtalk import keyboard_grab
from tuxtalk import tray_link


class FakeEvent:
    type = ecodes.EV_KEY

    def __init__(self, code, value):
        self.code = code
        self.value = value


class FakeConfig:
    beeps = notify = smart_quotes = False
    language = None
    auto_space = auto_period = True
    injection_mode = "type"


HOLD = ecodes.KEY_F8
TOGGLE = ecodes.KEY_F9


@pytest.fixture
def keys(monkeypatch):
    reported, started = [], []
    monkeypatch.setattr(tray_link, "notify_hotkey_pressed", reported.append)
    monkeypatch.setattr(keyboard_grab, "grab_keyboards_hiding", lambda *a, **k: None)
    monkeypatch.setattr(app, "start_recording", lambda *a, **k: started.append(True) or True)
    rs.state.is_recording = False
    yield reported, started
    rs.state.is_recording = False


def press(code):
    app._handle_key_event(FakeEvent(code, 1), HOLD, TOGGLE,
                          None, set(), [], FakeConfig(), 0)


def flag_path():
    return service_launcher.hotkey_test_flag_path()


def test_a_fresh_flag_reports_presses_instead_of_recording(keys):
    reported, started = keys
    open(flag_path(), "w").close()

    press(HOLD)
    press(TOGGLE)

    assert reported == ["hold", "toggle"]
    assert started == []


def test_without_the_flag_the_hotkeys_record(keys):
    reported, started = keys

    press(HOLD)

    assert reported == []
    assert started == [True]


def test_a_stale_flag_left_by_a_crashed_preferences_is_ignored(keys):
    reported, started = keys
    open(flag_path(), "w").close()
    old = time.time() - service_launcher.HOTKEY_TEST_LEASE_S - 1
    os.utime(flag_path(), (old, old))

    press(HOLD)

    assert reported == []
    assert started == [True], "a dead Preferences left dictation switched off"


def test_preferences_refreshes_well_within_the_lease():
    assert service_launcher.HOTKEY_TEST_REFRESH_S * 2 < service_launcher.HOTKEY_TEST_LEASE_S


def test_signal_handlers_are_installed_before_anything_slow(monkeypatch):
    """SIGUSR1 and SIGUSR2 end a process that doesn't handle them. Loading the
    model takes seconds (minutes with a download), so handlers installed after
    it would leave a window in which a signal kills the service."""
    saved = {s: signal.getsignal(s) for s in (signal.SIGUSR1, signal.SIGUSR2)}

    class Stop(Exception):
        pass

    def first_slow_step():
        assert signal.getsignal(signal.SIGUSR1) == app._handle_sigusr1
        assert signal.getsignal(signal.SIGUSR2) == app._handle_sigusr2
        raise Stop

    from tuxtalk import desktop_identity
    monkeypatch.setattr(desktop_identity, "apply", lambda: None)
    monkeypatch.setattr(app, "_acquire_single_instance", lambda: None)
    monkeypatch.setattr(app, "load_config", first_slow_step)
    try:
        with pytest.raises(Stop):
            app.main()
    finally:
        for s, handler in saved.items():
            signal.signal(s, handler)


def test_a_stray_sigusr2_does_not_toggle_anything(keys):
    reported, started = keys
    app._handle_sigusr2(signal.SIGUSR2, None)

    press(HOLD)

    assert reported == [] and started == [True]
