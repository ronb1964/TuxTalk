"""The desktops the window tests stand in for, shared by test_app_focus.py,
test_kwin_windows.py, test_x11_windows.py and test_window_actions.py: KWin
(a script run under Node, answering over the test's private session bus),
GNOME Shell (an object on that bus) and X11 (a private Xvfb with a stand-in
window manager). Nothing here touches the real desktop. Registered as a
plugin in conftest.py.
"""
import ctypes
import json
import os
import shutil
import struct
import subprocess
import threading

import pytest
from gi.repository import Gio, GLib

from gnome_extension_js import run_kwin_script
from tuxtalk import app_focus, kwin_activate, kwin_windows, x11_windows, xcb_ctypes


def set_desktop(monkeypatch, desktop, session):
    """The session the tests run in: *desktop* (XDG_CURRENT_DESKTOP) on *session*."""
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    monkeypatch.setenv("XDG_SESSION_TYPE", session)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)


@pytest.fixture
def kde(monkeypatch):
    set_desktop(monkeypatch, "KDE", "wayland")


@pytest.fixture
def gnome(monkeypatch):
    set_desktop(monkeypatch, "GNOME", "wayland")


@pytest.fixture
def cosmic(monkeypatch):
    set_desktop(monkeypatch, "COSMIC", "wayland")


@pytest.fixture
def nobody_is_asked(monkeypatch):
    """Fails the test if any desktop is asked about a window."""
    def asked(*_args, **_kwargs):
        pytest.fail("a desktop was asked about a window")
    monkeypatch.setattr(app_focus, "call_extension", asked)
    monkeypatch.setattr(kwin_activate, "activate", asked)
    monkeypatch.setattr(kwin_windows, "act", asked)
    monkeypatch.setattr(app_focus.x11_activate, "activate", asked)
    monkeypatch.setattr(x11_windows, "act", asked)


def _private_bus():
    """A connection of its own to the test's session bus."""
    address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


class FakeKWin:
    """KWin's Scripting interface, as kwin_focus.session_bus() gives it:
    each started script runs under Node with *windows* open (JavaScript
    makeWindow expressions, the bottom of the stack first), and its D-Bus
    calls go out on the test's bus as KWin's callDBus sends them. *silent*:
    a KWin that never runs the script. *active*: the index in *windows* of
    the window that has the focus when the script runs (none by default).
    After a run, *closed*, *minimized* and *maximized* hold each window's
    state."""

    def __init__(self):
        self.windows = []
        self.silent = False
        self.load_result = 1
        self.scripts = []
        self.methods = []
        self.active = None
        self.activated = None
        self.closed = self.minimized = self.maximized = None
        self._bus = _private_bus()

    def call(self, method, *args):
        self.methods.append(method)
        if method == "loadScript":
            with open(args[0]) as f:
                self.scripts.append(f.read())
            return self.load_result
        if method == "start" and not self.silent:
            self._run(self.scripts[-1])
        return True

    def _run(self, script):
        result = run_kwin_script(script, f"""
            workspace.stackingOrder = [{", ".join(self.windows)}];
            const active = {json.dumps(self.active)};
            if (active !== null) {{
                workspace.activeWindow = workspace.stackingOrder[active];
            }}
            load();
            const focused = workspace.activeWindow;
            const each = field => workspace.stackingOrder.map(w => w[field]);
            report({{calls: dbusCalls, active: focused && focused.internalId,
                     minimized: each("minimized"), closed: each("closed"),
                     maximized: each("maximized")}});
        """)
        self.activated, self.minimized = result["active"], result["minimized"]
        self.closed, self.maximized = result["closed"], result["maximized"]
        for service, path, interface, method, *args in result["calls"]:
            # Sent and not waited for, as callDBus does.
            self._bus.call(service, path, interface, method,
                           GLib.Variant("(b)", tuple(args)), None,
                           Gio.DBusCallFlags.NONE, -1, None, None, None)

    def close(self):
        self._bus.flush_sync(None)
        self._bus.close_sync(None)


@pytest.fixture
def fake_kwin(monkeypatch, tmp_path):
    kwin = FakeKWin()
    monkeypatch.setattr(kwin_activate, "script_path", lambda: str(tmp_path / "activate.js"))
    monkeypatch.setattr(kwin_windows, "script_path", lambda: str(tmp_path / "windows.js"))
    monkeypatch.setattr(kwin_activate.kwin_focus, "session_bus", lambda: kwin)
    yield kwin
    kwin.close()


_ACTIVATE_APP = """<method name="ActivateApp">
    <arg type="s" direction="in" name="app_id"/>
    <arg type="b" direction="out" name="had_window"/>
  </method>"""
