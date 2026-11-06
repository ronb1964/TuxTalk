"""The desktops the window tests stand in for, shared by test_app_focus.py,
test_kwin_windows.py, test_x11_windows.py, test_window_actions.py and
test_desktop_actions.py: KWin (a script run under Node, answering over the
test's private session bus), GNOME Shell (an object on that bus) and X11 (a
private Xvfb with a stand-in window manager). Nothing here touches the real
desktop. Registered as a plugin in conftest.py.
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
from tuxtalk import (app_focus, extension_helper, kwin_activate, kwin_desktop, kwin_windows,
                     x11_desktop, x11_windows, xcb_ctypes)


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
def x11_session(monkeypatch):
    set_desktop(monkeypatch, "XFCE", "x11")


@pytest.fixture
def nobody_is_asked(monkeypatch):
    """Fails the test if any desktop is asked about a window or desktops."""
    def asked(*_args, **_kwargs):
        pytest.fail("a desktop was asked about a window")
    monkeypatch.setattr(app_focus, "call_extension", asked)
    monkeypatch.setattr(kwin_activate, "activate", asked)
    monkeypatch.setattr(kwin_windows, "act", asked)
    monkeypatch.setattr(app_focus.x11_activate, "activate", asked)
    monkeypatch.setattr(x11_windows, "act", asked)
    monkeypatch.setattr(kwin_desktop, "act", asked)
    monkeypatch.setattr(x11_desktop, "act", asked)


def _private_bus():
    """A connection of its own to the test's session bus."""
    address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


def _own(bus, name):
    """Whether *bus* became *name*'s owner. Not queued: a fake a test left
    behind that still owns it would answer in this test's place."""
    reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                          "org.freedesktop.DBus", "RequestName", GLib.Variant("(su)", (name, 4)),
                          GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
    return reply.unpack()[0] == 1


class _BusService:
    """A desktop service on the test's session bus, served from a thread
    and main context of its own, owning *names*. Starting returns once it
    owns them all; stop() returns once its connection is closed and the
    names are free for the next test's fake, and fails the test otherwise.
    Each fake registers its objects in _register(bus, register)."""

    names = ()

    def _start(self):
        self._context = GLib.MainContext()
        self._loop = GLib.MainLoop(self._context)
        self._owns = False
        ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, args=(ready,), daemon=True)
        self._thread.start()
        assert ready.wait(10), f"{type(self).__name__} didn't start"
        assert self._owns, f"something else already owns one of {self.names}"

    def _serve(self, ready):
        self._context.push_thread_default()
        bus = _private_bus()
        self._register(bus, getattr(bus, "register_object_with_closures2", bus.register_object))
        self._owns = all(_own(bus, name) for name in self.names)
        ready.set()
        if self._owns:
            self._loop.run()
        bus.close_sync(None)

    def stop(self):
        # Quit from the serving thread: a quit() sent before run() has
        # started is lost, and the loop would then serve forever.
        self._context.invoke_full(GLib.PRIORITY_DEFAULT, self._loop.quit)
        self._thread.join(10)
        assert not self._thread.is_alive(), f"{type(self).__name__} didn't stop"


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
        self.screens = 1
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
            workspace.screens = Array.from({{length: {self.screens}}}, (_, i) => ({{name: "DP-" + i}}));
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
            signature = "(s)" if method == "Failed" else "(b)"
            self._bus.call(service, path, interface, method,
                           GLib.Variant(signature, tuple(args)), None,
                           Gio.DBusCallFlags.NONE, -1, None, None, None)

    def close(self):
        self._bus.flush_sync(None)
        self._bus.close_sync(None)


@pytest.fixture
def fake_kwin(monkeypatch, tmp_path):
    kwin = FakeKWin()
    monkeypatch.setattr(kwin_activate, "script_path", lambda name: str(tmp_path / name))
    monkeypatch.setattr(kwin_activate.kwin_focus, "session_bus", lambda: kwin)
    yield kwin
    kwin.close()


_KDE_XML = """<node>
  <interface name="org.kde.kglobalaccel.Component">
    <method name="invokeShortcut"><arg type="s" direction="in" name="name"/></method>
  </interface>
  <interface name="org.kde.KWin">
    <method name="showDesktop"><arg type="b" direction="in" name="showing"/></method>
    <method name="nextDesktop"/>
    <method name="previousDesktop"/>
    <method name="setCurrentDesktop">
      <arg type="i" direction="in" name="desktop"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="currentDesktop"><arg type="i" direction="out" name="desktop"/></method>
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get">
      <arg type="s" direction="in" name="interface_name"/>
      <arg type="s" direction="in" name="property_name"/>
      <arg type="v" direction="out" name="value"/>
    </method>
  </interface>
</node>"""


