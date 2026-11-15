"""Blocking D-Bus calls from TuxTalk's worker threads never crash the service.

dbus-python's shared connection runs on GLib's loop, on GTK's thread in the
service; a blocking call made on it from another thread, answered after its
time limit, freed the same timer on both threads and glibc ended the
service (SIGABRT). On KDE that happened when KWin answered the mouse grid's
zoom late. Such calls go on dbus_calls' connection, which has no main loop.
"""
import os
import pathlib
import re
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("dbus")

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

# A service that answers every call 50 ms late, and a client that calls it
# from four threads, each call giving up after 20 ms, while GLib's loop runs
# on a thread of its own as in the service. CONNECTION picks the connection.
_LATE_SERVICE = textwrap.dedent("""
    import dbus, dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    name = dbus.service.BusName("org.example.Late", bus)
    class Late(dbus.service.Object):
        @dbus.service.method("org.example.Late", in_signature="", out_signature="",
                             async_callbacks=("ok", "err"))
        def Slow(self, ok, err):
            GLib.timeout_add(50, lambda: ok() and False)
    Late(bus, "/late")
    print("ready", flush=True)
    GLib.MainLoop().run()
""")
_CLIENT = textwrap.dedent("""
    import sys, threading
    import dbus, dbus.mainloop.glib
    from gi.repository import GLib
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    dbus.SessionBus()
    from tuxtalk import dbus_calls
    bus = dbus_calls.session() if sys.argv[1] == "dbus_calls" else dbus.SessionBus()
    GLib.timeout_add(1, lambda: True)
    threading.Thread(target=GLib.MainLoop().run, daemon=True).start()
    def calls():
        for _ in range(300):
            try:
                bus.call_blocking("org.example.Late", "/late", "org.example.Late", "Slow",
                                  "", (), timeout=0.02)
            except dbus.exceptions.DBusException:
                pass
    workers = [threading.Thread(target=calls) for _ in range(4)]
    for w in workers: w.start()
    for w in workers: w.join()
    print("survived")
""")


def _late_answers(connection):
    """Run the client on *connection* ("dbus_calls" or "shared") against
    the late service, on the tests' session bus; its exit code."""
    service = subprocess.Popen([sys.executable, "-c", _LATE_SERVICE],
                               stdout=subprocess.PIPE, text=True)
    try:
        assert service.stdout.readline().strip() == "ready"
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(
            [str(SRC), os.environ.get("PYTHONPATH", "")]))
        return subprocess.run([sys.executable, "-c", _CLIENT, connection], env=env,
                              capture_output=True, timeout=120).returncode
    finally:
        service.kill()
        service.wait()


def test_late_answers_to_blocking_calls_from_threads_dont_crash_it():
    assert _late_answers("dbus_calls") == 0


def test_one_connection_serves_every_call():
    from tuxtalk import dbus_calls
    assert dbus_calls.session() is dbus_calls.session()


# Where a blocking call may run off GTK's thread in the service. The shared
# connection's name is still read there (focused_window: whether the service
# holds TuxTalk's name itself), but no call is made on it.
_CALLERS = ("app_focus", "app_launcher", "focused_window", "keyboard_layout", "kwin_desktop",
            "kwin_focus", "kwin_hotkeys", "notify", "tray_link", "update_ui")


@pytest.mark.parametrize("module", _CALLERS)
def test_blocking_calls_go_on_their_own_connection(module):
    text = (SRC / "tuxtalk" / f"{module}.py").read_text()
    assert not re.search(r"dbus\.SessionBus\(\)\.(call_blocking|get_object)", text)
    assert not re.search(r"bus = dbus\.SessionBus\(\)", text)
