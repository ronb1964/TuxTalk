"""A short message floating where the user is looking: over the middle of
the active window (window_geometry), for a few seconds, then gone.

The corner notification is easy to miss on a big screen. This is a
pill-shaped label, not a window: it takes no focus, clicks go
through it, and there is nothing to close. The caller still sends the usual
notification as a backup, which stays in the notification history.

Drawn in the dictation service, which runs GTK on X11 / XWayland (for the
recording indicator), as an override-redirect popup, so it goes exactly
where it is put on every desktop.
"""
from .logger import setup_logger

logger = setup_logger(__name__)

# On screen long enough to read: a few seconds plus a little per word, so a
# short label stays 4 s and a longer one up to 8. Keep the text short all the
# same: it's glanced at.
MIN_SECONDS, MAX_SECONDS, SECONDS_PER_WORD = 4.0, 8.0, 0.4
_PAD_X, _PAD_Y = 18, 10
_current = None          # the label on screen, replaced by the next one
_current_tag = None      # what the caller called it, so hide() takes only its own
_current_relabel = None  # changes the text of the label on screen (relabel)


# low=True puts the label this far above the window's bottom edge: over the
# lower part of a chat window, just above where its message box usually is,
# not on top of the text being sent. A short window gets a quarter of its
# height instead.
LOW_ABOVE_BOTTOM = 200