class FakeKDE(_BusService):
    """KDE Plasma 6's kglobalaccel and KWin on the test's session bus: the
    shortcut, desktop and show-desktop calls kwin_desktop makes. *desktops*
    exist, the user is on *current* (1-based), *showing* the desktop; with
    *wraps* the last desktop's next is the first. Every call is kept in
    *calls* as (method, *args)."""

    names = ("org.kde.kglobalaccel", "org.kde.KWin")

    def __init__(self):
        self.desktops, self.current, self.showing, self.wraps = 2, 1, False, False
        self.calls = []
        self._start()

    def _register(self, _bus, register):
        interfaces = Gio.DBusNodeInfo.new_for_xml(_KDE_XML).interfaces
        register("/component/kwin", interfaces[0], self._on_call, None, None)
        for path in ("/KWin", "/VirtualDesktopManager"):
            register(path, interfaces[1], self._on_call, None, None)
            register(path, interfaces[2], self._on_call, None, None)

    def _on_call(self, _bus, _sender, _path, _interface, method, parameters, invocation):
        args = parameters.unpack()
        if method == "Get":
            value = {"showingDesktop": GLib.Variant("b", self.showing),
                     "count": GLib.Variant("u", self.desktops)}[args[1]]
            invocation.return_value(GLib.Variant("(v)", (value,)))
            return
        self.calls.append((method, *args))
        answer = {"currentDesktop": lambda: (self.current,),
                  "setCurrentDesktop": lambda: (self._switch(args[0]),)}.get(method)
        if method in ("nextDesktop", "previousDesktop"):
            self._step(1 if method == "nextDesktop" else -1)
        invocation.return_value(
            GLib.Variant({"currentDesktop": "(i)", "setCurrentDesktop": "(b)"}[method],
                         answer()) if answer else None)

    def _switch(self, number):
        if not 1 <= number <= self.desktops:
            return False
        self.current = number
        return True

    def _step(self, direction):
        wanted = self.current + direction
        if 1 <= wanted <= self.desktops:
            self.current = wanted
        elif self.wraps:
            self.current = 1 if direction > 0 else self.desktops


@pytest.fixture
def fake_kde():
    kde_session = FakeKDE()
    yield kde_session
    kde_session.stop()


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
_DESKTOP_COMMAND = """<method name="DesktopCommand">
    <arg type="s" direction="in" name="action"/>
    <arg type="i" direction="in" name="number"/>
    <arg type="s" direction="out" name="answer"/>
  </method>"""


_EXTENSION_XML = (f'<node><interface name="{app_focus.EXTENSION_INTERFACE}">'
                  f"{_ACTIVATE_APP}{_WINDOW_ACTION}{_DESKTOP_COMMAND}</interface></node>")
# GNOME Shell's own: what it says about an installed extension.
_SHELL_EXTENSIONS_XML = """<node><interface name="org.gnome.Shell.Extensions">
  <method name="GetExtensionInfo">
    <arg type="s" direction="in" name="uuid"/>
    <arg type="a{sv}" direction="out" name="info"/>
  </method>
</interface></node>"""


class FakeShell(_BusService):
    """GNOME Shell on the test's session bus. With *extension*, TuxTalk's extension answers ActivateApp with
    *answer*, keeping each app id it's asked about in *called*, and
    WindowAction with *window_answer*, keeping (action, app_id, every) in
    *window_calls*, and DesktopCommand with *desktop_answer*, keeping
    (action, number) in *desktop_calls*. *extension* "old" is enabled but
    serves no object, as the 0.21 extension GNOME Shell still runs until the
    user logs out after an update; False: TuxTalk's extension isn't enabled
    at all."""

    names = (app_focus.GNOME_SHELL,)

    def __init__(self, extension=True):
        self.answer = self.window_answer = True
        self.desktop_answer = "done"
        self.called, self.window_calls, self.desktop_calls = [], [], []
        self._extension = extension
        self._start()

    def _register(self, _bus, register):
        if self._extension is True:
            interface = Gio.DBusNodeInfo.new_for_xml(_EXTENSION_XML).interfaces[0]
            register(app_focus.EXTENSION_PATH, interface, self._on_call, None, None)
        shell = Gio.DBusNodeInfo.new_for_xml(_SHELL_EXTENSIONS_XML).interfaces[0]
        register("/org/gnome/Shell", shell, self._extension_info(bool(self._extension)),
                 None, None)

    def _on_call(self, _bus, _sender, _path, _interface, method, parameters, invocation):
        if method == "WindowAction":
            self.window_calls.append(parameters.unpack())
            invocation.return_value(GLib.Variant("(b)", (self.window_answer,)))
            return
        if method == "DesktopCommand":
            self.desktop_calls.append(parameters.unpack())
            invocation.return_value(GLib.Variant("(s)", (self.desktop_answer,)))
            return
        self.called.append(parameters.unpack()[0])
        invocation.return_value(GLib.Variant("(b)", (self.answer,)))

    @staticmethod
    def _extension_info(enabled):
        """GetExtensionInfo's answer: TuxTalk's extension enabled and running,
        or, as for an extension GNOME Shell doesn't know, nothing."""
        info = {"state": GLib.Variant("d", 1.0), "enabled": GLib.Variant("b", True)}

        def on_call(_bus, _sender, _path, _interface, _method, parameters, invocation):
            known = enabled and parameters.unpack()[0] == extension_helper.EXTENSION_UUID
            invocation.return_value(GLib.Variant("(a{sv})", (info if known else {},)))
        return on_call


