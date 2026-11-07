"""The mouse-by-voice grid: cells, numbers, zoom levels and the tablet fit."""
import pytest

from tuxtalk.mouse_grid_model import Fit, Grid, Rect, close_enough

SCREEN = Rect(0, 0, 1920, 1080)


def test_one_monitor_has_nine_cells_numbered_from_the_top_left():
    cells = {c.number: c.rect for c in Grid([SCREEN]).cells}
    assert sorted(cells) == list(range(1, 10))
    assert cells[1] == Rect(0, 0, 640, 360)
    assert cells[5].center == (960, 540)
    assert cells[9].center == (1600, 900)


def test_the_second_monitor_goes_on_from_ten():
    grid = Grid([SCREEN, Rect(1920, 0, 1280, 1024)])
    cells = {c.number: c.rect for c in grid.cells}
    assert sorted(cells) == list(range(1, 19))
    assert cells[10].center == pytest.approx((1920 + 1280 / 6, 1024 / 6))


def test_a_number_zooms_into_that_cell():
    grid = Grid([SCREEN])
    assert grid.zoom(5) == "ok"
    assert grid.region == Rect(640, 360, 640, 360)
    cells = {c.number: c.rect for c in grid.cells}
    assert sorted(cells) == list(range(1, 10))
    assert cells[1].center == pytest.approx((640 + 640 / 6, 360 + 360 / 6))


def test_zooming_stops_where_a_cell_would_be_too_small():
    grid = Grid([Rect(0, 0, 1280, 800)])
    answers = [grid.zoom(5) for _ in range(10)]
    assert answers[0] == "ok"
    assert answers[-1] == "smallest"
    region = grid.region
    assert grid.zoom(5) == "smallest"
    assert grid.region == region
    assert min(region.width, region.height) / 9 < 8  # a cell of the region, split again


def test_a_number_with_no_cell():
    grid = Grid([SCREEN, Rect(1920, 0, 1280, 1024)])
    assert grid.zoom(0) == "no_cell"
    assert grid.zoom(19) == "no_cell"
    assert grid.target(19) == "no_cell"
    assert grid.zoom(1) == "ok"
    assert grid.zoom(10) == "no_cell"


def test_click_with_no_number_needs_one_on_two_monitors():
    assert Grid([SCREEN, Rect(1920, 0, 1280, 1024)]).target(None) == "say_a_number"
    assert Grid([SCREEN]).target(None) == (960, 540)


def test_click_with_no_number_after_a_zoom_is_the_regions_centre():
    grid = Grid([SCREEN, Rect(1920, 0, 1280, 1024)])
    grid.zoom(5)
    assert grid.target(None) == (960, 540)


def test_the_bounds_cover_all_monitors():
    grid = Grid([Rect(0, 0, 1920, 1080), Rect(1920, -100, 1280, 1024)])
    assert grid.bounds == Rect(0, -100, 3200, 1180)


def test_the_straight_fit_matches_the_spike():
    fit = Fit.straight(SCREEN)
    assert fit.fraction((192, 108)) == pytest.approx((0.1, 0.1))


def test_the_straight_fit_counts_from_the_boxs_corner():
    fit = Fit.straight(Rect(-1000, 0, 2000, 1000))
    assert fit.fraction((0, 500)) == pytest.approx((0.5, 0.5))


def test_a_measured_fit_corrects_a_scaled_or_shifted_mapping():
    # The desktop mapped the tablet's 0..1 onto x 100..1100 and y 0..1080.
    sent = ((0.2, 0.2), (0.8, 0.8))
    arrived = ((100 + 0.2 * 1000, 0.2 * 1080), (100 + 0.8 * 1000, 0.8 * 1080))
    fit = Fit.measured(sent, arrived)
    assert fit.fraction((600, 540)) == pytest.approx((0.5, 0.5))


def test_a_fit_from_one_spot_is_refused():
    sent = ((0.2, 0.2), (0.8, 0.8))
    assert Fit.measured(sent, ((300, 100), (300, 900))) is None
    assert Fit.measured(sent, ((100, 400), (900, 400))) is None


def test_close_enough_is_three_pixels():
    assert close_enough((100, 100), (103, 97))
    assert not close_enough((100, 100), (104, 100))
    assert not close_enough((100, 100), (100, 96))


def test_a_fit_from_one_sent_fraction_is_refused():
    assert Fit.measured(((0.5, 0.2), (0.5, 0.8)), ((300, 100), (900, 900))) is None


def _on_a_monitor(point, monitors):
    x, y = point
    return any(m.x <= x < m.x + m.width and m.y <= y < m.y + m.height for m in monitors)


@pytest.mark.parametrize("monitors", [
    [SCREEN],
    # A 4K screen with a 1080p one to its right, tops aligned: the box around
    # both has a hole below the small one.
    [Rect(0, 0, 3840, 2160), Rect(3840, 0, 1920, 1080)],
    [Rect(1920, 0, 1920, 1080), Rect(0, 600, 1920, 1080)],
    # Staggered so that the first screen's cell 1 and the second's cell 9
    # are at the same height.
    [Rect(0, 0, 1280, 1080), Rect(1280, -1620, 3840, 2160)],
])
def test_the_calibration_spots_are_on_a_monitor_and_apart(monitors):
    """Sent off every screen, the pointer stops at an edge, and a fit from
    there would refuse every click."""
    first, last = Grid(monitors).calibration_spots()
    assert _on_a_monitor(first, monitors) and _on_a_monitor(last, monitors)
    assert first[0] != last[0] and first[1] != last[1]


def test_the_spot_starts_at_the_parts_centre():
    grid = Grid([SCREEN])
    assert grid.spot is None
    grid.zoom(5)
    assert grid.spot == (960, 540)
    assert grid.target(None) == (960, 540)


def test_left_3_moves_the_spot_15_px():
    grid = Grid([SCREEN])
    grid.zoom(5)
    assert grid.nudge("left", 3) == "ok"
    assert grid.spot == (945, 540)
    grid.nudge("down", 2)
    grid.nudge("up", 1)
    grid.nudge("right", 1)
    assert grid.spot == (950, 545)


def test_a_nudge_stops_at_the_parts_edge():
    grid = Grid([SCREEN])
    grid.zoom(5)
    grid.nudge("right", 99)
    grid.nudge("up", 99)
    assert grid.spot == (1280, 360)


def test_a_new_number_puts_the_spot_back_in_the_middle():
    grid = Grid([SCREEN])
    grid.zoom(5)
    grid.nudge("left", 9)
    grid.zoom(1)
    assert grid.spot == grid.region.center


def test_a_nudge_at_the_top_level_asks_for_a_number():
    grid = Grid([SCREEN])
    assert grid.nudge("left", 1) == "nudge_needs_a_number"
    assert grid.spot is None


def test_click_with_no_number_clicks_the_nudged_spot():
    grid = Grid([SCREEN])
    grid.zoom(5)
    grid.nudge("down", 4)
    assert grid.target(None) == (960, 560)


def test_cells_are_small_at_1920x1080_after_one_number():
    grid = Grid([SCREEN])
    assert not grid.cells_are_small()
    grid.zoom(5)
    assert grid.cells_are_small()


def test_cells_are_small_on_3840x2160_only_after_two_numbers():
    grid = Grid([Rect(0, 0, 3840, 2160)])
    grid.zoom(5)
    assert not grid.cells_are_small()
    grid.zoom(5)
    assert grid.cells_are_small()