_WINDOW_ACTION = """<method name="WindowAction">
    <arg type="s" direction="in" name="action"/>
    <arg type="s" direction="in" name="app_id"/>
    <arg type="b" direction="in" name="every"/>
    <arg type="b" direction="out" name="acted"/>
  </method>"""


def _shell_xml(extension):
    """The extension's interface: an "old" one, from before the window
    commands, serves ActivateApp alone."""
    methods = _ACTIVATE_APP if extension == "old" else _ACTIVATE_APP + _WINDOW_ACTION
    return (f'<node><interface name="{app_focus.EXTENSION_INTERFACE}">{methods}'
            "</interface></node>")


class FakeShell:
    """GNOME Shell on the test's session bus, served from a thread of its
    own. With *extension*, TuxTalk's extension answers ActivateApp with
    *answer*, keeping each app id it's asked about in *called*, and
    WindowAction with *window_answer*, keeping (action, app_id, every) in
    *window_calls*; *extension* "old" serves ActivateApp alone, as the
    extension GNOME Shell still runs until the user logs out after an update."""

    def __init__(self, extension=True):
        self.answer = self.window_answer = True
        self.called, self.window_calls = [], []
        self._context = GLib.MainContext()
        self._loop = GLib.MainLoop(self._context)
        ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, args=(extension, ready),
                                        daemon=True)
        self._thread.start()
        assert ready.wait(10)

    def _serve(self, extension, ready):
        self._context.push_thread_default()
        bus = _private_bus()
        if extension:
            interface = Gio.DBusNodeInfo.new_for_xml(_shell_xml(extension)).interfaces[0]
            register = getattr(bus, "register_object_with_closures2", bus.register_object)
            register(app_focus.EXTENSION_PATH, interface, self._on_call, None, None)
        bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                      "RequestName", GLib.Variant("(su)", (app_focus.GNOME_SHELL, 4)), None,
                      Gio.DBusCallFlags.NONE, 5000, None)
        ready.set()
        self._loop.run()
        bus.close_sync(None)

    def _on_call(self, _bus, _sender, _path, _interface, method, parameters, invocation):
        if method == "WindowAction":
            self.window_calls.append(parameters.unpack())
            invocation.return_value(GLib.Variant("(b)", (self.window_answer,)))
            return
        self.called.append(parameters.unpack()[0])
        invocation.return_value(GLib.Variant("(b)", (self.answer,)))

    def stop(self):
        self._loop.quit()
        self._thread.join(10)


@pytest.fixture
def fake_extension():
    shell = FakeShell()
    yield shell
    shell.stop()


_CLIENT_MESSAGE = 33
_SUBSTRUCTURE_REDIRECT = 1 << 20
_CW_EVENT_MASK = 1 << 11
_ATOM_ATOM, _ATOM_WINDOW, _ATOM_STRING, _ATOM_WM_CLASS = 4, 33, 31, 67


@pytest.fixture
def x11(monkeypatch):
    """A private X server on an X11 Xfce session; DISPLAY points at it."""
    if not shutil.which("Xvfb"):
        pytest.skip("needs Xvfb")
    read, write = os.pipe()
    server = subprocess.Popen(["Xvfb", "-displayfd", str(write), "-nolisten", "tcp"],
                              pass_fds=(write,), stderr=subprocess.DEVNULL)
    os.close(write)
    with os.fdopen(read) as f:
        monkeypatch.setenv("DISPLAY", f":{f.readline().strip()}")
    set_desktop(monkeypatch, "XFCE", "x11")
    yield
    server.terminate()
    server.wait()


# The requests a window manager says it supports (EWMH's _NET_SUPPORTED) have
# atoms from its start; TuxTalk asks only for atoms the X server already has.
_SUPPORTED = (b"_NET_ACTIVE_WINDOW", b"_NET_CLOSE_WINDOW", b"WM_CHANGE_STATE",
              b"_NET_WM_STATE", b"_NET_WM_STATE_MAXIMIZED_VERT",
              b"_NET_WM_STATE_MAXIMIZED_HORZ", b"_NET_WM_WINDOW_TYPE",
              b"_NET_WM_WINDOW_TYPE_DESKTOP", b"_NET_WM_WINDOW_TYPE_DOCK")


