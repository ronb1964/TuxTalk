"""Which window has the focus, and whether the cursor is in a password field there.

Paste picks its shortcut by the focused window's class (Ctrl+Shift+V in a
terminal), and typing goes nowhere near a password field. Typing that waited
for a Right Ctrl or Right Alt dictation key, and an automatic send, also need
to know whether the window is still the one the text went to, so a look
(look()) says which window it is too, asking each source once:

  1. the tray, which the KWin script (kwin_focus.py) and the GNOME extension
     tell on every focus change: the class, the window's own id and its frame;
  2. when they haven't told it all, Sway, i3, Hyprland, niri, COSMIC or X11
     directly (compositor_focus.py).

Which window it is, is the compositor's own id for it wherever one is known:
KWin's internalId, Mutter's window id, X11's window, Sway's and i3's con id,
Hyprland's address, niri's id. It stays the same when the window moves, and
tells two windows of one app apart. Each id is tagged with where it came
from, the tray or the compositor: on an X11 session of KDE or GNOME, a tray
that didn't answer in time leaves the look to X11, whose number for the same
window is another one. Without an id only the app is known, as on COSMIC,
which tells only the class: the GNOME extension from before it sent one
(still loaded until the user logs out and in after an update) tells the
window's frame too, but that changes when the window moves or is resized,
and two maximized windows of one app share it.

TuxTalk's own windows have no class here: the paste shortcut isn't picked
for them. Whether it is one of TuxTalk's still counts, wherever it was told
and with or without an id ("own"): an automatic Enter never goes into one,
where it would press its default button, and a key said after the words
only when they were dictated there. wl-copy's brief window, which the tray
leaves out of its reports, counts as unknown.
"""
import os
from typing import NamedTuple

from .logger import setup_logger

logger = setup_logger(__name__)

# How long any call to the tray may take: these questions, and the
# dictation service's notices to it (app.py). The tray answers from a cache
# in a millisecond; this is for one whose loop is busy (Preferences opening,
# a Whisper model loading), which would otherwise leave the class unknown
# and pick the wrong paste shortcut. dbus-python's default is 25 seconds, and
# the calls come from the typing thread and the hotkey loop, which holds
# every keyboard grabbed while recording: a wedged tray or session bus would
# freeze the keyboard that long. The bus daemon gets the same limit.
TRAY_TIMEOUT_S = 1.5


class Destination(NamedTuple):
    """Where keys go: the focused window's class and which window it is
    (look()), each None when not known, and whether the cursor is in a
    password field."""
    wm_class: str | None
    window: tuple | None
    password: bool = False


def reported(timeout: float = TRAY_TIMEOUT_S):
    """(class, id, frame) the KWin script or GNOME extension last told the
    tray, each "" when not told; None when the tray couldn't be asked. The
    frame is "x,y,w,h|output|ox,oy,ow,oh" (window_geometry.parse_reported)."""
    import dbus
    from . import dbus_service
    from .dbus_service import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH
    try:
        bus = dbus.SessionBus()
        owner = bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                  "org.freedesktop.DBus", "GetNameOwner", "s", (DBUS_NAME,),
                                  timeout=timeout)
        if owner == bus.get_unique_name():
            # With no tray running, the dictation service holds the name
            # itself; a call to its own name would wait on the GTK loop that
            # is making it, and time out.
            return tuple(dbus_service.cached_focus())
        # To the owner's unique name: dbus-python looks a well-known name up
        # again for each proxy, with no time limit.
        answer = bus.call_blocking(owner, DBUS_PATH, DBUS_INTERFACE, "GetFocusedWindow", "", (),
                                   timeout=timeout)
    except dbus.exceptions.DBusException as e:   # no tray, or a busy one
        logger.debug(f"Could not ask the tray which window has the focus: {e}")
        return None
    return tuple(str(part) for part in answer)