@pytest.fixture
def fake_extension():
    shell = FakeShell()
    yield shell
    shell.stop()


_CLIENT_MESSAGE = 33
_SUBSTRUCTURE_REDIRECT = 1 << 20
_CW_EVENT_MASK = 1 << 11
_ATOM_ATOM, _ATOM_CARDINAL, _ATOM_WINDOW, _ATOM_STRING, _ATOM_WM_CLASS = 4, 6, 33, 31, 67
_CONFIG_X, _CONFIG_Y = 1, 2


@pytest.fixture
def x11_flags():
    """Extra Xvfb options; a test sets them with parametrize ("-extension", "RANDR")."""
    return ()


@pytest.fixture
def x11(monkeypatch, x11_flags):
    """A private X server on an X11 Xfce session; DISPLAY points at it."""
    if not shutil.which("Xvfb"):
        pytest.skip("needs Xvfb")
    read, write = os.pipe()
    server = subprocess.Popen(["Xvfb", "-displayfd", str(write), "-nolisten", "tcp", *x11_flags],
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
              b"_NET_WM_WINDOW_TYPE_DESKTOP", b"_NET_WM_WINDOW_TYPE_DOCK",
              b"_NET_MOVERESIZE_WINDOW", b"_NET_WORKAREA", b"_NET_FRAME_EXTENTS",
              b"_NET_CURRENT_DESKTOP", b"_NET_NUMBER_OF_DESKTOPS", b"_NET_WM_DESKTOP",
              b"_NET_SHOWING_DESKTOP",
              b"_NET_WM_STATE_FULLSCREEN", b"_NET_WM_STRUT_PARTIAL")


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
        lib.xcb_configure_window.restype = xcb_ctypes.Cookie
        lib.xcb_configure_window.argtypes = [P, U32, U16, ctypes.POINTER(ctypes.c_int32)]
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

    def set_cardinals(self, window, name, values):
        """Set the CARDINAL list property *name* (b"_NET_WORKAREA") of
        *window*, the root window included, as the window manager does."""
        data = struct.pack(f"={len(values)}I", *values)
        self.lib.xcb_change_property(self.conn, 0, window, self.atom(name), _ATOM_CARDINAL, 32,
                                     len(values), data)
        self.round_trip()

    def move(self, window, x, y):
        """Put *window* at (*x*, *y*) on the screen, as a user dragging it would."""
        values = (ctypes.c_int32 * 2)(x, y)
        self.lib.xcb_configure_window(self.conn, window, _CONFIG_X | _CONFIG_Y, values)
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


_PORTAL_XML = """<node><interface name="org.freedesktop.portal.Screenshot">
  <method name="Screenshot">
    <arg type="s" direction="in" name="parent_window"/>
    <arg type="a{sv}" direction="in" name="options"/>
    <arg type="o" direction="out" name="handle"/>
  </method>
</interface></node>"""


class FakePortal(_BusService):
    """The screenshot portal on the test's session bus. Screenshot answers
    with the Response signal on the request path it was given: *code* (0
    done, 1 cancelled, 2 failed, None never answers), and for 0 the *saved*
    result: "file", one it writes in *folder*; "unseen", a file TuxTalk
    can't see (a sandbox's); "nothing", no uri at all. With *replies* off
    the method call itself is never answered. *calls* keeps each call's
    options."""

    names = ("org.freedesktop.portal.Desktop",)

    def __init__(self, folder):
        self.code = 0
        self.saved = "file"
        self.replies = True
        self.calls = []
        self._unanswered = []
        self._folder = folder
        self._start()

    def _register(self, _bus, register):
        interface = Gio.DBusNodeInfo.new_for_xml(_PORTAL_XML).interfaces[0]
        register("/org/freedesktop/portal/desktop", interface, self._on_call, None, None)

    def _on_call(self, bus, sender, _path, _interface, _method, parameters, invocation):
        options = parameters.unpack()[1]
        self.calls.append(options)
        if not self.replies:
            self._unanswered.append(invocation)     # kept, so GDBus doesn't answer for it
            return
        request = ("/org/freedesktop/portal/desktop/request/"
                   f"{sender.lstrip(':').replace('.', '_')}/{options['handle_token']}")
        invocation.return_value(GLib.Variant("(o)", (request,)))
        if self.code is None:
            return
        results = {}
        if self.code == 0 and self.saved != "nothing":
            shot = os.path.join(self._folder, "Screenshot from the portal.png")
            if self.saved == "file":
                os.makedirs(self._folder, exist_ok=True)
                with open(shot, "wb") as f:
                    f.write(b"png")
            results = {"uri": GLib.Variant("s", GLib.filename_to_uri(shot))}
        bus.emit_signal(sender, request, "org.freedesktop.portal.Request", "Response",
                        GLib.Variant("(ua{sv})", (self.code, results)))


@pytest.fixture
def fake_portal(tmp_path):
    portal = FakePortal(tmp_path / "Pictures")
    yield portal
    portal.stop()
