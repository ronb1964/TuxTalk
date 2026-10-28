"""Quit from the tray must end the process even with a dialog open.

dialog.run() and the welcome dialog's Gtk.main() spin nested loops;
Gtk.main_quit() from inside one cannot stop the tray's outer loop.
"""
import types

from tuxtalk import tray as tray_mod
from tuxtalk import tray_service


def test_leaving_the_main_loop_queues_a_hard_exit(monkeypatch):
    """Quit ends here once the service has gone (test_quit_restart)."""
    timers = []
    monkeypatch.setattr(tray_service, "GLib", types.SimpleNamespace(
        timeout_add=lambda ms, fn: timers.append((ms, fn))))
    quits = []
    monkeypatch.setattr(tray_service, "Gtk", types.SimpleNamespace(
        main_quit=lambda: quits.append(1)))
    exits = []
    monkeypatch.setattr(tray_service.os, "_exit", lambda code: exits.append(code))

    tray_mod.DictationTray._leave_main_loop(types.SimpleNamespace())

    assert quits == [1]
    assert len(timers) == 1 and timers[0][0] <= 500
    timers[0][1]()
    assert exits == [0]
