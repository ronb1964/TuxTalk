"""Which window has the focus, and whether the cursor is in a password field there.

Paste picks its shortcut by the focused window's class (Ctrl+Shift+V in a
terminal), and typing goes nowhere near a password field. Typing that waited
for a Right Ctrl or Right Alt dictation key, and an automatic send, also need
to know whether the window is still the one the text went to, so a look
(look()) says which window it is too, asking each source once:

  1. the tray, which the KWin script (kwin_focus.py) and the GNOME extension
     tell on every focus change: the class and the window's frame;
  2. Sway, i3, Hyprland, niri, COSMIC or X11 directly (compositor_focus.py).

Which window it is, is the compositor's own id for it wherever one is known:
X11's window, Sway's and i3's con id, Hyprland's address, niri's id. It
stays the same when the window moves, and tells two windows of one app
apart. On KDE and GNOME it's the window's frame, so there a window moved or
resized counts as another, and two maximized windows of one app on one
screen as the same. COSMIC tells only the class: there two windows of one
app are the same window to TuxTalk.

TuxTalk's own windows count as unknown: a stale report of Preferences must
not pick the paste shortcut for the app the user is really in. So does
wl-copy's brief window, which the tray already leaves out of its reports.
"""
import os

from .logger import setup_logger

logger = setup_logger(__name__)

# The tray answers from a cache in a millisecond; this is for one whose loop
# is busy (Preferences opening), which would otherwise leave the class
# unknown and pick the wrong paste shortcut.
TRAY_TIMEOUT_S = 1.5


def reported(timeout: float = TRAY_TIMEOUT_S):
    """(class, frame) the KWin script or GNOME extension last told the tray,
    each "" when not told; None when the tray couldn't be asked. The frame
    is "x,y,w,h|output|ox,oy,ow,oh" (window_geometry.parse_reported)."""
    import dbus
    from . import dbus_service
    from .dbus_service import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH
    try:
        bus = dbus.SessionBus()
        if bus.get_name_owner(DBUS_NAME) == bus.get_unique_name():
            # With no tray running, the dictation service holds the name
            # itself; a call to its own name would wait on the GTK loop that
            # is making it, and time out.
            return dbus_service.focused_window()
        proxy = bus.get_object(DBUS_NAME, DBUS_PATH, introspect=False)
        answer = proxy.GetFocusedWindow(dbus_interface=DBUS_INTERFACE, timeout=timeout)
    except dbus.exceptions.DBusException as e:   # no tray, or a busy one
        logger.debug(f"Could not ask the tray which window has the focus: {e}")
        return None
    return tuple(str(part) for part in answer)


def _not_a_target(wm_class) -> bool:
    from .desktop_identity import is_clipboard_tool_class, is_own_window_class
    return is_own_window_class(wm_class) or is_clipboard_tool_class(wm_class)


def look():
    """(class, which window) of the focused window, each None when not known.
    Safe from any thread."""
    from . import compositor_focus
    from .window_geometry import parse_reported
    wm_class, frame = reported() or ("", "")
    asked_class, which = compositor_focus.focused_window()
    wm_class = next((c for c in (wm_class, asked_class) if c and not _not_a_target(c)), None)
    if which is None:
        which = parse_reported(frame)
        if which is None and (rect := compositor_focus.focused_rect()):
            which = (rect, "", None)
    return wm_class, which


def window_class():
    """The focused window's class (or Wayland app_id), None when not known."""
    return look()[0]


def changed(before, after) -> bool:
    """Whether two looks at the focused window (tuples, a part None when it
    wasn't known) differ in a part both know. A D-Bus question that timed
    out isn't another window, and where only the class is known, nor is
    TuxTalk's own window: stopping for those would stop a dictation for
    nothing."""
    return any(a is not None and b is not None and a != b for a, b in zip(before, after))


def destination():
    """Where keys go now: (class, which window, each None when not known,
    and whether the cursor is in a password field)."""
    return (*look(), is_password())


def is_password() -> bool:
    """Whether the cursor is in a password field: one the accessibility bus
    knows (atspi_helper), or the desktop's own password prompt."""
    from . import atspi_helper
    return atspi_helper.focused_is_password() or _password_prompt_has_focus()


def _password_prompt_has_focus() -> bool:
    """The desktop's own password prompt has the focus. KDE's and LXQt's are
    polkit apps of their own, which only tell the accessibility bus with
    accessibility switched on; their window says what they are. Cinnamon
    and COSMIC draw theirs in the shell, over the app, which still counts as
    focused while the prompt takes every key: there a waiting prompt is
    enough (checked in the test VMs). With no prompt waiting nothing is
    asked: this runs before every typed line."""
    waiting = _polkit_prompt_waiting()
    if waiting is False:
        return False
    from .desktop_detect import get_desktop_environment
    if waiting and get_desktop_environment() in ("cinnamon", "cosmic"):
        return True
    focused = window_class()
    return focused is not None and any(word in focused.lower() for word in ("polkit", "policykit"))


def _polkit_prompt_waiting() -> bool | None:
    """Whether a polkit prompt waits for this user's password: while one
    does, polkit-agent-helper-1 runs, on every desktop. Another user's,
    logged in at the same time, doesn't count. None when /proc hides other
    users' processes (hidepid), so it can't be told."""
    if not os.path.exists("/proc/1"):
        return None
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                with open(f"/proc/{entry.name}/comm") as f:
                    if f.read().strip() != "polkit-agent-he":   # comm keeps 15 characters
                        continue
                with open(f"/proc/{entry.name}/status") as f:
                    uid = next(int(line.split()[1]) for line in f if line.startswith("Uid:"))
            except (OSError, StopIteration, ValueError):
                continue    # a process that ended meanwhile
            # Set-user-ID root, so the real user is whoever it asks for; a
            # socket-activated one (polkit 124 and later) runs as root itself.
            if uid in (os.getuid(), 0):
                return True
    return False
