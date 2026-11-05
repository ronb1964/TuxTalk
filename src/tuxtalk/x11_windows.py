"""Close, minimize and maximize windows on X11 (Xfce, MATE, Cinnamon, i3...),
for the voice commands "close Firefox", "minimize window" and so on.

TuxTalk asks the window manager, as a taskbar does, and lets it act: closing
is _NET_CLOSE_WINDOW, which the window manager turns into the app's own
"please close" (WM_DELETE_WINDOW), so an app with unsaved work still asks to
save. Never XKillClient, which would lose that work. Minimizing is ICCCM's
WM_CHANGE_STATE, which every window manager knows; maximizing is EWMH's
_NET_WM_STATE. The windows are found as x11_activate finds them.
"""
import logging

from . import x11_activate

log = logging.getLogger(__name__)

_ICONIC_STATE = 3
_REMOVE, _ADD = 0, 1
_CURRENT_TIME = 0


def _requests(display, action: str, named: bool) -> list:
    """The (message type, data) requests that do *action* to a window.
    Maximizing a *named* app's window and restoring one also ask for it to
    come forward, which un-minimizes it, as for switch to."""
    if action == "close":
        return [(b"_NET_CLOSE_WINDOW", (_CURRENT_TIME, x11_activate.FROM_A_TASKBAR))]
    if action == "minimize":
        return [(b"WM_CHANGE_STATE", (_ICONIC_STATE,))]
    change = _ADD if action == "maximize" else _REMOVE
    requests = [(b"_NET_WM_STATE", (change, display.atom(b"_NET_WM_STATE_MAXIMIZED_VERT"),
                                    display.atom(b"_NET_WM_STATE_MAXIMIZED_HORZ"),
                                    x11_activate.FROM_A_TASKBAR))]
    if action == "restore" or (action == "maximize" and named):
        requests.append((b"_NET_ACTIVE_WINDOW", (x11_activate.FROM_A_TASKBAR,)))
    return requests


def _targets(display, desktop_file: str, names, every: bool) -> list:
    """The windows to act on, never the desktop's own: with no *desktop_file*
    and no *names*, the focused one; else the app's used last, or all of them with *every*."""
    if not desktop_file and not names:
        active = display.active_window()
        return display.app_windows([active]) if active else []
    found = display.matching(desktop_file, names)
    return found if every else found[:1]


def act(action: str, desktop_file: str, names, every: bool) -> bool:
    """Ask the window manager to do *action* ("close", "minimize",
    "maximize", "unmaximize" or "restore"; "find" asks nothing) to the app's
    windows (*desktop_file* and *names* as x11_activate.activate takes them)
    or, with neither, to the focused window. True when it was asked about a
    window; False with no X server or no such window. Raises OSError when
    the window manager doesn't take the request or the X server refuses it."""
    display = x11_activate.open_display()
    if display is None:
        log.info("No X server to ask about the open windows")
        return False
    try:
        targets = _targets(display, desktop_file, names, every)
        if targets and action != "find":
            requests = _requests(display, action, bool(desktop_file or names))
            for window in targets:
                for message_type, data in requests:
                    display.ask_window_manager(window, message_type, data)
        return bool(targets)
    finally:
        display.close()
