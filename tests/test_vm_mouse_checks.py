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
    ("mouse_click_five", True, MouseRequest("click", 5)),
    ("mouse_seven", True, MouseRequest("zoom", 7)),
    ("mouse_three", True, MouseRequest("zoom", 3)),
    ("mouse_click", True, MouseRequest("click")),
    ("mouse_right_click_five", True, MouseRequest("right click", 5)),
    ("mouse_cancel", True, MouseRequest("cancel")),
    ("mouse_five", False, None),
    ("mouse_left_click", False, MouseRequest("click")),
])
def test_each_phrase_played_is_the_command_its_check_waits_for(phrase_id, grid, expected):
    said = voices.load_phrases()[phrase_id]["say"]
    assert command_phrases.parse(said, navigation=False, actions=True, grid=grid) == expected
