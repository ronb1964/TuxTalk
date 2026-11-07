"""The see-through numbered grid of "mouse by voice", drawn over each monitor.

One borderless popup per monitor, with an RGBA visual so the desktop shows
through; without a compositor that draws it see-through, no grid shows.
Popups are override-redirect windows (XWayland on a Wayland desktop, as with
the recording indicator), so they go exactly where they're put and never take
the keyboard from the app being controlled.

Two looks. The top level draws every cell with a border and a centred number.
A zoomed grid shows one region, outlined and lightly tinted, with the rest
dimmed more; its cells have thin lines and a small number in their top-left
corner, so the numbers stay off the middle of a cell, and one dot marks
where a click lands. The monitor that holds the region draws the dot; the
others are dimmed whole.

Every method here runs on the GTK thread. The dictation worker reaches it
through gtk_loop.call and polls `pointer_since` to see where the virtual
pointer arrived, which needs the GTK thread free to deliver motion events.
"""
import time
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from . import ui_style  # noqa: E402
from .logger import setup_logger  # noqa: E402
from .mouse_grid_model import Cell, Grid, Rect  # noqa: E402

logger = setup_logger(__name__)

_CSS = b"""
window.mouse-grid { background-color: rgba(0, 0, 0, 0.18); }
window.mouse-grid-zoomed { background-color: rgba(0, 0, 0, 0); }
.mouse-grid-cell { border: 2px solid rgba(255, 210, 0, 0.9); }
/* Right and bottom only: neighbours share one 1 px line, and the region's
   own outline is the top and left. */
.mouse-grid-cell-thin { border-style: solid; border-color: rgba(255, 210, 0, 0.55);
                        border-width: 0 1px 1px 0; }
.mouse-grid-region { border: 2px solid rgba(255, 210, 0, 1);
                     background-color: rgba(0, 0, 0, 0.18); }
.mouse-grid-dim { background-color: rgba(0, 0, 0, 0.45); }
.mouse-grid-number { color: #ffd200; font-weight: bold;
                     background-color: rgba(0, 0, 0, 0.55);
                     border-radius: 8px; padding: 2px 14px; }
.mouse-grid-number-small { border-radius: 4px; padding: 0 4px; }
.mouse-grid-corner { color: #ffd200; font-weight: bold;
                     background-color: rgba(0, 0, 0, 0.55);
                     border-radius: 4px; padding: 0 3px; }
.mouse-grid-dot { background-color: #ffd200; border-radius: 50%;
                  border: 2px solid rgba(0, 0, 0, 0.8); }
"""

# The badge is 40 px in a roomy cell and shrinks, down to a readable 11 px,
# when the cell is small (a zoomed cell can be only a few dozen pixels).
_BADGE_PX, _BADGE_MIN_PX, _CELL_PER_BADGE = 40, 11, 4
_SMALL_BADGE_PX = 24
# Deeper levels: a 16 px number 3 px in from the cell's corner, and a 10 px
# dot (its ring included). Without a desktop zoom the deepest cells are only
# 9 to 14 px, so there the number shrinks and moves in to 1 px, down to 8 px,
# and below that isn't shown. A label is about 1.45 times its font size high
# (13 px for a 9 px font, measured).
_CORNER_PX, _CORNER_MIN_PX, _CORNER_INSET_PX, _DOT_PX = 16, 8, 3, 10
_LINE_PER_FONT_PX = 1.45
_css_loaded = False


def monitors() -> list[Rect]:
    """Each monitor's rectangle in X11 desktop pixels, in GDK's order; none
    with no display at all (GridWindows.show then refuses)."""
    display = Gdk.Display.get_default()
    if display is None:
        return []
    rects = []
    for i in range(display.get_n_monitors()):
        g = display.get_monitor(i).get_geometry()
        rects.append(Rect(g.x, g.y, g.width, g.height))
    return rects


def _load_css_once(screen):
    global _css_loaded
    if _css_loaded:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 100)
    _css_loaded = True


