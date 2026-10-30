"""Where the active window is, in the coordinates TuxTalk's own windows use.

Wayland doesn't let an app ask where other apps' windows are, so this asks
whoever does know, first answer wins:
  1. the tray's cache, which KDE's KWin script (kwin_focus.py) and the GNOME
     extension push to, the way they already push the window's class;
  2. Sway / i3 and Hyprland directly (compositor_focus.focused_rect);
  3. in an X11 session, the window manager, through GDK.
Nothing answered: None, and the caller uses the middle of the screen.

The answer is in the compositor's logical coordinates. TuxTalk's windows (the
recording indicator, screen_message) are drawn through X11 / XWayland, which
on a scaled screen counts physical pixels, so to_screen_rect() maps the
window's rectangle into GDK's monitor of the same name.
"""
import os
from collections import namedtuple

Rect = namedtuple("Rect", "x y w h")

_DBUS_TIMEOUT_S = 0.3     # the tray answers from a cache in a millisecond


def _rect(text):
    """"x,y,w,h" -> Rect; raises ValueError for anything else."""
    parts = [int(round(float(p))) for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError(text)
    return Rect(*parts)


def parse_reported(text):
    """("x,y,w,h|output|ox,oy,ow,oh") -> (window Rect, output name, output Rect),
    or None when there is no usable report."""
    try:
        window, output, output_rect = text.split("|")
        return _rect(window), output, _rect(output_rect)
    except (ValueError, AttributeError):
        return None


def _mapper(output, output_rect, monitors):
    """A function taking a compositor point to where GDK draws it.

    *monitors* is [(name, Rect)] as GDK sees them. A point keeps its offset
    within its monitor, scaled from the compositor's size of that monitor to
    GDK's. Without a matching name a single monitor is still used; with
    several, or no monitor size at all, coordinates are taken as they are."""
    one_to_one = lambda x, y: (round(x), round(y))
    if output_rect is None or output_rect.w <= 0 or output_rect.h <= 0:
        return one_to_one
    match = [m for name, m in monitors if output and name == output]
    if not match and len(monitors) == 1:
        match = [monitors[0][1]]
    if not match:
        return one_to_one
    mon = match[0]
    sx, sy = mon.w / output_rect.w, mon.h / output_rect.h
    return lambda x, y: (round(mon.x + (x - output_rect.x) * sx),
                         round(mon.y + (y - output_rect.y) * sy))


def to_screen_rect(rect, output, output_rect, monitors):
    """*rect* (compositor coordinates) where GDK draws it."""
    to_screen = _mapper(output, output_rect, monitors)
    x0, y0 = to_screen(rect.x, rect.y)
    x1, y1 = to_screen(rect.x + rect.w, rect.y + rect.h)
    return Rect(x0, y0, x1 - x0, y1 - y0)


def screen_monitors():
    """[(name, Rect)] for each monitor as GDK sees it. The name is the
    connector ("DP-6") where GDK knows it; none without a display."""
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if display is None:
        return []
    out = []
    for i in range(display.get_n_monitors()):
        mon = display.get_monitor(i)
        g = mon.get_geometry()
        out.append((mon.get_model() or "", Rect(g.x, g.y, g.width, g.height)))
    return out


def _reported():
    """Where the KWin script or GNOME extension last told the tray the
    focused window is, or None."""
    from .focused_window import reported
    frame = (reported(timeout=_DBUS_TIMEOUT_S) or ("", "", ""))[-1]
    return frame or None


def _x11_active_rect():
    """The active window's frame in an X11 session, from the window manager.
    Not under Wayland: XWayland knows only the X11 apps."""
    from .desktop_detect import is_x11_session
    if not is_x11_session(os.environ):
        return None
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    screen = Gdk.Screen.get_default()
    window = screen.get_active_window() if screen is not None else None
    if window is None:
        return None
    g = window.get_frame_extents()
    return Rect(g.x, g.y, g.width, g.height)


_ASK = object()          # active_window_rect(): look the window up now


def active_window_report():
    """(window Rect, output name, output Rect) in the compositor's coordinates,
    from the sources that need no GDK (the tray's cache, Sway / Hyprland), or
    None. Safe from any thread."""
    from . import compositor_focus
    reported = _reported()
    parsed = parse_reported(reported) if reported else None
    if parsed:
        return parsed
    rect = compositor_focus.focused_rect()
    return (rect, "", None) if rect else None


def active_window_rect(report=_ASK):
    """The active window's frame where TuxTalk's own windows are drawn, or
    None when nothing could say. *report* is an active_window_report() taken
    earlier. GTK thread only: GDK lists the monitors, and finds the window
    in an X11 session."""
    if report is _ASK:
        report = active_window_report()
    if report is None:
        rect = _x11_active_rect()
        report = (rect, "", None) if rect else None
    return to_screen_rect(*report, screen_monitors()) if report else None


def active_window_center(report=_ASK):
    """The middle of the active window where TuxTalk's own windows are drawn,
    or None when nothing could say. GTK thread only, as active_window_rect."""
    rect = active_window_rect(report)
    return (round(rect.x + rect.w / 2), round(rect.y + rect.h / 2)) if rect else None
