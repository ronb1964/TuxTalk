"""Tray > Performance > Custom opens Preferences.

Clicking a radio item moves the dot to it, so after opening Preferences the
dot must go back to the setup that's really in use: Custom only shows the dot
when the settings match no preset.
"""
import types

import pytest

from tuxtalk import tray as tray_module
from tuxtalk.tray import DictationTray


@pytest.fixture(autouse=True)
def run_timeouts_now(monkeypatch):
    """The dot goes back on a short timer; run it at once and record that it
    was deferred rather than done in the same main-loop pass."""
    deferred = []

    def timeout_add(ms, fn, *args):
        deferred.append(ms)
        fn(*args)
        return 1
    monkeypatch.setattr(tray_module.GLib, "timeout_add", timeout_add)
    return deferred


class Item:
    def __init__(self, active):
        self.active = active

    def get_active(self):
        return self.active


def fake_tray(updating=False):
    calls = []
    tray = types.SimpleNamespace(
        _updating_preset=updating,
        open_preferences=lambda _: calls.append("prefs"),
        _revert_preset_radio=lambda: calls.append("revert"),
        calls=calls,
    )
    tray._put_dot_back_soon = lambda: DictationTray._put_dot_back_soon(tray)
    return tray


def test_clicking_custom_opens_preferences_then_puts_the_dot_back(run_timeouts_now):
    tray = fake_tray()
    DictationTray._on_custom_preset(tray, Item(active=True))
    assert tray.calls == ["prefs", "revert"]
    # Deferred: done in the same pass, KDE shows two dots (see the handler).
    assert run_timeouts_now and run_timeouts_now[0] > 0


def test_the_dot_leaving_custom_does_not_open_preferences():
    """GTK sends "activate" to the radio being unselected too."""
    tray = fake_tray()
    DictationTray._on_custom_preset(tray, Item(active=False))
    assert tray.calls == []


def test_the_tray_moving_the_dot_itself_does_not_open_preferences():
    tray = fake_tray(updating=True)
    DictationTray._on_custom_preset(tray, Item(active=True))
    assert tray.calls == []
