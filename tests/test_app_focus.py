"""Open apps by voice: "open Firefox" with Firefox already open brings its
window forward instead of starting a second one (app_focus.py), on KDE,
GNOME and other X11 desktops; anywhere else, or when that fails, TuxTalk
starts the app.

Nothing here touches the real desktop. KWin is a stand-in that runs TuxTalk's
script under Node (tests/kwin) and sends its answer over the test's private
session bus; GNOME Shell is an object on that bus; X11 is a private Xvfb with
a stand-in window manager.
"""
import ctypes
import os
import shutil
import struct
import subprocess
import threading

import pytest
from gi.repository import Gio, GLib

from gnome_extension_js import run_kwin_script
from tuxtalk import app_focus, kwin_activate, xcb_ctypes
from tuxtalk.app_finder import AppEntry


def _entry(app_id, name, program):
    return AppEntry(app_id, name, name, "", (), program, "")


FIREFOX = _entry("firefox.desktop", "Firefox", "firefox")
KONSOLE = _entry("org.kde.konsole.desktop", "Konsole", "konsole")
IDLE = _entry("idle3.desktop", "IDLE", "python3")


def _desktop(monkeypatch, desktop, session):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    monkeypatch.setenv("XDG_SESSION_TYPE", session)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)


@pytest.fixture
def kde(monkeypatch):
    _desktop(monkeypatch, "KDE", "wayland")


@pytest.fixture
def gnome(monkeypatch):
    _desktop(monkeypatch, "GNOME", "wayland")


@pytest.fixture
def cosmic(monkeypatch):
    _desktop(monkeypatch, "COSMIC", "wayland")


@pytest.fixture
def nobody_is_asked(monkeypatch):
    """Fails the test if any desktop is asked about a window."""
    def asked(*_args, **_kwargs):
        pytest.fail("a desktop was asked to bring a window forward")
    monkeypatch.setattr(app_focus, "_ask_extension", asked)
    monkeypatch.setattr(app_focus.kwin_activate, "activate", asked)
    monkeypatch.setattr(app_focus.x11_activate, "activate", asked)


def _private_bus():
    """A connection of its own to the test's session bus."""
    address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


# --- the names a window is known by -------------------------------------------------

def test_a_window_is_known_by_its_menu_entry_and_its_program():
    """On Wayland KWin names the window after the menu entry
    ("org.kde.konsole"); on X11 its class is the program's ("konsole")."""
    assert app_focus.window_names(KONSOLE) == {"org.kde.konsole", "konsole"}
    assert app_focus.window_names(FIREFOX) == {"firefox"}


def test_an_interpreter_never_names_the_window():
    """Every Python app's window could be called "python3": opening IDLE
    must not bring some other Python program forward."""
    assert app_focus.window_names(IDLE) == {"idle3"}


# --- KDE ------------------------------------------------------------------------------

