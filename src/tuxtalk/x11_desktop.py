"""Snap, centre, show the desktop, switch desktops and move windows between
desktops and screens on X11 (Cinnamon, Xfce, MATE), for the voice commands
"snap left", "go to desktop two" and so on.

TuxTalk asks the window manager through EWMH, as a pager does, and lets it
act. Snapping and centring are _NET_MOVERESIZE_WINDOW requests against the
monitor the window is on: EWMH's work area (_NET_WORKAREA, the screen less
panels) spans every monitor as one rectangle, so it is intersected with that
monitor's RandR rectangle, else a window snapped on the second screen would
land across both. A maximized or fullscreen window ignores a move, so it
is taken out of those states first. The panels' own struts are subtracted
too, since the work area misses a panel on a monitor smaller than the screen.

Answers are the strings desktop_actions turns into a spoken-style note:
"done", "no_window", "no_desktop", "one_desktop", "one_screen", "edge" and
"unknown"; None for "overview", which X11 desktops don't have.
"""
import logging

from . import x11_activate

log = logging.getLogger(__name__)

# _NET_MOVERESIZE_WINDOW's first word: gravity NorthWest (1), which of x, y,
# width and height are given (bits 8 to 11), the source indication in bits 12+.
_GRAVITY_NORTH_WEST = 1
_GIVEN_X, _GIVEN_Y, _GIVEN_SIZE = 1 << 8, 1 << 9, 3 << 10
_MOVE = _GRAVITY_NORTH_WEST | _GIVEN_X | _GIVEN_Y | x11_activate.FROM_A_TASKBAR << 12
_MOVE_AND_SIZE = _MOVE | _GIVEN_SIZE
_UNMAXIMIZE = 0
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


def _place(display, window, data: tuple):
    """Ask for *window*'s move (*data*) after taking it out of the states that
    make a window manager ignore one: maximized and fullscreen. Every atom
    is looked up before anything is sent, so a window manager without one
    leaves the window as it was, never half changed."""
    display.atom(b"_NET_MOVERESIZE_WINDOW")
    display.atom(b"_NET_WM_STATE")
    vert, horz, fullscreen = (display.atom(b"_NET_WM_STATE_" + state)
                              for state in (b"MAXIMIZED_VERT", b"MAXIMIZED_HORZ", b"FULLSCREEN"))
    source = x11_activate.FROM_A_TASKBAR
    display.ask_window_manager(window, b"_NET_WM_STATE", (_UNMAXIMIZE, vert, horz, source))
    display.ask_window_manager(window, b"_NET_WM_STATE", (_UNMAXIMIZE, fullscreen, 0, source))
    display.ask_window_manager(window, b"_NET_MOVERESIZE_WINDOW", data)


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


def _move_to(display, window, x: int, y: int):
    """Put *window*'s frame corner at (*x*, *y*)."""
    _place(display, window, (_MOVE, x, y))


def _centre(display, window) -> str:
    outer = _outer_rect(display, window)
    screens = monitors(display)
    if outer is None or not screens:
        return "no_window"
    rect = outer[0]
    ax, ay, aw, ah = _work_area(display, screens[_monitor_index(screens, rect)])
    _move_to(display, window, ax + (aw - rect[2]) // 2, ay + (ah - rect[3]) // 2)
    return "done"


def _to_next_screen(display, window) -> str:
    """Move *window* to the next monitor (the first after the last), keeping
    its offset in the monitor but clamped to fit inside it."""
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
    _move_to(display, window, x, y)
    return "done"


def _show_desktop(display) -> str:
    showing = display.cardinals(display.root, b"_NET_SHOWING_DESKTOP")
    display.ask_window_manager(display.root, b"_NET_SHOWING_DESKTOP",
                               (0 if showing and showing[0] else 1,))
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
