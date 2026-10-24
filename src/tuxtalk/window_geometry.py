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

from .logger import setup_logger

logger = setup_logger(__name__)

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
    connector ("DP-6") where GDK knows it."""
    try:
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        out = []
        for i in range(display.get_n_monitors()):
            mon = display.get_monitor(i)
            g = mon.get_geometry()
            out.append((mon.get_model() or "", Rect(g.x, g.y, g.width, g.height)))
        return out
    except Exception as e:
        logger.debug(f"Could not list the monitors: {e}")
        return []


def _reported():
    """What the KWin script or GNOME extension last told the tray, or None."""
    try:
        import dbus
        from . import dbus_service
        from .dbus_service import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH
        bus = dbus.SessionBus()
        if bus.get_name_owner(DBUS_NAME) == bus.get_unique_name():
            # With no tray running, the dictation service holds the name
            # itself; a call to its own name would wait on the GTK loop that
            # is making it, and time out.
            return dbus_service._focused_window_geometry or None
        proxy = bus.get_object(DBUS_NAME, DBUS_PATH, introspect=False)
        return str(proxy.GetFocusedWindowGeometry(dbus_interface=DBUS_INTERFACE,
                                                  timeout=_DBUS_TIMEOUT_S)) or None
    except Exception as e:
        logger.debug(f"Could not ask the tray where the active window is: {e}")
        return None


def _x11_active_rect():
    """The active window's frame in an X11 session, from the window manager.
    Not under Wayland: XWayland knows only the X11 apps."""
    if os.environ.get("XDG_SESSION_TYPE") != "x11":
        return None
    try:
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
        window = Gdk.Screen.get_default().get_active_window()
        if window is None:
            return None
        g = window.get_frame_extents()
        return Rect(g.x, g.y, g.width, g.height)
    except Exception as e:
        logger.debug(f"Could not ask X11 where the active window is: {e}")
        return None


def which_window():
    """Something that tells the focused window from others, None when nothing
    says: the compositor's own id on X11, Sway, i3, Hyprland and niri;
    on KDE and GNOME, where it is (active_window_report), so there a window
    moved or resized counts as another and two of one app maximized on one
    screen as the same (KWin and Mutter have ids, but the KWin script and
    the GNOME extension push only the class and the frame). COSMIC says
    nothing more than the class. Safe from any thread."""
    from . import compositor_focus
    window_id = compositor_focus.focused_window()[1]
    return window_id if window_id is not None else active_window_report()


def changed(before, after) -> bool:
    """Whether two looks at the focused window (tuples, a part None when it
    wasn't known) differ in a part both know. A D-Bus question that timed
    out, or TuxTalk's own window, isn't another window: stopping for it
    would stop a dictation for nothing."""
    return any(a is not None and b is not None and a != b for a, b in zip(before, after))


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
