"""Bring an app's window forward on X11 (Xfce, MATE, Cinnamon, LXQt, i3...),
for "open Firefox" with Firefox already open (app_focus.py).

The window manager lists the windows it manages on the root window
(_NET_CLIENT_LIST_STACKING, bottom to top, or else _NET_CLIENT_LIST, oldest
first), and WM_CLASS says whose each one is; GTK and KDE apps also name
their menu entry on the window (_GTK_APPLICATION_ID, _KDE_NET_WM_DESKTOP_FILE),
which Mint's own apps need: xed's entry is org.x.editor, its class Xed.
To bring one forward a client
asks the window manager with a _NET_ACTIVE_WINDOW message to the root
window, as a taskbar does (EWMH); the window manager switches desktops and
unminimizes as it would for a click there. Through XCB (xcb_ctypes), like
compositor_focus's look at the focused window.
"""
import ctypes
import logging
import struct

from .compositor_focus import XCB_ATOM_WM_CLASS, parse_wm_class
from .xcb_ctypes import free, get_property, intern_atom, x11_root, xcb_lib

log = logging.getLogger(__name__)

_CLIENT_MESSAGE = 33
# Who may hear a message to the root window: the window manager listens for
# SubstructureRedirect there.
_TO_THE_WINDOW_MANAGER = (1 << 19) | (1 << 20)     # SubstructureNotify | Redirect
# EWMH's source indication for a pager or taskbar: window managers that keep
# apps from stealing the focus still obey it, as the user asked for it.
_FROM_A_TASKBAR = 2
_ENTRY_ID_PROPERTIES = (b"_GTK_APPLICATION_ID", b"_KDE_NET_WM_DESKTOP_FILE")


def active_window_request(window: int, atom: int) -> bytes:
    """The 32-byte ClientMessage asking to activate *window* (EWMH's
    _NET_ACTIVE_WINDOW, *atom*): from a taskbar, at no particular time
    (CurrentTime), with no window of ours active."""
    return struct.pack("=BBHII5I", _CLIENT_MESSAGE, 32, 0, window, atom,
                       _FROM_A_TASKBAR, 0, 0, 0, 0)


class _Display:
    """One XCB connection to $DISPLAY and its first screen's root window."""

    def __init__(self, lib, conn, root):
        self._lib, self._conn, self._root = lib, conn, root

    def windows(self) -> list:
        """The window manager's windows, the one used last at the end; []
        when no window manager lists them."""
        for name in (b"_NET_CLIENT_LIST_STACKING", b"_NET_CLIENT_LIST"):
            atom = intern_atom(self._lib, self._conn, name)
            raw = get_property(self._lib, self._conn, self._root, atom) if atom else None
            if raw:
                return list(struct.unpack(f"={len(raw) // 4}I", raw[:len(raw) // 4 * 4]))
        return []

    def wm_class(self, window) -> str:
        """The class part of *window*'s WM_CLASS ("firefox"), "" when unset."""
        raw = get_property(self._lib, self._conn, window, XCB_ATOM_WM_CLASS)
        return (parse_wm_class(raw) if raw else None) or ""

    def entry_atoms(self) -> list:
        """The atoms of the properties naming a window's menu entry that the
        X server has; one no window has set yet isn't there."""
        atoms = (intern_atom(self._lib, self._conn, name) for name in _ENTRY_ID_PROPERTIES)
        return [atom for atom in atoms if atom]

    def entry_ids(self, window, atoms) -> set:
        """The menu entries *window* says it belongs to, without case
        ("org.x.editor")."""
        raw = (get_property(self._lib, self._conn, window, atom) for atom in atoms)
        return {r.decode("utf-8", "replace").strip("\0").casefold() for r in raw if r}

    def activate(self, window) -> None:
        """Ask the window manager to bring *window* forward. Raises OSError
        when there's no window manager to ask or the X server refuses."""
        atom = intern_atom(self._lib, self._conn, b"_NET_ACTIVE_WINDOW")
        if not atom:
            raise OSError("no window manager here takes _NET_ACTIVE_WINDOW")
        error = self._lib.xcb_request_check(self._conn, self._lib.xcb_send_event_checked(
            self._conn, 0, self._root, _TO_THE_WINDOW_MANAGER,
            active_window_request(window, atom)))
        if error:
            code = error.contents.error_code
            free(error)
            raise OSError(f"the X server refused the request (error {code})")

    def close(self):
        self._lib.xcb_disconnect(self._conn)


def _open():
    """A _Display on $DISPLAY, or None when there is no X server to reach."""
    lib = xcb_lib()
    screen_number = ctypes.c_int(0)
    conn = lib.xcb_connect(None, ctypes.byref(screen_number))
    if not conn:
        return None
    root = None if lib.xcb_connection_has_error(conn) else x11_root(lib, conn,
                                                                     screen_number.value)
    if not root:
        lib.xcb_disconnect(conn)
        return None
    return _Display(lib, conn, root)


def activate(desktop_file: str, names) -> bool:
    """Bring forward the window used last that names the menu entry
    *desktop_file* ("org.x.editor") or whose class, without case, is in
    *names* (app_focus.window_names). True when one was asked for."""
    display = _open()
    if display is None:
        log.info("No X server to ask about the open windows")
        return False
    try:
        wanted, atoms = desktop_file.casefold(), display.entry_atoms()
        for window in reversed(display.windows()):
            if (wanted in display.entry_ids(window, atoms)
                    or display.wm_class(window).casefold() in names):
                display.activate(window)
                return True
        return False
    finally:
        display.close()
