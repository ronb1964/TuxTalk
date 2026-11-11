"""The live text window (TuxTalk Labs, live_text.py): what's been said so far,
in a dark rounded box under the recording indicator, growing with the
dictation.

It takes no focus and clicks go through it, like the indicator and the
floating messages (ui_style.click_through): an override-redirect popup,
which goes exactly where it's put on X11 and XWayland, where the dictation
service runs GTK. Without an X display GTK 3 would make it an ordinary
window that takes the keyboard, so there none is made (make). It grows up
to a third of the screen's height; past that the newest lines show and the
first ones fade out at the top. Everything here runs on GTK's thread; show
and hide may be called from any thread.
"""
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
import cairo
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

from . import recording_state as rs
from .logger import setup_logger
from .ui_style import LABEL_FILL, LABEL_TEXT, NEWS_EDGE, click_through, is_x11
from .window_geometry import main_monitor_rect, monitor_rect_at

logger = setup_logger(__name__)

WIDTH = 560                 # pixels, or the screen's width less a margin
_PAD = 14
_GAP = 8                    # between the indicator and the window
_FADE = 28                  # the fade over the first lines, when they don't fit
_MARGIN = 20                # kept clear at the screen's edges


def make(get_indicator):
    """The window, or None where it would take the keyboard (no X display).
    On GTK's thread. *get_indicator* returns the recording indicator (a
    Gtk.Window) or None, for where to put it."""
    if not is_x11(Gdk.Display.get_default()):
        logger.info("Live text can't be shown: with no X display its window would take "
                    "the keyboard")
        return None
    return LiveTextWindow(get_indicator)


def place(width, height, anchor, monitor):
    """Top-left corner for a *width* x *height* window under *anchor* (x, y,
    w, h: the indicator, or a point), or above it when there's no room
    below, kept inside *monitor* (a window_geometry.Rect)."""
    ax, ay, aw, ah = anchor
    x = round(ax + aw / 2 - width / 2)
    y = ay + ah + _GAP
    if y + height > monitor.y + monitor.h - _MARGIN:
        y = ay - _GAP - height
    x = max(monitor.x + _MARGIN, min(x, monitor.x + monitor.w - _MARGIN - width))
    y = max(monitor.y + _MARGIN, min(y, monitor.y + monitor.h - _MARGIN - height))
    return x, y


class LiveTextWindow:
    """Shows and hides the window; made by make()."""

    def __init__(self, get_indicator):
        self._get_indicator = get_indicator
        self._win = None
        self._layout = None
        self._size = (0, 0)
        self._shift = 0             # how far the text is drawn up, past the top

    def show(self, words, rec, phrase):
        """Show *words*, if they're still from the phrase being said when GTK
        gets to it: a read finishing after the stop, or after Type at pause
        typed the phrase (*phrase*, rec.chunks_submitted), mustn't bring
        them back."""
        GLib.idle_add(self._show_now, words, rec, phrase)

    def hide(self):
        """Close it, when shown."""
        GLib.idle_add(self._hide_now)

    def _show_now(self, words, rec, phrase):
        """On GTK's thread."""
        if (rs.state.current is not rec or not rs.state.is_recording
                or rec.chunks_submitted != phrase):
            return False
        try:
            if self._win is None:
                self._build()
            self._layout.set_text(words, -1)
            # For a screen reader to read out on request. The box never has
            # the focus, so screen readers aren't told to announce each change.
            self._win.get_accessible().set_description(words)
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
        """The popup window, not shown yet."""
        win = Gtk.Window(type=Gtk.WindowType.POPUP)
        click_through(win)
        win.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        win.get_accessible().set_name("Live text")
        win.connect("draw", self._draw)
        self._layout = win.create_pango_layout("")
        self._layout.set_font_description(Pango.FontDescription("Sans 12"))
        self._layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        self._win = win

    def _anchor(self):
        """(x, y, w, h) to sit under: the indicator when it shows, else a
        point low on the main monitor."""
        indicator = self._get_indicator()
        if indicator is not None and indicator.get_visible():
            x, y = indicator.get_position()
            w, h = indicator.get_size()
            return x, y, w, h
        main = main_monitor_rect()
        return main.x + main.w // 2, main.y + main.h * 2 // 3, 0, 0

    def _fit(self):
        """Size and place the window for the text it holds now."""
        anchor = self._anchor()
        monitor = monitor_rect_at(anchor[0], anchor[1])
        width = min(WIDTH, monitor.w - 2 * _MARGIN)
        self._layout.set_width((width - 2 * _PAD) * Pango.SCALE)
        _w, text_h = self._layout.get_pixel_size()
        tallest = monitor.h // 3
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
        cr.set_source_rgba(*LABEL_FILL)
        cr.fill_preserve()
        cr.set_source_rgba(*NEWS_EDGE)
        cr.set_line_width(1.5)
        cr.stroke()
        cr.save()
        _rounded(cr, width, height, 12)
        cr.clip()
        cr.move_to(_PAD, _PAD - self._shift)
        cr.set_source_rgba(*LABEL_TEXT)
        PangoCairo.show_layout(cr, self._layout)
        if self._shift:
            fill = LABEL_FILL[:3]
            fade = cairo.LinearGradient(0, 0, 0, _PAD + _FADE)
            fade.add_color_stop_rgba(0, *fill, 1.0)
            fade.add_color_stop_rgba(1, *fill, 0.0)
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