def low_center(rect):
    """Where a low label is centred in the window *rect* (window_geometry.Rect)."""
    above = min(LOW_ABOVE_BOTTOM, rect.h // 4)
    return (round(rect.x + rect.w / 2), rect.y + rect.h - above)


def reading_seconds(text):
    """How long *text* stays on screen."""
    seconds = 2.0 + SECONDS_PER_WORD * len(text.split())
    return round(min(MAX_SECONDS, max(MIN_SECONDS, seconds)), 1)


def place(width, height, center, monitor):
    """Top-left corner for a *width* x *height* label centred on *center*,
    kept inside *monitor* (a window_geometry.Rect)."""
    x = round(center[0] - width / 2)
    y = round(center[1] - height / 2)
    x = max(monitor.x, min(x, monitor.x + monitor.w - width))
    y = max(monitor.y, min(y, monitor.y + monitor.h - height))
    return x, y


def show(text, center=None):
    """Show *text* centred on *center* (None: the middle of the main screen).
    Safe from any thread; never raises."""
    from gi.repository import GLib
    GLib.idle_add(_show_now, text, center)


def show_over_active_window(text, seconds=None, warning=True, tag=None, low=False):
    """Show *text* over the middle of the active window, or its lower part
    with *low* (low_center), for *seconds* (None: reading_seconds). *warning*
    False drops the warning sign and the amber edge, for news rather than a
    problem. Safe from any thread: the lookups
    that need no GDK run here, the GDK ones on the GTK thread, since GDK may
    only be used from the thread that runs Gtk.main()."""
    try:
        from gi.repository import GLib
        from . import window_geometry
        GLib.idle_add(_show_over_window_now, text, window_geometry.active_window_report(),
                      seconds, warning, tag, low)
    except Exception as e:      # a notice mustn't break the dictation that gives it
        logger.info(f"Could not show the message over the window: {e}")


def hide(tag):
    """Take down the label shown with *tag*, if it is still the one on screen."""
    from gi.repository import GLib
    GLib.idle_add(_hide_now, tag)


def _hide_now(tag):
    global _current, _current_tag, _current_relabel
    if _current is not None and _current_tag == tag:
        _current.destroy()
        _current = _current_tag = _current_relabel = None
    return False


def relabel(tag, text):
    """Change the text of the label shown with *tag*, in place: a countdown
    ticking on one label instead of a new label each second."""
    from gi.repository import GLib
    GLib.idle_add(_relabel_now, tag, text)


def _relabel_now(tag, text):
    if _current_relabel is not None and _current_tag == tag:
        _current_relabel(text)
    return False


def _show_over_window_now(text, report, seconds=None, warning=True, tag=None, low=False):
    """GTK-thread half of show_over_active_window."""
    try:
        from . import window_geometry
        if low:
            rect = window_geometry.active_window_rect(report)
            center = low_center(rect) if rect else None
        else:
            center = window_geometry.active_window_center(report)
    except Exception as e:      # shown in the middle of the screen instead
        logger.info(f"Could not find the active window: {e}")
        center = None
    return _show_now(text, center, seconds, warning, tag)


def _show_now(text, center, seconds=None, warning=True, tag=None):
    """Build the label and show it for *seconds* (None: reading_seconds());
    GTK thread only."""
    global _current, _current_tag, _current_relabel
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Gdk, GLib, Gtk, PangoCairo
        import cairo
        from .ui_style import is_x11
        from .window_geometry import Rect

        display = Gdk.Display.get_default()
        # A label floats over the active window without taking the keyboard
        # only on X11 (XWayland). The service falls back to native Wayland
        # when there's no X display, and there GTK 3 makes the label an
        # ordinary window that takes the focus, so everything typed while it
        # shows is lost ("Press Enter when you're ready" included; VM test,
        # 2026-10-20).
        if not is_x11(display):
            logger.info(f"Not showing {text!r} over the window: no X display, so it would "
                        "take the keyboard")
            return False
        if center is None:
            mon = display.get_primary_monitor() or display.get_monitor(0)
            g = mon.get_geometry()
            center = (g.x + g.width // 2, g.y + g.height // 2)
        g = (display.get_monitor_at_point(*center) or display.get_monitor(0)).get_geometry()
        monitor = Rect(g.x, g.y, g.width, g.height)

        win = Gtk.Window(type=Gtk.WindowType.POPUP)
        win.set_app_paintable(True)
        visual = win.get_screen().get_rgba_visual()
        if visual is not None:
            win.set_visual(visual)
        win.set_accept_focus(False)
        win.set_keep_above(True)

        layout = win.create_pango_layout("")
        sign = "⚠  " if warning else ""

        def set_text(words):
            layout.set_markup(f'<span size="large">{sign}{GLib.markup_escape_text(words)}</span>', -1)
        set_text(text)
        text_w, text_h = layout.get_pixel_size()
        width, height = text_w + 2 * _PAD_X, text_h + 2 * _PAD_Y
        win.set_default_size(width, height)
        win.move(*place(width, height, center, monitor))

        def draw(_widget, cr):
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)
            r = height / 2
            cr.new_sub_path()
            cr.arc(width - r, r, r, -1.5708, 1.5708)
            cr.arc(r, r, r, 1.5708, 4.7124)
            cr.close_path()
            cr.set_source_rgba(0.12, 0.12, 0.13, 0.94)
            cr.fill_preserve()
            if warning:
                cr.set_source_rgba(0.94, 0.62, 0.15, 1.0)     # amber edge: a warning
            else:
                cr.set_source_rgba(0.45, 0.62, 0.90, 1.0)     # blue edge: news
            cr.set_line_width(1.5)
            cr.stroke()
            cr.move_to(_PAD_X, _PAD_Y)
            cr.set_source_rgba(0.98, 0.93, 0.85, 1.0)
            PangoCairo.show_layout(cr, layout)
            return True

        win.connect("draw", draw)
        # Clicks go through to whatever is underneath.
        win.connect("realize", lambda w: w.input_shape_combine_region(cairo.Region()))
        def relabel_this(words):
            # Same size label: a countdown's text keeps its length.
            set_text(words)
            win.queue_draw()

        if _current is not None:
            _current.destroy()
        _current, _current_tag, _current_relabel = win, tag, relabel_this
        win.show_all()

        def done():
            global _current, _current_tag, _current_relabel
            if _current is win:
                _current = _current_tag = _current_relabel = None
            win.destroy()
            return False
        shown_for = reading_seconds(text) if seconds is None else seconds
        GLib.timeout_add(int(shown_for * 1000), done)
    except Exception as e:
        logger.warning(f"Could not show the message on screen: {e}")
    return False
