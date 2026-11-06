"""Snap, centre, show the desktop, switch desktops and move windows between
desktops and screens on X11 (Cinnamon, Xfce, MATE), for the voice commands
"snap left", "go to desktop two" and so on.

TuxTalk asks the window manager through EWMH, as a pager does, and lets it
act. Snapping and centring are _NET_MOVERESIZE_WINDOW requests against the
monitor the window is on: EWMH's work area (_NET_WORKAREA, the screen less
panels) spans every monitor as one rectangle, so it is intersected with that
monitor's RandR rectangle, else a window snapped on the second screen would
land across both. A maximized or fullscreen window ignores a move, so it
is taken out of those states first: maximized again on the other screen
when it was, and centred at the size it comes back to, read again after a
moment. The panels' own struts are subtracted too, since the work area
misses a panel on a monitor smaller than the screen.

popup_ends_show_desktop() tells the recording indicator and the floating
labels to stay away while Cinnamon shows the desktop.

Answers are the strings desktop_actions turns into a spoken-style note:
"done", "no_window", "no_desktop", "one_desktop", "one_screen", "edge" and
"unknown"; None for "overview", which X11 desktops don't have.
"""
import logging
import threading
import time

from . import desktop_detect, x11_activate

log = logging.getLogger(__name__)

# _NET_MOVERESIZE_WINDOW's first word: gravity NorthWest (1: x and y are the
# frame's corner), which of x, y, width and height are given (bits 8 to 11),
# the source indication in bits 12+.
_GRAVITY_NORTH_WEST = 1
_GIVEN_X, _GIVEN_Y, _GIVEN_SIZE = 1 << 8, 1 << 9, 3 << 10
_MOVE = _GRAVITY_NORTH_WEST | _GIVEN_X | _GIVEN_Y | x11_activate.FROM_A_TASKBAR << 12
_MOVE_AND_SIZE = _MOVE | _GIVEN_SIZE
_STATE_REMOVE, _STATE_ADD = 0, 1
_MAXIMIZED = (b"_NET_WM_STATE_MAXIMIZED_VERT", b"_NET_WM_STATE_MAXIMIZED_HORZ")
_SIZED_BY_STATE = (*_MAXIMIZED, b"_NET_WM_STATE_FULLSCREEN")
# How long a maximized window may take to come back at its own size before
# it is centred; then how often its frame is looked at.
_RESTORE_WAIT_S = 0.5
_RESTORE_LOOK_S = 0.05
# How long the X server gets to say whether the desktop is shown, and the
# question still being asked (its thread, and its answer once given): one at
# most, however long the server hangs.
_ASK_WAIT_S = 0.5
_asking = None
_asking_lock = threading.Lock()
_NO_FRAME = (0, 0, 0, 0)

# Where each snap goes, in halves of the monitor: first column, last column,
# first row, last row.
_SNAP_PLACES = {
    "snap left": (0, 1, 0, 2), "snap right": (1, 2, 0, 2),
    "snap top": (0, 2, 0, 1), "snap bottom": (0, 2, 1, 2),
    "snap top left": (0, 1, 0, 1), "snap top right": (1, 2, 0, 1),
    "snap bottom left": (0, 1, 1, 2), "snap bottom right": (1, 2, 1, 2),
}
_WINDOW_ACTIONS = (*_SNAP_PLACES, "center", "to next screen", "to desktop")
_DESKTOP_STEPS = {"next desktop": 1, "previous desktop": -1,
                  "to next desktop": 1, "to previous desktop": -1}


def monitors(display) -> list:
    """(x, y, width, height) of each monitor: RandR's, else the whole
    screen as one."""
    found = display.randr_monitors()
    if found:
        return found
    whole = display.geometry(display.root)
    return [whole] if whole else []


def _overlap(first, second):
    """The rectangle two (x, y, width, height) rectangles share, or None."""
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    return (left, top, right - left, bottom - top) if right > left and bottom > top else None


def _monitor_index(screens, rect) -> int:
    """Which of *screens* holds the centre of *rect* (the first when none does)."""
    cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
    for index, (x, y, width, height) in enumerate(screens):
        if x <= cx < x + width and y <= cy < y + height:
            return index
    return 0


