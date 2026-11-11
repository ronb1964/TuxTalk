"""The live text window (TuxTalk Labs, live_text.py): what's been said so far,
in a dark rounded box under the recording indicator, growing with the
dictation.

It takes no focus and clicks go through it, like the indicator and the
floating messages (screen_message): an override-redirect popup, which goes
exactly where it's put on X11 and XWayland, where the dictation service runs
GTK. Without an X display GTK 3 would make it an ordinary window that takes
the keyboard, so there it isn't shown. It grows up to a third of the screen's
height; past that the newest lines show and the first ones fade out at the
top. Everything here runs on GTK's thread; show and hide may be called from
any thread.
"""
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
import cairo
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

from . import recording_state as rs
from .logger import setup_logger

logger = setup_logger(__name__)

WIDTH = 560                 # pixels, or the screen's width less a margin
_PAD = 14
_GAP = 8                    # between the indicator and the window
_FADE = 28                  # the fade over the first lines, when they don't fit
_MARGIN = 20                # kept clear at the screen's edges


def _monitor_at(display, x, y):
    """The geometry of the monitor at (x, y), or the first one's."""
    return (display.get_monitor_at_point(x, y) or display.get_monitor(0)).get_geometry()


def place(width, height, anchor, monitor):
    """Top-left corner for a *width* x *height* window under *anchor* (x, y,
    w, h: the indicator, or a point), or above it when there's no room
    below, kept inside *monitor*."""
    ax, ay, aw, ah = anchor
    x = round(ax + aw / 2 - width / 2)
    y = ay + ah + _GAP
    if y + height > monitor.y + monitor.height - _MARGIN:
        y = ay - _GAP - height
    x = max(monitor.x + _MARGIN, min(x, monitor.x + monitor.width - _MARGIN - width))
    y = max(monitor.y + _MARGIN, min(y, monitor.y + monitor.height - _MARGIN - height))
    return x, y


class LiveTextWindow:
    """Shows and hides the window. *indicator* returns the recording
    indicator (a Gtk.Window) or None, for where to put it."""

    def __init__(self, indicator):
        self._indicator = indicator
        self._win = None
        self._layout = None
        self._size = (0, 0)
        self._shift = 0             # how far the text is drawn up, past the top

    def show(self, words, rec):
        """Show *words*, if *rec* is still the recording running when GTK
        gets to it (a read finishing after the stop mustn't reopen it)."""
        GLib.idle_add(self._show_now, words, rec)

    def hide(self):
        """Close it, when shown."""
        GLib.idle_add(self._hide_now)

    def _show_now(self, words, rec):
        """On GTK's thread."""
        if rs.state.current is not rec or not rs.state.is_recording:
            return False
        try:
            if self._win is None and not self._build():
                return False
            self._layout.set_text(words, -1)
            self._fit()
            self._win.queue_draw()
            self._win.show_all()
        except Exception as e:      # live text is a help: never a reason to stop dictating
            logger.warning(f"Couldn't show the live text: {e}")
        return False

    def _hide_now(self):
        """On GTK's thread."""
        if self._win is not None:
            self._win.destroy()
            self._win = self._layout = None
        return False

    def _build(self):
        """The popup window; False where it would take the keyboard."""
        from .ui_style import is_x11
        if not is_x11(Gdk.Display.get_default()):
            logger.info("Live text not shown: no X display, so its window would take the "
                        "keyboard")
            return False
        win = Gtk.Window(type=Gtk.WindowType.POPUP)
        win.set_app_paintable(True)
        visual = win.get_screen().get_rgba_visual()
        if visual is not None:
            win.set_visual(visual)
        win.set_accept_focus(False)
        win.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        win.connect("draw", self._draw)
        # Clicks go through to whatever is underneath.
        win.connect("realize", lambda w: w.input_shape_combine_region(cairo.Region()))
        self._layout = win.create_pango_layout("")
        self._layout.set_font_description(Pango.FontDescription("Sans 12"))
        self._layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        self._win = win
        return True

    def _anchor(self, display):
        """(x, y, w, h) to sit under: the indicator when it shows, else a
        point low on the main monitor."""
        indicator = self._indicator()
        if indicator is not None and indicator.get_visible():
            x, y = indicator.get_position()
            w, h = indicator.get_size()
            return x, y, w, h
        mon = display.get_primary_monitor() or display.get_monitor(0)
        g = mon.get_geometry()
        return g.x + g.width // 2, g.y + g.height * 2 // 3, 0, 0

    def _fit(self):
        """Size and place the window for the text it holds now."""
        display = Gdk.Display.get_default()
        anchor = self._anchor(display)
        monitor = _monitor_at(display, anchor[0], anchor[1])
        width = min(WIDTH, monitor.width - 2 * _MARGIN)
        self._layout.set_width((width - 2 * _PAD) * Pango.SCALE)
        _w, text_h = self._layout.get_pixel_size()
        tallest = monitor.height // 3
        height = min(text_h + 2 * _PAD, tallest)
        self._shift = max(0, text_h + 2 * _PAD - tallest)
        self._size = (width, height)
        self._win.resize(width, height)
        self._win.move(*place(width, height, anchor, monitor))

    def _draw(self, _widget, cr):
        """A dark rounded box with a blue edge and the text, its first lines
        fading out at the top when they don't all fit."""
        width, height = self._size
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        _rounded(cr, width, height, 12)
        cr.set_source_rgba(0.12, 0.12, 0.13, 0.94)
        cr.fill_preserve()
        cr.set_source_rgba(0.45, 0.62, 0.90, 1.0)
        cr.set_line_width(1.5)
        cr.stroke()
        cr.save()
        _rounded(cr, width, height, 12)
        cr.clip()
        cr.move_to(_PAD, _PAD - self._shift)
        cr.set_source_rgba(0.98, 0.95, 0.90, 1.0)
        PangoCairo.show_layout(cr, self._layout)
        if self._shift:
            fade = cairo.LinearGradient(0, 0, 0, _PAD + _FADE)
            fade.add_color_stop_rgba(0, 0.12, 0.12, 0.13, 1.0)
            fade.add_color_stop_rgba(1, 0.12, 0.12, 0.13, 0.0)
            cr.rectangle(0, 0, width, _PAD + _FADE)
            cr.set_source(fade)
            cr.fill()
        cr.restore()
        return True


def _rounded(cr, width, height, radius):
    """A rounded rectangle path the size of the window."""
    cr.new_sub_path()
    cr.arc(width - radius, radius, radius, -1.5708, 0)
    cr.arc(width - radius, height - radius, radius, 0, 1.5708)
    cr.arc(radius, height - radius, radius, 1.5708, 3.1416)
    cr.arc(radius, radius, radius, 3.1416, 4.7124)
    cr.close_path()