def _badge(cell: Cell) -> Gtk.Label:
    """The number label of a cell, sized to fit it."""
    px = max(_BADGE_MIN_PX,
             min(_BADGE_PX, min(cell.rect.width, cell.rect.height) / _CELL_PER_BADGE))
    label = Gtk.Label(label=str(cell.number))
    attrs = Pango.AttrList()
    attrs.insert(Pango.attr_size_new_absolute(int(px * Pango.SCALE)))
    label.set_attributes(attrs)
    label.set_halign(Gtk.Align.CENTER)
    label.set_valign(Gtk.Align.CENTER)
    context = label.get_style_context()
    context.add_class("mouse-grid-number")
    if px < _SMALL_BADGE_PX:
        context.add_class("mouse-grid-number-small")
    return label


def corner_size(cell: Cell) -> tuple[int, int] | None:
    """The corner number's font size and inset in pixels for *cell*, so it
    stays inside it; None when even the smallest readable one wouldn't."""
    room = min(cell.rect.width, cell.rect.height)
    roomy = room >= _CORNER_INSET_PX + _CORNER_PX * _LINE_PER_FONT_PX
    inset = _CORNER_INSET_PX if roomy else 1
    px = min(_CORNER_PX, int((room - inset) / _LINE_PER_FONT_PX))
    return (px, inset) if px >= _CORNER_MIN_PX else None


def _corner_label(cell: Cell, px: int) -> Gtk.Label:
    """The small number shown in a zoomed cell's top-left corner."""
    label = Gtk.Label(label=str(cell.number))
    attrs = Pango.AttrList()
    attrs.insert(Pango.attr_size_new_absolute(px * Pango.SCALE))
    label.set_attributes(attrs)
    label.get_style_context().add_class("mouse-grid-corner")
    return label


def _hide_the_pointer(window: Gtk.Window) -> None:
    """No pointer drawn over the grid: the real one waits wherever the zoom
    needs it and would cover a number; the dot shows where a click lands.
    It comes back as the grid's windows go (GridWindows.hide), before any
    click."""
    gdk_window = window.get_window()
    gdk_window.set_cursor(Gdk.Cursor.new_for_display(gdk_window.get_display(),
                                                     Gdk.CursorType.BLANK_CURSOR))


class _MonitorWindow(Gtk.Window):
    """The popup over one monitor, holding that monitor's boxes and badges."""

    def __init__(self, area: Rect, on_motion, on_press):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.area = area
        # show() checked the screen has one.
        self.set_visual(self.get_screen().get_rgba_visual())
        self.move(round(area.x), round(area.y))
        self.set_default_size(round(area.width), round(area.height))
        self.resize(round(area.width), round(area.height))
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("motion-notify-event", on_motion)
        self.connect("button-press-event", on_press)
        self.connect("realize", _hide_the_pointer)
        self._fixed = Gtk.Fixed()
        self.add(self._fixed)

    def _put_box(self, rect: Rect, css_class: str) -> Gtk.Box:
        """An empty styled box covering *rect*. Edges are rounded, not sizes,
        so neighbouring boxes meet with no one-pixel gap."""
        left, top = round(rect.x - self.area.x), round(rect.y - self.area.y)
        right = round(rect.x + rect.width - self.area.x)
        bottom = round(rect.y + rect.height - self.area.y)
        box = Gtk.Box()
        box.get_style_context().add_class(css_class)
        box.set_size_request(max(1, right - left), max(1, bottom - top))
        self._fixed.put(box, left, top)
        return box

    def _put_cells(self, cells: list[Cell]):
        for cell in cells:
            self._put_box(cell.rect, "mouse-grid-cell").set_center_widget(_badge(cell))

    def _put_zoomed_cells(self, cells: list[Cell]):
        """Each zoomed cell: a thin outline, and its number in the corner."""
        for cell in cells:
            self._put_box(cell.rect, "mouse-grid-cell-thin")
            size = corner_size(cell)
            if size is None:
                continue
            px, inset = size
            self._fixed.put(_corner_label(cell, px),
                            round(cell.rect.x - self.area.x) + inset,
                            round(cell.rect.y - self.area.y) + inset)

    def _put_dot(self, spot: tuple[float, float]):
        """The click dot, centred on *spot*."""
        dot = Gtk.Box()
        dot.get_style_context().add_class("mouse-grid-dot")
        dot.set_size_request(_DOT_PX, _DOT_PX)
        self._fixed.put(dot, round(spot[0] - self.area.x - _DOT_PX / 2),
                        round(spot[1] - self.area.y - _DOT_PX / 2))

    def _put_dim_around(self, region: Rect):
        """Dim the monitor except for *region*: four strips around it."""
        a = self.area
        strips = [
            Rect(a.x, a.y, a.width, region.y - a.y),
            Rect(a.x, region.y + region.height, a.width,
                 a.y + a.height - region.y - region.height),
            Rect(a.x, region.y, region.x - a.x, region.height),
            Rect(region.x + region.width, region.y,
                 a.x + a.width - region.x - region.width, region.height),
        ]
        for strip in strips:
            if strip.width > 0 and strip.height > 0:
                self._put_box(strip, "mouse-grid-dim")

    def draw_grid(self, grid: Grid):
        """Replace everything shown with the grid's current level."""
        for child in self._fixed.get_children():
            child.destroy()
        context = self.get_style_context()
        region = grid.region
        context.remove_class("mouse-grid")
        context.remove_class("mouse-grid-zoomed")
        if region is None:
            context.add_class("mouse-grid")
            self._put_cells([c for c in grid.cells if self._holds(c.rect)])
        else:
            context.add_class("mouse-grid-zoomed")
            if self._holds(region):
                self._put_dim_around(region)
                self._put_box(region, "mouse-grid-region")
                self._put_zoomed_cells(grid.cells)
                if grid.spot and self._holds(Rect(*grid.spot, 0, 0)):
                    self._put_dot(grid.spot)
            else:
                self._put_box(self.area, "mouse-grid-dim")
        self._fixed.show_all()

    def _holds(self, rect: Rect) -> bool:
        cx, cy = rect.center
        a = self.area
        return a.x <= cx < a.x + a.width and a.y <= cy < a.y + a.height