class FakeKWin:
    """KWin's Scripting interface, as kwin_focus.session_bus() gives it:
    each started script runs under Node with *windows* open (JavaScript
    makeWindow expressions, the bottom of the stack first), and its D-Bus
    calls go out on the test's bus as KWin's callDBus sends them. *silent*:
    a KWin that never runs the script."""

    def __init__(self):
        self.windows = []
        self.silent = False
        self.load_result = 1
        self.scripts = []
        self.methods = []
        self.activated = None
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
            load();
            const active = workspace.activeWindow;
            report({{calls: dbusCalls, active: active && active.internalId,
                     minimized: workspace.stackingOrder.map(w => w.minimized)}});
        """)
        self.activated, self.minimized = result["active"], result["minimized"]
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
    monkeypatch.setattr(kwin_activate.kwin_focus, "session_bus", lambda: kwin)
    yield kwin
    kwin.close()


def test_kde_brings_the_apps_window_forward(kde, fake_kwin):
    fake_kwin.windows = ['makeWindow("org.kde.konsole", "{k}", 0, 0, 800, 600)',
                         'makeWindow("firefox", "{f}", 0, 0, 800, 600)']
    assert app_focus.focus_running(FIREFOX) is True
    assert fake_kwin.activated == "{f}"
    assert '"firefox"' in fake_kwin.scripts[-1]


def test_kde_picks_the_window_used_last(kde, fake_kwin):
    """Two Konsole windows: the one higher in the stack is the one the user
    had last, as clicking Konsole in the task manager would show."""
    fake_kwin.windows = ['makeWindow("org.kde.konsole", "{old}", 0, 0, 800, 600)',
                         'makeWindow("org.kde.konsole", "{new}", 0, 0, 800, 600)',
                         'makeWindow("firefox", "{f}", 0, 0, 800, 600)']
    assert app_focus.focus_running(KONSOLE) is True
    assert fake_kwin.activated == "{new}"


def test_kde_knows_a_window_by_its_menu_entry(kde, fake_kwin):
    """A Flatpak's window: its class is its app id, which KWin also gives
    as the menu entry it belongs to."""
    flatpak = _entry("org.mozilla.firefox.desktop", "Firefox", "firefox")
    fake_kwin.windows = ['Object.assign(makeWindow("org.mozilla.firefox", "{f}", 0, 0, 9, 9),'
                         ' {desktopFileName: "org.mozilla.firefox"})']
    assert app_focus.focus_running(flatpak) is True
    assert fake_kwin.activated == "{f}"


def test_kde_shows_a_minimized_window(kde, fake_kwin):
    fake_kwin.windows = ['Object.assign(makeWindow("firefox", "{f}", 0, 0, 9, 9),'
                         ' {minimized: true})']
    assert app_focus.focus_running(FIREFOX) is True
    assert fake_kwin.minimized == [False]


def test_kde_never_brings_forward_a_popup_of_the_app(kde, fake_kwin):
    """A menu or tooltip of Firefox has its class too, but isn't a window
    to switch to: with only that, Firefox is started."""
    fake_kwin.windows = ['Object.assign(makeWindow("firefox", "{tip}", 0, 0, 9, 9),'
                         ' {normalWindow: false})']
    assert app_focus.focus_running(FIREFOX) is False
    assert fake_kwin.activated is None


def test_kde_without_the_apps_window_says_launch(kde, fake_kwin):
    fake_kwin.windows = ['makeWindow("org.kde.konsole", "{k}", 0, 0, 800, 600)']
    assert app_focus.focus_running(FIREFOX) is False
    assert fake_kwin.activated is None


def test_a_kwin_that_never_answers_means_launch(kde, fake_kwin):
    fake_kwin.silent = True
    assert app_focus.focus_running(FIREFOX, timeout=0.05) is False


def test_a_refused_script_means_launch(kde, fake_kwin):
    """loadScript says -1 when a script of that name is still loaded."""
    fake_kwin.load_result = -1
    assert app_focus.focus_running(FIREFOX) is False
    assert "start" not in fake_kwin.methods


def test_the_script_is_taken_out_of_kwin_afterwards(kde, fake_kwin):
    """One left loaded would be started again by the next script's start(),
    bringing Firefox forward a second time; one from a run that stopped
    halfway goes before loading."""
    fake_kwin.windows = ['makeWindow("firefox", "{f}", 0, 0, 800, 600)']
    app_focus.focus_running(FIREFOX)
    assert fake_kwin.methods == ["unloadScript", "loadScript", "start", "unloadScript"]


def test_an_answer_from_an_earlier_script_never_counts(kde, fake_kwin):
    """Each attempt listens on a connection of its own: a slow script's late
    "yes" goes to a name that's gone, not to the next attempt."""
    fake_kwin.windows = ['makeWindow("firefox", "{f}", 0, 0, 800, 600)']
    app_focus.focus_running(FIREFOX)
    first = fake_kwin.scripts[-1]
    fake_kwin.silent = True
    app_focus.focus_running(FIREFOX, timeout=0.05)
    assert fake_kwin.scripts[-1] != first


# --- GNOME ------------------------------------------------------------------------------

_SHELL_XML = f"""<node><interface name="{app_focus.EXTENSION_INTERFACE}">
  <method name="ActivateApp">
    <arg type="s" direction="in" name="app_id"/>
    <arg type="b" direction="out" name="had_window"/>
  </method>
</interface></node>"""


class FakeShell:
    """GNOME Shell on the test's session bus, served from a thread of its
    own: with *extension*, TuxTalk's extension answers ActivateApp with
    *answer*, keeping each app id it's asked about."""

    def __init__(self, extension=True):
        self.answer = True
        self.called = []
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
            interface = Gio.DBusNodeInfo.new_for_xml(_SHELL_XML).interfaces[0]
            register = getattr(bus, "register_object_with_closures2", bus.register_object)
            register(app_focus.EXTENSION_PATH, interface, self._on_call, None, None)
        bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                      "RequestName", GLib.Variant("(su)", (app_focus.GNOME_SHELL, 4)), None,
                      Gio.DBusCallFlags.NONE, 5000, None)
        ready.set()
        self._loop.run()
        bus.close_sync(None)

    def _on_call(self, _bus, _sender, _path, _interface, _method, parameters, invocation):
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


def test_gnome_asks_the_extension(gnome, fake_extension):
    assert app_focus.focus_running(FIREFOX) is True
    assert fake_extension.called == ["firefox.desktop"]


def test_gnome_without_the_apps_window_says_launch(gnome, fake_extension):
    fake_extension.answer = False
    assert app_focus.focus_running(FIREFOX) is False


def test_gnome_without_the_extension_means_launch(gnome):
    """The extension turned off, or never installed (Ubuntu's GNOME with
    the tray instead): nobody to ask, so Firefox is started."""
    shell = FakeShell(extension=False)
    try:
        assert app_focus.focus_running(FIREFOX) is False
    finally:
        shell.stop()


def test_gnome_shell_not_on_the_bus_means_launch(gnome):
    assert app_focus.focus_running(FIREFOX) is False


# --- X11 --------------------------------------------------------------------------------

