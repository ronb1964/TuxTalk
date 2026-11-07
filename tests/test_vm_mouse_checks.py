"""The VM checks for mouse by voice judge a click by the press the test
window logged and by where TuxTalk's log says the grid aimed. A misread line
would pass a click that landed elsewhere, so each reading is pinned here,
and so is each played phrase being the command the check expects.
"""
import pytest
from conftest import import_vm_tool

from tuxtalk import command_phrases
from tuxtalk.mouse_phrases import MouseRequest

pytest.importorskip("tomllib", reason="the VM harness needs Python 3.11 or newer")
mouse = import_vm_tool("checks_mouse")
voices = import_vm_tool("voices")


def test_presses_are_read_with_their_button_and_place():
    text = "E 960,540\nright E 961,539\nmiddle A 10,20\nnoise\n"
    assert mouse.parse_presses(text) == [
        mouse.Press("left", "E", 960, 540), mouse.Press("right", "E", 961, 539),
        mouse.Press("middle", "A", 10, 20)]


def test_the_aim_is_the_last_grid_click_in_the_log():
    log = ("INFO - Mouse grid: clicked cell 5, level 1, at 960,540\n"
           "INFO - Mouse grid: right-clicked the middle of cell 3, level 3, at 533,780\n")
    assert mouse.aimed_at(log) == (533, 780)
    assert mouse.aimed_at("Mouse grid: zoomed into cell 7, level 2") is None


@pytest.mark.parametrize("presses, problem", [
    ([], "no press"),
    ([mouse.Press("left", "E", 960, 540)] * 2, "2 presses"),
    ([mouse.Press("left", "D", 960, 540)], "landed on D"),
    ([mouse.Press("right", "E", 960, 540)], "a right press"),
    ([mouse.Press("left", "E", 964, 540)], "more than 3 px"),
])
def test_a_click_that_missed_says_how(presses, problem):
    assert problem in mouse.click_problem(presses, "left", "E", (960, 540))


def test_a_click_within_three_pixels_on_the_button_passes():
    assert mouse.click_problem([mouse.Press("left", "E", 963, 537)], "left", "E",
                               (960, 540)) is None
    assert mouse.click_problem([mouse.Press("left", "C", 1, 1)], "left", "C", None) is None


def test_the_aim_must_be_the_cell_centre_of_this_screen():
    assert mouse.aim_problem((960, 540), (1920, 1080), (0.5, 0.5)) is None
    assert "not at the cell's centre 960,540" in mouse.aim_problem((640, 540), (1920, 1080),
                                                                  (0.5, 0.5))
    assert "doesn't say" in mouse.aim_problem(None, (1920, 1080), (0.5, 0.5))


def test_a_number_heard_as_digits_is_the_same_words():
    assert mouse.same_words("5.", "Five.")
    assert mouse.same_words("Click 5.", "Click five.")
    assert not mouse.same_words("Fine.", "Five.")


@pytest.mark.parametrize("phrase_id, grid, expected", [
    ("mouse_grid", False, MouseRequest("grid")),
    ("mouse_click_five", True, MouseRequest("click", 5, in_grid=True)),
    ("mouse_seven", True, MouseRequest("zoom", 7, in_grid=True)),
    ("mouse_three", True, MouseRequest("zoom", 3, in_grid=True)),
    ("mouse_click", True, MouseRequest("click", in_grid=True)),
    ("mouse_right_click_five", True, MouseRequest("right click", 5, in_grid=True)),
    ("mouse_cancel", True, MouseRequest("cancel", in_grid=True)),
    ("mouse_right_four", True, MouseRequest("nudge", 4, in_grid=True, direction="right")),
    ("mouse_five", False, None),
    ("mouse_left_click", False, MouseRequest("click")),
])
def test_each_phrase_played_is_the_command_its_check_waits_for(phrase_id, grid, expected):
    said = voices.load_phrases()[phrase_id]["say"]
    assert command_phrases.parse(said, navigation=False, actions=True, grid=grid) == expected


def test_a_nudged_click_must_land_the_steps_from_the_part_centre():
    # Cell 7 of the nine, centre (320, 900) on 1920 x 1080; "right four" is 4 * 5 px.
    seven = (1 / 6, 5 / 6)
    assert mouse.nudge_problem((340, 900), (1920, 1080), seven, (20, 0)) is None
    assert mouse.nudge_problem((342, 899), (1920, 1080), seven, (20, 0)) is None
    assert "not at 340,900" in mouse.nudge_problem((320, 900), (1920, 1080), seven, (20, 0))
    assert "doesn't say" in mouse.nudge_problem(None, (1920, 1080), seven, (20, 0))


def test_the_grid_shows_where_each_nudge_goes():
    assert mouse.NUDGE_PX == 5
    assert mouse.nudge_offset("right", 4) == (20, 0)
    assert mouse.nudge_offset("up", 2) == (0, -10)


@pytest.mark.parametrize("log, expects, problem", [
    ("Mouse grid: zoomed the desktop in 3.0x (GNOME's magnifier, proportional)\n"
     "Mouse grid: put the desktop zoom back\n", True, None),
    ("Mouse grid: zoomed into cell 7, level 2\n", False, None),
    ("Desktop zoom: KDE's Zoom effect is off\n", True, "didn't zoom"),
    ("Desktop zoom: the GNOME's magnifier is a lens or covers part of the screen; not used\n",
     True, None),
    ("Mouse grid: the desktop didn't zoom in; the grid goes on without it\n", True,
     "didn't zoom"),
    ("Mouse grid: zoomed the desktop in 3.0x (KWin, centered)\n", True, "not put back"),
    ("Mouse grid: put the desktop zoom back\n"
     "Mouse grid: zoomed the desktop in 3.0x (KWin, centered)\n", True, "not put back"),
])
def test_the_desktop_zoom_must_come_on_and_be_put_back(log, expects, problem):
    found = mouse.zoom_problem(log, expects)
    assert (found is None) if problem is None else (problem in found)


def test_zoom_in_the_log_is_told_apart_from_none():
    assert mouse.zoomed_in("Mouse grid: zoomed the desktop in 3.0x (KWin, proportional)")
    assert not mouse.zoomed_in("Mouse grid: zoomed into cell 7, level 2")


def test_the_magnifier_is_read_from_gsettings_and_must_come_back_as_it_was():
    assert mouse.parse_magnifier("false\n", "2.0\n") == (False, 2.0)
    assert mouse.parse_magnifier("", "") is None
    assert mouse.magnifier_problem((False, 2.0), (True, 3.0), (False, 2.0)) is None
    assert "wasn't on" in mouse.magnifier_problem((False, 2.0), (False, 2.0), (False, 2.0))
    assert "back" in mouse.magnifier_problem((False, 2.0), (True, 3.0), (True, 3.0))
    assert "back" in mouse.magnifier_problem((True, 2.0), (True, 3.0), (False, 1.0))
    assert mouse.magnifier_problem((True, 2.0), (True, 3.0), (True, 2.0)) is None
    assert mouse.magnifier_problem(None, None, None) is None
