"""The mouse-by-voice grid windows: one popup per monitor, numbered cells,
real clicks reported, and the pointer's arrival read in root coordinates.

Built on the test xvfb display, never on the user's desktop.
"""
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

import pytest  # noqa: E402

from tuxtalk import mouse_grid_window as mgw  # noqa: E402
from tuxtalk.mouse_grid_model import Grid  # noqa: E402


@pytest.fixture
def windows(monkeypatch):
    """The grid's windows, on a screen taken for a composited desktop: Xvfb
    has no compositor, where the grid refuses to show."""
    monkeypatch.setattr(Gdk.Screen, "is_composited", lambda screen: True)
    clicks = []
    shown = mgw.GridWindows(lambda: clicks.append(1))
    shown.clicks = clicks
    yield shown
    shown.hide()


def _labels(window):
    found = []

    def walk(widget):
        if isinstance(widget, Gtk.Label):
            found.append(widget)
        if isinstance(widget, Gtk.Container):
            widget.forall(walk)
    walk(window)
    return sorted(found, key=lambda label: int(label.get_text()))


def _flush(window):
    """Let GTK lay the window out: its labels get real allocations."""
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration()
        if all(label.get_allocation().x >= 0 for label in _labels(window)):
            return


def test_one_popup_per_monitor_covering_it(windows):
    screen = mgw.monitors()[0]
    assert windows.show(Grid([screen])) is None
    window = windows._windows[0]
    _flush(window)
    assert len(windows._windows) == 1
    assert window.get_window_type() == Gtk.WindowType.POPUP
    assert window.get_size() == (round(screen.width), round(screen.height))
    labels = _labels(window)
    assert [label.get_text() for label in labels] == [str(n) for n in range(1, 10)]
    # A badge is a small tag in the cell, not a bar filling its height.
    assert all(label.get_allocation().height < screen.height / 6 for label in labels)


def test_no_pointer_is_drawn_over_the_grid(windows):
    """The real pointer waits where the zoom needs it, often over a number;
    over the grid it isn't drawn, and the dot shows the spot instead."""
    windows.show(Grid([mgw.monitors()[0]]))
    window = windows._windows[0]
    _flush(window)
    cursor = window.get_window().get_cursor()
    assert cursor is not None and cursor.get_cursor_type() == Gdk.CursorType.BLANK_CURSOR


def test_after_a_zoom_the_numbers_are_inside_the_cell(windows):
    grid = Grid([mgw.monitors()[0]])
    windows.show(grid)
    grid.zoom(5)
    windows.redraw(grid)
    _flush(windows._windows[0])
    labels = _labels(windows._windows[0])
    assert len(labels) == 9
    region = grid.region
    for label in labels:
        box = label.get_allocation()
        assert region.x <= box.x and box.x + box.width <= region.x + region.width
        assert region.y <= box.y and box.y + box.height <= region.y + region.height


def test_the_windows_cover_the_monitors_the_cells_were_cut_from(windows):
    """One reading of the monitors for both: a screen plugged in meanwhile
    can't give windows that don't match the cells."""
    windows.show(Grid([mgw.Rect(0, 0, 320, 240)]))
    assert [w.area for w in windows._windows] == [mgw.Rect(0, 0, 320, 240)]


def test_without_an_x_display_nothing_shows(windows, monkeypatch):
    monkeypatch.setattr(mgw.ui_style, "is_x11", lambda display: False)
    assert windows.show(Grid([mgw.monitors()[0]])) == "grid_needs_x"
    assert windows._windows == []


def test_without_compositing_nothing_shows(windows, monkeypatch):
    """With no compositor the see-through tint is drawn solid: the grid
    would hide the whole screen."""
    monkeypatch.setattr(Gdk.Screen, "is_composited", lambda screen: False)
    assert windows.show(Grid([mgw.monitors()[0]])) == "grid_needs_compositing"
    assert windows._windows == []


def test_a_real_click_on_the_grid_calls_back(windows):
    windows.show(Grid([mgw.monitors()[0]]))
    event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    windows._windows[0].emit("button-press-event", event)
    assert windows.clicks == [1]


def test_motion_is_reported_in_root_coordinates(windows):
    windows.show(Grid([mgw.monitors()[0]]))
    before = time.monotonic()
    event = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
    event.x_root, event.y_root = 321.0, 123.0
    windows._windows[0].emit("motion-notify-event", event)
    assert windows.pointer_since(before) == (321.0, 123.0)
    assert windows.pointer_since(time.monotonic()) is None


