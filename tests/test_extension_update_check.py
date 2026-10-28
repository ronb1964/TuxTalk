"""The GNOME extension's Check for Updates: the app's own result window, and
the extension's Updates tab for an extension update, never both at once."""
import threading

import pytest

from tuxtalk import dbus_service, update_checker
from tuxtalk.dbus_service import TuxTalkDBusService


@pytest.mark.parametrize("app, extension, window", [
    (True, False, True),        # an app update: the window offers it
    (False, False, True),       # nothing new: the window says so
    (False, True, False),       # only the extension: its Updates tab, not "up to date"
    (True, True, True),
])
def test_the_result_window_comes_only_when_it_has_something_to_say(monkeypatch, app,
                                                                   extension, window):
    shown, signalled, done = [], [], threading.Event()
    s = TuxTalkDBusService.__new__(TuxTalkDBusService)          # no real bus
    s.app = type("App", (), {"show_update_result": lambda self, r: shown.append(r)})()
    monkeypatch.setattr(dbus_service.GLib, "idle_add",
                        lambda fn, *a: signalled.append(a) or done.set())
    monkeypatch.setattr(update_checker, "check_for_updates", lambda: {
        "success": True, "update_available": app, "extension_update": extension,
        "extension_current": 0, "extension_latest": 3})
    s.CheckForUpdates()                         # checks on a thread of its own
    assert done.wait(5)
    assert bool(shown) is window
    # Version 0 is a version: it reached the extension as 0, not as "unknown" (-1).
    assert signalled[0][4:6] == (0, 3)
