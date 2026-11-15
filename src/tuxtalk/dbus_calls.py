"""The session bus for blocking D-Bus calls, safe from any thread.

dbus-python's shared session connection runs on GLib's main loop, which in
the dictation service is GTK's own thread (app._dbus_main_loop_first). A
blocking call made on that connection from another thread puts its time
limit on that loop, and when the answer comes after the limit (KWin busy
with a first zoom, a tray whose loop is busy), both threads free the same
timer and glibc ends the process (SIGABRT), with nothing in TuxTalk's log.
The mouse grid's desktop zoom did that on KDE, in about one test run in
four. This connection has no main loop: libdbus keeps the time limit
itself, and any thread may use it. It only makes calls; whatever exports
objects or listens for signals stays on the shared connection.
"""
import threading

_lock = threading.Lock()
_bus = None


def session():
    """The connection for blocking calls, opened on first use and again
    after it closed. Raises dbus-python's DBusException when there's no
    session bus, as dbus.SessionBus() does."""
    global _bus
    import dbus
    import dbus.mainloop
    with _lock:
        if _bus is None or not _bus.get_is_connected():
            _bus = dbus.SessionBus(private=True, mainloop=dbus.mainloop.NULL_MAIN_LOOP)
        return _bus