def _current_desktop(display) -> int:
    return (display.cardinals(display.root, b"_NET_CURRENT_DESKTOP") or [0])[0]


def _desktop_count(display) -> int:
    return (display.cardinals(display.root, b"_NET_NUMBER_OF_DESKTOPS") or [1])[0]


def _work_area(display, screen):
    """*screen* less the panels: its overlap with the current desktop's work
    area, or the whole *screen* when the window manager names none."""
    areas = display.cardinals(display.root, b"_NET_WORKAREA")
    start = _current_desktop(display) * 4
    if len(areas) >= start + 4:
        screen = _overlap(screen, tuple(areas[start:start + 4])) or screen
    return _less_panels(display, screen)


def _less_panels(display, area):
    """*area* less the panels on its monitor. _NET_WORKAREA is computed from
    struts at the screen's edges, so a panel on a smaller monitor than the
    screen's edge leaves it untouched; each panel's own strut says where it is."""
    whole = display.geometry(display.root)
    left, top, right, bottom = area[0], area[1], area[0] + area[2], area[1] + area[3]
    for panel in display.docks():
        strut = display.cardinals(panel, b"_NET_WM_STRUT_PARTIAL")
        if len(strut) < 12 or whole is None:
            continue
        for side, (rect, edge) in enumerate(_strut_rects(strut, whole)):
            if not edge or _overlap(area, rect) is None:
                continue
            if side == 0:
                left = max(left, rect[0] + rect[2])
            elif side == 1:
                right = min(right, rect[0])
            elif side == 2:
                top = max(top, rect[1] + rect[3])
            else:
                bottom = min(bottom, rect[1])
    return left, top, right - left, bottom - top


def _strut_rects(strut, whole):
    """For a _NET_WM_STRUT_PARTIAL, the (rectangle, thickness) it keeps clear
    on the left, right, top and bottom of the screen *whole*."""
    sw, sh = whole[2], whole[3]
    left, right, top, bottom = strut[:4]
    return [((0, strut[4], left, strut[5] - strut[4] + 1), left),
            ((sw - right, strut[6], right, strut[7] - strut[6] + 1), right),
            ((strut[8], 0, strut[9] - strut[8] + 1, top), top),
            ((strut[10], sh - bottom, strut[11] - strut[10] + 1, bottom), bottom)]


def _outer_rect(display, window):
    """*window*'s frame (x, y, width, height) and the frame's (left, right,
    top, bottom) thickness; None when the window is gone."""
    rect = display.geometry(window)
    if rect is None:
        return None
    left, right, top, bottom = (display.cardinals(window, b"_NET_FRAME_EXTENTS")
                                or list(_NO_FRAME))
    return (rect[0] - left, rect[1] - top, rect[2] + left + right, rect[3] + top + bottom), \
        (left, right, top, bottom)


def _unmaximize_requests(display, window) -> list:
    """The requests that take *window* out of maximized and fullscreen,
    which make a window manager ignore a move."""
    vert, horz = (display.atom(state) for state in _MAXIMIZED)
    fullscreen = display.atom(b"_NET_WM_STATE_FULLSCREEN")
    source = x11_activate.FROM_A_TASKBAR
    return [display.request(window, b"_NET_WM_STATE", (_STATE_REMOVE, vert, horz, source)),
            display.request(window, b"_NET_WM_STATE", (_STATE_REMOVE, fullscreen, 0, source))]


def _place(display, window, data: tuple, maximize_after: bool = False):
    """Ask for *window*'s move (*data*) after taking it out of maximized and
    fullscreen; with *maximize_after*, maximized again where it lands. Every
    request is made before any is sent, so a window manager without one of
    them leaves the window as it was, never half changed."""
    messages = _unmaximize_requests(display, window)
    messages.append(display.request(window, b"_NET_MOVERESIZE_WINDOW", data))
    if maximize_after:
        vert, horz = (display.atom(state) for state in _MAXIMIZED)
        messages.append(display.request(window, b"_NET_WM_STATE",
                                        (_STATE_ADD, vert, horz, x11_activate.FROM_A_TASKBAR)))
    for message in messages:
        display.send(message)