_CLIENT_MESSAGE = 33
_SUBSTRUCTURE_REDIRECT = 1 << 20
_CW_EVENT_MASK = 1 << 11
_ATOM_WINDOW, _ATOM_STRING, _ATOM_WM_CLASS = 33, 31, 67


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
    _desktop(monkeypatch, "XFCE", "x11")
    yield
    server.terminate()
    server.wait()


class FakeWindowManager:
    """An X client in the window manager's place: its windows, the list of
    them on the root window (_NET_CLIENT_LIST_STACKING), and the requests to
    activate one that reach it."""

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
        self.active_atom = self.atom(b"_NET_ACTIVE_WINDOW")

    def atom(self, name):
        """*name*'s atom, made if the X server has none yet, as a window
        manager makes the ones it supports."""
        reply = self.lib.xcb_intern_atom_reply(
            self.conn, self.lib.xcb_intern_atom(self.conn, 0, len(name), name), None)
        atom = reply.contents.atom
        xcb_ctypes.free(reply)
        return atom

    def window(self, instance, wm_class):
        """A new window with WM_CLASS *instance*, *wm_class*."""
        lib = self.lib
        window = lib.xcb_generate_id(self.conn)
        lib.xcb_create_window(self.conn, 0, window, self.root, 0, 0, 10, 10, 0, 1, 0, 0, None)
        raw = f"{instance}\0{wm_class}\0".encode()
        lib.xcb_change_property(self.conn, 0, window, _ATOM_WM_CLASS, _ATOM_STRING, 8,
                                len(raw), raw)
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

    def activations(self):
        """(window, source indication) of each _NET_ACTIVE_WINDOW request."""
        self.round_trip()
        got = []
        while event := self.lib.xcb_poll_for_event(self.conn):
            raw = ctypes.string_at(event, 32)
            xcb_ctypes.free(event)
            kind, form, _seq, window, atom, source = struct.unpack_from("=BBHIII", raw)
            if kind & 0x7f == _CLIENT_MESSAGE and form == 32 and atom == self.active_atom:
                got.append((window, source))
        return got

    def close(self):
        self.lib.xcb_disconnect(self.conn)


@pytest.fixture
def window_manager(x11):
    wm = FakeWindowManager()
    yield wm
    wm.close()


def test_x11_activates_through_net_active_window(window_manager):
    """Source indication 2, a taskbar's: window managers that keep other
    programs from stealing the focus still obey it."""
    firefox = window_manager.window("Navigator", "firefox")
    window_manager.stack([window_manager.window("xfce4-terminal", "Xfce4-terminal"), firefox])
    assert app_focus.focus_running(FIREFOX) is True
    assert window_manager.activations() == [(firefox, 2)]


def test_x11_picks_the_window_used_last(window_manager):
    older = window_manager.window("Navigator", "firefox")
    newer = window_manager.window("Navigator", "firefox")
    window_manager.stack([older, newer, window_manager.window("mousepad", "Mousepad")])
    assert app_focus.focus_running(FIREFOX) is True
    assert window_manager.activations() == [(newer, 2)]


def test_x11_matches_the_class_whatever_its_case(window_manager):
    """Older Firefox and many apps capitalise their class ("Firefox")."""
    firefox = window_manager.window("Navigator", "Firefox")
    window_manager.stack([firefox])
    assert app_focus.focus_running(FIREFOX) is True
    assert window_manager.activations() == [(firefox, 2)]


def test_x11_without_the_apps_window_says_launch(window_manager):
    window_manager.stack([window_manager.window("mousepad", "Mousepad")])
    assert app_focus.focus_running(FIREFOX) is False
    assert window_manager.activations() == []


def test_x11_without_a_window_manager_list_says_launch(window_manager):
    window_manager.window("Navigator", "firefox")
    assert app_focus.focus_running(FIREFOX) is False


def test_no_x_server_means_launch(monkeypatch):
    _desktop(monkeypatch, "XFCE", "x11")
    monkeypatch.setenv("DISPLAY", ":9999")
    assert app_focus.focus_running(FIREFOX) is False


# --- everywhere else ----------------------------------------------------------------------

def test_other_desktops_say_launch(cosmic, nobody_is_asked):
    """COSMIC, Sway, Hyprland: no way to bring a window forward is known, so
    the app is started; most then show their window."""
    assert app_focus.focus_running(FIREFOX) is False


def test_wayland_on_another_desktop_never_asks_xwayland(monkeypatch, nobody_is_asked):
    """XWayland knows only the X11 apps, and can't move the focus to them
    under a Wayland compositor."""
    _desktop(monkeypatch, "sway", "wayland")
    monkeypatch.setenv("DISPLAY", ":0")
    assert app_focus.focus_running(FIREFOX) is False


def test_an_error_means_launch_and_is_logged(gnome, monkeypatch, caplog):
    def broken(*_args):
        raise RuntimeError("the bus went away")
    monkeypatch.setattr(app_focus, "_ask_extension", broken)
    with caplog.at_level("INFO"):
        assert app_focus.focus_running(FIREFOX) is False
    assert "the bus went away" in caplog.text