def look():
    """(class, which window) of the focused window, each None when not
    known. Which window is ("tray", id) or ("compositor", id); one of
    TuxTalk's own is ("own", that or None when it has no id), with no
    class. Safe from any thread."""
    from . import compositor_focus
    from .desktop_identity import is_clipboard_tool_class, is_own_window_class
    wm_class, tray_id, _frame = reported() or ("", "", "")
    if is_own_window_class(wm_class):
        return None, ("own", ("tray", tray_id) if tray_id else None)
    if wm_class and tray_id:
        return wm_class, ("tray", tray_id)
    asked_class, asked_id = compositor_focus.focused_window()
    asked = None if asked_id is None else ("compositor", asked_id)
    if not wm_class and is_own_window_class(asked_class):
        return None, ("own", asked)
    wm_class = next((c for c in (wm_class, asked_class)
                     if c and not is_clipboard_tool_class(c)), None)
    return wm_class, asked


def window_class():
    """The focused window's class (or Wayland app_id), None when not known."""
    return look()[0]


def same_window(before, after) -> bool | None:
    """Whether two looks at the focused window (look() or destination())
    are of one window: True when the desktop's id says so, False when they
    are known to differ (another app, another id, one TuxTalk's own and the
    other not), None when it can't be told: a part one look didn't get, or
    ids from two sources."""
    (class_a, window_a), (class_b, window_b) = before[:2], after[:2]
    # KWin lowercases the class ("google-chrome"); X11 has it as the app
    # set it ("Google-chrome"), and on Plasma's X11 session a look can come
    # from either.
    if class_a is not None and class_b is not None and class_a.casefold() != class_b.casefold():
        return False
    if is_own(before) != is_own(after) and None not in (window_a or class_a, window_b or class_b):
        return False
    id_a, id_b = _id(window_a), _id(window_b)
    if id_a is None or id_b is None or id_a[0] != id_b[0]:
        return None
    return id_a == id_b


def _id(window):
    """A look's (source, id), or None; TuxTalk's own windows have the same
    ids as every other window from that source."""
    return window[1] if window is not None and window[0] == "own" else window


def is_own(where) -> bool:
    """Whether *where* (a look or a destination) is one of TuxTalk's own windows."""
    return where[1] is not None and where[1][0] == "own"


def changed(before, after) -> bool:
    """Whether two looks are known to be of different windows, or two
    destinations differ in whether the cursor is in a password field. A
    D-Bus question that timed out isn't another window, nor is one where
    only the class is known: stopping typing for those would stop a
    dictation for nothing."""
    return same_window(before, after) is False or any(
        # A look has no password part; a destination does.
        a != b for a, b in zip(before[2:], after[2:], strict=False))


def claim_into(window, now=None):
    """virtual_keyboard.send's claim for keys that may only go into
    *window*, a look taken earlier: GaveUp("moved") when another window is
    known to have the focus *now* (a look, else one taken here), right
    before they go out."""
    from . import virtual_keyboard
    if changed(window, look() if now is None else now):
        logger.info("Not typed: another window has the focus now")
        raise virtual_keyboard.GaveUp("moved")
    return lambda: None


def destination() -> Destination:
    """Where keys go now. The password check goes first: it can take a
    while (the accessibility bus has a quarter of a second to answer), and
    a window looked at before it could have lost the focus by the time the
    keys go out. A caller for whom that matters looks once more before
    (send_countdown._claim): a focus that moved during the check may have
    been asked about the other window."""
    password = is_password()
    return Destination(*look(), password)


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


_PROC = "/proc"


def _polkit_prompt_waiting() -> bool | None:
    """Whether a polkit prompt waits for this user's password: while one
    does, polkit-agent-helper-1 runs, on every desktop. Another user's,
    logged in at the same time, doesn't count. None when /proc hides other
    users' processes (hidepid), so it can't be told."""
    if not os.path.exists(f"{_PROC}/1"):
        return None
    with os.scandir(_PROC) as entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                with open(f"{_PROC}/{entry.name}/comm") as f:
                    if f.read().strip() != "polkit-agent-he":   # comm keeps 15 characters
                        continue
                with open(f"{_PROC}/{entry.name}/status") as f:
                    uid = next(int(line.split()[1]) for line in f if line.startswith("Uid:"))
            except (OSError, StopIteration, ValueError):
                continue    # a process that ended meanwhile
            # Set-user-ID root, so the real user is whoever it asks for; a
            # socket-activated one (polkit 124 and later) runs as root itself.
            if uid in (os.getuid(), 0):
                return True
    return False
