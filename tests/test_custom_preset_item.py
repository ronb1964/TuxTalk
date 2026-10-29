"""Tray > Performance > Custom opens Preferences.

Clicking a radio item moves the dot to it, so after opening Preferences the
dot must go back to the setup that's really in use: Custom only shows the dot
when the settings match no preset.
"""
import types

import pytest

from tuxtalk import tray_menu
from tuxtalk.tray_menu import TrayMenu


@pytest.fixture(autouse=True)
def run_timeouts_now(monkeypatch):
    """The dot goes back on a short timer; run it at once and record that it
    was deferred rather than done in the same main-loop pass."""
    deferred = []

    def timeout_add(ms, fn, *args):
        deferred.append(ms)
        fn(*args)
        return 1
    monkeypatch.setattr(tray_menu.GLib, "timeout_add", timeout_add)
    return deferred


class Item:
    def __init__(self, active):
        self.active = active

    def get_active(self):
        return self.active


def fake_menu(moving_dot=False):
    """The tray's menu with its dot moves recorded, on a tray that records
    Preferences opening."""
    calls = []
    menu = types.SimpleNamespace(
        _moving_dot=moving_dot,
        tray=types.SimpleNamespace(open_preferences=lambda _: calls.append("prefs")),
        put_dot_back=lambda: calls.append("revert"),
        calls=calls,
    )
    menu.put_dot_back_soon = lambda: TrayMenu.put_dot_back_soon(menu)
    return menu


def test_clicking_custom_opens_preferences_then_puts_the_dot_back(run_timeouts_now):
    menu = fake_menu()
    TrayMenu._on_custom(menu, Item(active=True))
    assert menu.calls == ["prefs", "revert"]
    # Deferred: done in the same pass, KDE shows two dots (see the handler).
    assert run_timeouts_now and run_timeouts_now[0] > 0


def test_the_dot_leaving_custom_does_not_open_preferences():
    """GTK sends "activate" to the radio being unselected too."""
    menu = fake_menu()
    TrayMenu._on_custom(menu, Item(active=False))
    assert menu.calls == []


def test_the_tray_moving_the_dot_itself_does_not_open_preferences():
    menu = fake_menu(moving_dot=True)
    TrayMenu._on_custom(menu, Item(active=True))
    assert menu.calls == []