def _has_state(display, window, names) -> bool:
    """Whether *window*'s _NET_WM_STATE holds any of *names*."""
    states = set(display.cardinals(window, b"_NET_WM_STATE"))
    return any(display.atom(name) in states for name in names)


def _maximized(display, window) -> bool:
    """Whether *window* is maximized both ways (_NET_WM_STATE)."""
    states = set(display.cardinals(window, b"_NET_WM_STATE"))
    return {display.atom(state) for state in _MAXIMIZED} <= states


def _snap(display, window, action: str) -> str:
    """Resize and place *window* to the part of its monitor's work area that
    *action* names; the size asked for is the content's, so the frame's
    thickness comes off it."""
    outer = _outer_rect(display, window)
    if outer is None:
        return "no_window"
    rect, (left, right, top, bottom) = outer
    screens = monitors(display)
    if not screens:
        return "no_window"
    ax, ay, aw, ah = _work_area(display, screens[_monitor_index(screens, rect)])
    col0, col1, row0, row1 = _SNAP_PLACES[action]
    x, y = ax + aw * col0 // 2, ay + ah * row0 // 2
    width, height = ax + aw * col1 // 2 - x, ay + ah * row1 // 2 - y
    _place(display, window, (_MOVE_AND_SIZE, x, y, width - left - right, height - top - bottom))
    return "done"


def _restored(display, window, before):
    """*window*'s frame once the window manager has given it its own size
    back after an un-maximize, read again every _RESTORE_LOOK_S for up to
    _RESTORE_WAIT_S; None when it hasn't changed from *before* by then."""
    for _ in range(round(_RESTORE_WAIT_S / _RESTORE_LOOK_S)):
        time.sleep(_RESTORE_LOOK_S)
        outer = _outer_rect(display, window)
        if outer is None:
            return None
        if outer[0] != before:
            return outer[0]
    return None