def _boxes_with_class(window, css_class):
    found = []

    def walk(widget):
        if widget.get_style_context().has_class(css_class):
            found.append(widget)
        if isinstance(widget, Gtk.Container):
            widget.forall(walk)
    walk(window)
    return found


def _centre(widget):
    box = widget.get_allocation()
    return box.x + box.width / 2, box.y + box.height / 2


def test_top_level_still_draws_nine_bordered_cells(windows):
    windows.show(Grid([mgw.monitors()[0]]))
    window = windows._windows[0]
    _flush(window)
    assert len(_boxes_with_class(window, "mouse-grid-cell")) == 9
    assert _boxes_with_class(window, "mouse-grid-corner") == []
    assert _boxes_with_class(window, "mouse-grid-dot") == []


def test_zoomed_level_has_thin_cell_lines_corner_numbers_and_a_dot(windows):
    """A zoomed level is still a grid, nine thin outlines, with its numbers
    in the corners rather than over the middle of each cell."""
    grid = Grid([mgw.monitors()[0]])
    windows.show(grid)
    grid.zoom(5)
    windows.redraw(grid)
    window = windows._windows[0]
    _flush(window)
    assert _boxes_with_class(window, "mouse-grid-cell") == []
    thin = _boxes_with_class(window, "mouse-grid-cell-thin")
    outlines = sorted((a.x, a.y, a.width, a.height) for a in (b.get_allocation() for b in thin))
    edges = [(round(c.rect.x), round(c.rect.y), round(c.rect.x + c.rect.width),
              round(c.rect.y + c.rect.height)) for c in grid.cells]
    assert outlines == sorted((left, top, right - left, bottom - top)
                              for left, top, right, bottom in edges)
    style = thin[0].get_style_context()
    right = style.get_border(style.get_state()).right
    assert right == 1                                   # a line is drawn, one pixel wide
    corners = _boxes_with_class(window, "mouse-grid-corner")
    assert len(corners) == 9
    for corner, cell in zip(sorted(corners, key=lambda c: int(c.get_text())), grid.cells, strict=True):
        box = corner.get_allocation()
        assert (box.x, box.y) == (round(cell.rect.x) + 3, round(cell.rect.y) + 3)
    (dot,) = _boxes_with_class(window, "mouse-grid-dot")
    cx, cy = _centre(dot)
    assert abs(cx - grid.spot[0]) <= 1 and abs(cy - grid.spot[1]) <= 1
    assert window._fixed.get_children()[-1] is dot      # drawn over the lines


def test_the_dot_follows_a_nudge(windows):
    grid = Grid([mgw.monitors()[0]])
    windows.show(grid)
    grid.zoom(5)
    grid.nudge("right", 2)
    windows.redraw(grid)
    window = windows._windows[0]
    _flush(window)
    (dot,) = _boxes_with_class(window, "mouse-grid-dot")
    cx, cy = _centre(dot)
    assert abs(cx - grid.spot[0]) <= 1 and abs(cy - grid.spot[1]) <= 1


def test_only_the_monitor_holding_the_spot_draws_the_dot(windows):
    grid = Grid([mgw.Rect(0, 0, 400, 300), mgw.Rect(400, 0, 400, 300)])
    windows.show(grid)
    grid.zoom(5)
    windows.redraw(grid)
    counts = [len(_boxes_with_class(w, "mouse-grid-dot")) for w in windows._windows]
    assert sorted(counts) == [0, 1]


def test_the_deepest_corner_numbers_stay_inside_their_cells(windows):
    """With no desktop zoom the deepest cells are about 13 px high: each
    number fits its cell, so no row's number covers the next row's."""
    grid = Grid([mgw.Rect(0, 0, 1920, 1080)])
    windows.show(grid)
    for number in (5, 5, 5):
        assert grid.zoom(number) == "ok"
    assert grid.zoom(5) == "smallest"
    windows.redraw(grid)
    window = windows._windows[0]
    _flush(window)
    corners = sorted(_boxes_with_class(window, "mouse-grid-corner"), key=lambda c: int(c.get_text()))
    assert len(corners) == 9
    for corner, cell in zip(corners, grid.cells, strict=True):
        box = corner.get_allocation()
        assert box.y + box.height <= round(cell.rect.y + cell.rect.height)
        assert box.x + box.width <= round(cell.rect.x + cell.rect.width)


def test_a_roomy_cell_keeps_the_16_px_number_and_a_tiny_one_has_none():
    assert mgw.corner_size(mgw.Cell(1, mgw.Rect(0, 0, 120, 120))) == (16, 3)
    assert mgw.corner_size(mgw.Cell(1, mgw.Rect(0, 0, 14, 8.9))) is None