class GridWindows:
    """The grid on screen: made by show(), redrawn by redraw(), gone by hide()."""

    def __init__(self, on_real_click: Callable[[], None]):
        self._on_real_click = on_real_click
        self._windows: list[_MonitorWindow] = []
        self._pointer: tuple[float, tuple[float, float]] | None = None

    def pointer_since(self, after: float) -> tuple[float, float] | None:
        """The last motion's root position if it came after monotonic time
        *after*, else None. Never waits: the caller polls, so this thread
        stays free to receive the motion events."""
        if self._pointer and self._pointer[0] > after:
            return self._pointer[1]
        return None

    def show(self, grid: Grid) -> str | None:
        """Put the grid on every monitor. None when it shows, else the
        command_notices kind saying why not: "grid_needs_x" with no X display
        to place popups on (a plain Wayland session), "grid_needs_compositing"
        when nothing draws windows see-through (X without a compositor, where
        the tint would cover the whole screen)."""
        display = Gdk.Display.get_default()
        if display is None or not ui_style.is_x11(display):
            logger.info("No grid shown: this desktop has no X display to put it on")
            return "grid_needs_x"
        screen = display.get_default_screen()
        if not screen.is_composited() or screen.get_rgba_visual() is None:
            logger.info("No grid shown: this desktop doesn't draw see-through windows")
            return "grid_needs_compositing"
        self.hide()
        _load_css_once(screen)
        self._pointer = None
        self._windows = [_MonitorWindow(area, self._on_motion, self._on_press)
                         for area in grid.monitors]
        self.redraw(grid)
        for window in self._windows:
            window.show()
        return None

    def redraw(self, grid: Grid):
        for window in self._windows:
            window.draw_grid(grid)

    def hide(self):
        """Take the grid's windows away, the pointer shown again first: a
        compositor that picks the window under the pointer only on its next
        motion (COSMIC) would otherwise keep the blank one after a close by
        voice, Esc or the timeout, with the mouse untouched."""
        for window in self._windows:
            if window.get_window() is not None:
                window.get_window().set_cursor(None)
        if self._windows:
            Gdk.Display.get_default().flush()
        for window in self._windows:
            window.destroy()
        self._windows = []

    def _on_motion(self, _window, event) -> bool:
        self._pointer = (time.monotonic(), (event.x_root, event.y_root))
        return False

    def _on_press(self, _window, _event) -> bool:
        self._on_real_click()
        return True