def _centre(display, window) -> str:
    """Put *window*'s frame in the middle of its work area, by its corner.
    A maximized (or fullscreen) window is un-maximized first and centred at
    the size it comes back to; one whose size hasn't come back in time is
    left un-maximized where the window manager put it, not moved by a guess."""
    outer = _outer_rect(display, window)
    screens = monitors(display)
    if outer is None or not screens:
        return "no_window"
    rect = outer[0]
    ax, ay, aw, ah = _work_area(display, screens[_monitor_index(screens, rect)])
    if _has_state(display, window, _SIZED_BY_STATE):
        display.atom(b"_NET_MOVERESIZE_WINDOW")     # never un-maximized without the move
        for message in _unmaximize_requests(display, window):
            display.send(message)
        rect = _restored(display, window, rect)
        if rect is None:
            log.info("Centre: the window's own size didn't come back in time; un-maximized only")
            return "done"
    _place(display, window, (_MOVE, ax + (aw - rect[2]) // 2, ay + (ah - rect[3]) // 2))
    return "done"


def _to_next_screen(display, window) -> str:
    """Move *window* to the next monitor (the first after the last), keeping
    its offset in the monitor but clamped to fit inside it, and maximized
    there if it was here, as KDE and GNOME keep it."""
    outer = _outer_rect(display, window)
    screens = monitors(display)
    if outer is None:
        return "no_window"
    if len(screens) < 2:
        return "one_screen"
    rect = outer[0]
    index = _monitor_index(screens, rect)
    here = screens[index]
    mx, my, mw, mh = screens[(index + 1) % len(screens)]
    x = min(max(mx + rect[0] - here[0], mx), max(mx, mx + mw - rect[2]))
    y = min(max(my + rect[1] - here[1], my), max(my, my + mh - rect[3]))
    _place(display, window, (_MOVE, x, y), maximize_after=_maximized(display, window))
    return "done"


def _showing(display) -> bool:
    """Whether the window manager is showing the desktop instead of the windows."""
    shown = display.cardinals(display.root, b"_NET_SHOWING_DESKTOP")
    return bool(shown and shown[0])


def _show_desktop(display) -> str:
    display.ask_window_manager(display.root, b"_NET_SHOWING_DESKTOP",
                               (0 if _showing(display) else 1,))
    return "done"


def _desktop_after(display, step: int):
    """The index of the desktop *step* from the current one, None past the ends."""
    target = _current_desktop(display) + step
    return target if 0 <= target < _desktop_count(display) else None


def _desktops(display, action: str, number: int | None, window) -> str:
    """The desktop commands: switching, and moving *window* (None for the
    switching ones)."""
    if _desktop_count(display) == 1 and action in _DESKTOP_STEPS:
        return "one_desktop"
    if action in _DESKTOP_STEPS:
        index = _desktop_after(display, _DESKTOP_STEPS[action])
    elif number is None:
        return "unknown"
    else:
        index = number - 1 if 1 <= number <= _desktop_count(display) else None
    if index is None:
        return "edge" if action in _DESKTOP_STEPS else "no_desktop"
    if window is None:
        display.ask_window_manager(display.root, b"_NET_CURRENT_DESKTOP", (index, 0))
    else:
        display.ask_window_manager(window, b"_NET_WM_DESKTOP", (index, x11_activate.FROM_A_TASKBAR))
    return "done"


def front_window_open() -> bool:
    """Whether an app window has the focus (not the desktop, a panel or
    nothing at all); False where there's no X server to ask."""
    try:
        display = x11_activate.open_display()
        if display is None:
            return False
        try:
            return _front_window(display) is not None
        finally:
            display.close()
    except OSError as e:
        log.info(f"Couldn't ask which window has the focus: {e}")
        return False


def _front_window(display):
    active = display.active_window()
    found = display.app_windows([active]) if active else []
    return found[0] if found else None


def _on_window(display, action: str, number: int | None) -> str:
    window = _front_window(display)
    if window is None:
        return "no_window"
    if action in _SNAP_PLACES:
        return _snap(display, window, action)
    if action == "center":
        return _centre(display, window)
    if action == "to next screen":
        return _to_next_screen(display, window)
    return _desktops(display, action, number, window)


def popup_ends_show_desktop() -> bool:
    """Whether a popup appearing now would end "show desktop": on Cinnamon,
    any window mapped, an override-redirect one too, ends it and brings the
    windows back (tried in the Mint VM; KWin, Mutter and xfwm4 keep it). So
    TuxTalk's indicator and floating labels stay away while the desktop is
    shown there. Asked as a recording starts, on the thread that also reads
    the keys, and before a label floats: an X server that doesn't answer
    within _ASK_WAIT_S counts as "it would", so the key's release and Esc
    are read all the same. A question still running is waited for, not
    asked again."""
    global _asking
    if desktop_detect.get_desktop_environment() != "cinnamon":
        return False
    with _asking_lock:
        if _asking is None or not _asking[0].is_alive():
            answer = []
            asking = threading.Thread(target=lambda: answer.append(_desktop_shown()),
                                      daemon=True, name="show-desktop-question")
            asking.start()
            _asking = (asking, answer)
        asking, answer = _asking
    asking.join(_ASK_WAIT_S)
    if not answer:
        log.info("The X server didn't say in time whether the desktop is shown")
        return True
    return answer[0]


def _desktop_shown() -> bool:
    """_showing() on a connection of its own; False where it can't be told.
    It runs on a thread of its own, so any error is logged here: uncaught,
    it would only reach stderr, and the log would blame a slow X server."""
    try:
        display = x11_activate.open_display()
        if display is None:
            return False
        try:
            return _showing(display)
        finally:
            display.close()
    except Exception as e:
        log.warning(f"Couldn't ask whether the desktop is shown: {type(e).__name__}: {e}")
        return False


def act(action: str, number: int | None):
    """Do *action* ("snap left", "center", "to next screen", "show desktop",
    "desktop" or "to desktop" with *number*, "next desktop", "to next
    desktop", ...) and answer as the module's docstring says. Raises OSError
    when there's no window manager to ask or the X server refuses."""
    if action == "overview":
        return None
    display = x11_activate.open_display()
    if display is None:
        log.info("No X server to ask about the desktop")
        return "no_window"
    try:
        if action == "show desktop":
            return _show_desktop(display)
        if action in ("desktop", "next desktop", "previous desktop"):
            return _desktops(display, action, number, None)
        if action in _WINDOW_ACTIONS or action in _DESKTOP_STEPS:
            return _on_window(display, action, number)
        return "unknown"
    finally:
        display.close()
