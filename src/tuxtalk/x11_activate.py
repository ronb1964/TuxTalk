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
compositor_focus's look at the focused window. x11_windows asks the window
manager to close, minimize and maximize the same way.
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
FROM_A_TASKBAR = 2
_ENTRY_ID_PROPERTIES = (b"_GTK_APPLICATION_ID", b"_KDE_NET_WM_DESKTOP_FILE")


def _client_message(window: int, atom: int, data: tuple) -> bytes:
    """The 32-byte ClientMessage *atom* about *window*, its *data* (up to
    five 32-bit words) padded with 0."""
    words = tuple(data) + (0,) * (5 - len(data))
    return struct.pack("=BBHII5I", _CLIENT_MESSAGE, 32, 0, window, atom, *words)


class Display:
    """One XCB connection to $DISPLAY and its first screen's root window."""

    def __init__(self, lib, conn, root):
        self._lib, self._conn, self._root = lib, conn, root

    def atom(self, name: bytes) -> int:
        """*name*'s atom. Raises OSError when the X server has none: no
        window manager here has said it supports *name*."""
        atom = intern_atom(self._lib, self._conn, name)
        if not atom:
            raise OSError(f"no window manager here takes {name.decode()}")
        return atom

    def windows(self) -> list:
        """The window manager's windows, the one used last at the end; []
        when no window manager lists them."""
        for name in (b"_NET_CLIENT_LIST_STACKING", b"_NET_CLIENT_LIST"):
            atom = intern_atom(self._lib, self._conn, name)
            raw = get_property(self._lib, self._conn, self._root, atom) if atom else None
            if raw:
                return list(struct.unpack(f"={len(raw) // 4}I", raw[:len(raw) // 4 * 4]))
        return []

    def active_window(self):
        """The window that has the focus (_NET_ACTIVE_WINDOW on the root
        window), or None when the window manager names none."""
        atom = intern_atom(self._lib, self._conn, b"_NET_ACTIVE_WINDOW")
        raw = get_property(self._lib, self._conn, self._root, atom) if atom else None
        window = struct.unpack_from("=I", raw)[0] if raw and len(raw) >= 4 else 0
        return window or None

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

    def matching(self, desktop_file: str, names) -> list:
        """The windows that name the menu entry *desktop_file*
        ("org.x.editor") or whose class, without case, is in *names*
        (app_focus.window_names): the one used last first."""
        wanted, atoms = desktop_file.casefold(), self.entry_atoms()
        return [window for window in reversed(self.windows())
                if wanted in self.entry_ids(window, atoms)
                or self.wm_class(window).casefold() in names]

    def ask_window_manager(self, window: int, message_type: bytes, data: tuple) -> None:
        """Send the window manager the request *message_type* about
        *window* (an EWMH or ICCCM ClientMessage to the root window, as a
        taskbar sends), with *data*. Raises OSError when there's no window
        manager to ask or the X server refuses."""
        message = _client_message(window, self.atom(message_type), data)
        error = self._lib.xcb_request_check(self._conn, self._lib.xcb_send_event_checked(
            self._conn, 0, self._root, _TO_THE_WINDOW_MANAGER, message))
        if error:
            code = error.contents.error_code
            free(error)
            raise OSError(f"the X server refused the request (error {code})")

    def close(self):
        self._lib.xcb_disconnect(self._conn)


def open_display():
    """A Display on $DISPLAY, or None when there is no X server to reach."""
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
    return Display(lib, conn, root)


def activate(desktop_file: str, names) -> bool:
    """Bring forward the window used last that names the menu entry
    *desktop_file* ("org.x.editor") or whose class, without case, is in
    *names* (app_focus.window_names). True when one was asked for."""
    display = open_display()
    if display is None:
        log.info("No X server to ask about the open windows")
        return False
    try:
        found = display.matching(desktop_file, names)
        if found:
            # From a taskbar, at no particular time (CurrentTime, 0), with no
            # window of ours active.
            display.ask_window_manager(found[0], b"_NET_ACTIVE_WINDOW", (FROM_A_TASKBAR,))
        return bool(found)
    finally:
        display.close()
