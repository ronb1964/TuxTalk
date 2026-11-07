"""Mouse by voice's words: numbers and "click" only while a grid shows."""
import pytest

from tuxtalk import command_phrases
from tuxtalk.mouse_phrases import MouseRequest


def parse(text, grid):
    return command_phrases.parse(text, navigation=True, actions=True, grid=grid)


@pytest.mark.parametrize("said", ["Five.", "5", "click", "move here", "cancel", "Too."])
def test_numbers_and_click_are_words_without_a_grid(said):
    assert parse(said, grid=False) is None


@pytest.mark.parametrize("grid", [False, True])
def test_the_grid_opens_from_any_state(grid):
    assert parse("Mouse grid.", grid) == MouseRequest("grid")
    assert parse("Show grid", grid) == MouseRequest("grid")


@pytest.mark.parametrize("said, number", [
    ("Five.", 5), ("number 12", 12), ("Too.", 2), ("twenty five", 25), ("99", 99),
    ("twenty-five", 25), ("ten", 10)])
def test_with_a_grid_a_number_zooms(said, number):
    assert parse(said, grid=True) == MouseRequest("zoom", number)


@pytest.mark.parametrize("said", ["0", "100", "a hundred", "a", "one hundred"])
def test_numbers_outside_the_grid_are_words(said):
    assert parse(said, grid=True) is None


def test_click_and_a_number_clicks_that_cell():
    assert parse("Right click five.", grid=True) == MouseRequest("right click", 5)
    assert parse("click", grid=True) == MouseRequest("click")
    assert parse("left click 7", grid=True) == MouseRequest("click", 7)
    assert parse("double click too", grid=True) == MouseRequest("double click", 2)
    assert parse("middle click number 30", grid=True) == MouseRequest("middle click", 30)


def test_move_and_cancel_with_a_grid():
    assert parse("Move here.", grid=True) == MouseRequest("move")
    assert parse("move to five", grid=True) == MouseRequest("move", 5)
    assert parse("move 12", grid=True) == MouseRequest("move", 12)
    for said in ("cancel", "Close grid.", "hide grid"):
        assert parse(said, grid=True) == MouseRequest("cancel")


def test_plain_clicks_without_a_grid():
    assert parse("Left click.", grid=False) == MouseRequest("click")
    assert parse("Right click.", grid=False) == MouseRequest("right click")
    assert parse("double click", grid=False) == MouseRequest("double click")
    assert parse("Middle click.", grid=False) == MouseRequest("middle click")
    assert parse("right click five", grid=False) is None


def test_the_commands_are_off_when_voice_commands_are_off():
    assert command_phrases.parse("Mouse grid.", navigation=True, actions=False) is None


def test_literal_types_a_mouse_command():
    assert command_phrases.literal_words(
        "literal mouse grid", navigation=True, actions=True) == "mouse grid"
    assert command_phrases.literal_words(
        "literal five", navigation=True, actions=True, grid=True) == "five"


@pytest.mark.parametrize("said", ["click the five buttons", "I moved here last year",
                                  "move here now", "five buttons", "cancel my order"])
def test_a_sentence_is_never_a_mouse_command(said):
    assert parse(said, grid=True) is None


@pytest.mark.parametrize("said", ["²", "five²", "move ²", "click ٣"])
def test_odd_digits_are_words_not_a_crash(said):
    assert parse(said, grid=True) is None


@pytest.mark.parametrize("said", ["new tab", "close Firefox", "go to youtube dot com",
                                  "select the last word", "volume up"])
def test_a_grid_leaves_other_commands_as_they_were(said):
    assert parse(said, grid=True) == parse(said, grid=False) is not None


def test_close_grid_is_not_a_window_request():
    assert parse("close grid", grid=True) == MouseRequest("cancel")