class FakeWindowManager:
    """An X client in the window manager's place: its windows, the list of
    them on the root window (_NET_CLIENT_LIST_STACKING), the active one
    (_NET_ACTIVE_WINDOW), and the requests that reach it."""

    def __init__(self):
        self.lib = lib = xcb_ctypes.xcb_lib()
        P, U8, U16, U32 = ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32
        lib.xcb_generate_id.restype = U32
        lib.xcb_generate_id.argtypes = [P]
        lib.xcb_create_window.restype = xcb_ctypes.Cookie
        lib.xcb_create_window.argtypes = [P, U8, U32, U32, ctypes.c_int16, ctypes.c_int16,
                                          U16, U16, U16, U16, U32, U32, P]
        lib.xcb_change_property.restype = xcb_ctypes.Cookie
        lib.xcb_change_property.argtypes = [P, U8, U32, U32, U32, U8, U32, P]
        lib.xcb_change_window_attributes_checked.restype = xcb_ctypes.Cookie
        lib.xcb_change_window_attributes_checked.argtypes = [P, U32, U32,
                                                             ctypes.POINTER(U32)]
        lib.xcb_poll_for_event.restype = P
        lib.xcb_poll_for_event.argtypes = [P]
        screen = ctypes.c_int(0)
        self.conn = lib.xcb_connect(None, ctypes.byref(screen))
        self.root = xcb_ctypes.x11_root(lib, self.conn, screen.value)
        mask = (ctypes.c_uint32 * 1)(_SUBSTRUCTURE_REDIRECT)
        assert not lib.xcb_request_check(self.conn, lib.xcb_change_window_attributes_checked(
            self.conn, self.root, _CW_EVENT_MASK, mask))
        for name in _SUPPORTED:
            self.atom(name)
        self._received = []

    def atom(self, name):
        """*name*'s atom, made if the X server has none yet, as a window
        manager makes the ones it supports."""
        reply = self.lib.xcb_intern_atom_reply(
            self.conn, self.lib.xcb_intern_atom(self.conn, 0, len(name), name), None)
        atom = reply.contents.atom
        xcb_ctypes.free(reply)
        return atom

    def window(self, instance, wm_class, window_type=None, **entry_ids):
        """A new window with WM_CLASS *instance*, *wm_class*, the
        _NET_WM_WINDOW_TYPE *window_type* (b"_NET_WM_WINDOW_TYPE_DOCK") if
        given, and the menu entry it names in *entry_ids*:
        _GTK_APPLICATION_ID=..., _KDE_NET_WM_DESKTOP_FILE=..."""
        lib = self.lib
        window = lib.xcb_generate_id(self.conn)
        lib.xcb_create_window(self.conn, 0, window, self.root, 0, 0, 10, 10, 0, 1, 0, 0, None)
        raw = f"{instance}\0{wm_class}\0".encode()
        lib.xcb_change_property(self.conn, 0, window, _ATOM_WM_CLASS, _ATOM_STRING, 8,
                                len(raw), raw)
        if window_type:
            lib.xcb_change_property(self.conn, 0, window, self.atom(b"_NET_WM_WINDOW_TYPE"),
                                    _ATOM_ATOM, 32, 1, struct.pack("=I", self.atom(window_type)))
        for name, value in entry_ids.items():
            lib.xcb_change_property(self.conn, 0, window, self.atom(name.encode()),
                                    self.atom(b"UTF8_STRING"), 8, len(value), value.encode())
        return window

    def stack(self, windows):
        """Name *windows*, the bottom one first, as the window manager's."""
        atom = self.atom(b"_NET_CLIENT_LIST_STACKING")
        data = struct.pack(f"={len(windows)}I", *windows)
        self.lib.xcb_change_property(self.conn, 0, self.root, atom, _ATOM_WINDOW, 32,
                                     len(windows), data)
        self.round_trip()

    def round_trip(self):
        """Wait until the X server has done everything asked of it so far."""
        self.atom(b"PRIMARY")

    def set_active(self, window):
        """Say *window* has the focus, as the window manager does on the root."""
        data = struct.pack("=I", window)
        self.lib.xcb_change_property(self.conn, 0, self.root, self.atom(b"_NET_ACTIVE_WINDOW"),
                                     _ATOM_WINDOW, 32, 1, data)
        self.round_trip()

    def requests(self, name):
        """(window, its five data words) of each request *name*
        (b"_NET_CLOSE_WINDOW") that has reached the window manager so far."""
        self.round_trip()
        while event := self.lib.xcb_poll_for_event(self.conn):
            raw = ctypes.string_at(event, 32)
            xcb_ctypes.free(event)
            kind, form, _seq, window, atom, *data = struct.unpack_from("=BBHII5I", raw)
            if kind & 0x7f == _CLIENT_MESSAGE and form == 32:
                self._received.append((atom, window, tuple(data)))
        wanted = self.atom(name)
        return [(window, data) for atom, window, data in self._received if atom == wanted]

    def activations(self):
        """(window, source indication) of each _NET_ACTIVE_WINDOW request."""
        return [(window, data[0]) for window, data in self.requests(b"_NET_ACTIVE_WINDOW")]

    def close(self):
        self.lib.xcb_disconnect(self.conn)


@pytest.fixture
def window_manager(x11):
    wm = FakeWindowManager()
    yield wm
    wm.close()
