"""Mouse by voice's words: numbers and "click" only while a grid shows."""
import pytest

from tuxtalk import command_phrases
from tuxtalk.mouse_phrases import MouseRequest


def parse(text, grid):
    return command_phrases.parse(text, navigation=True, actions=True, grid=grid)


@pytest.mark.parametrize("said", ["Five.", "5", "click", "move here", "cancel", "Too."])
def test_numbers_and_click_are_words_without_a_grid(said):
    assert parse(said, grid=False) is None


def test_a_request_heard_with_a_grid_says_so():
    """mouse_grid then never does it once the grid is gone: a bare "click"
    meant for the grid isn't a click wherever the pointer was left."""
    assert parse("click", grid=True).in_grid
    assert parse("Left click.", grid=True).in_grid
    assert not parse("Left click.", grid=False).in_grid


@pytest.mark.parametrize("grid", [False, True])
def test_the_grid_opens_from_any_state(grid):
    assert parse("Mouse grid.", grid) == MouseRequest("grid")
    assert parse("Show grid", grid) == MouseRequest("grid")


@pytest.mark.parametrize("said, number", [
    ("Five.", 5), ("number 12", 12), ("Too.", 2), ("free", 3), ("twenty five", 25), ("99", 99),
    ("twenty-five", 25), ("ten", 10)])
def test_with_a_grid_a_number_zooms(said, number):
    assert parse(said, grid=True) == MouseRequest("zoom", number, in_grid=True)


@pytest.mark.parametrize("said", ["0", "100", "a hundred", "a", "one hundred"])
def test_numbers_outside_the_grid_are_words(said):
    assert parse(said, grid=True) is None


def test_click_and_a_number_clicks_that_cell():
    assert parse("Right click five.", grid=True) == MouseRequest("right click", 5, in_grid=True)
    assert parse("click", grid=True) == MouseRequest("click", in_grid=True)
    assert parse("left click 7", grid=True) == MouseRequest("click", 7, in_grid=True)
    assert parse("double click too", grid=True) == MouseRequest("double click", 2, in_grid=True)
    assert parse("middle click number 30", grid=True) == MouseRequest("middle click", 30, in_grid=True)


def test_move_and_cancel_with_a_grid():
    assert parse("Move here.", grid=True) == MouseRequest("move", in_grid=True)
    assert parse("move to five", grid=True) == MouseRequest("move", 5, in_grid=True)
    assert parse("move 12", grid=True) == MouseRequest("move", 12, in_grid=True)
    for said in ("cancel", "Close grid.", "hide grid"):
        assert parse(said, grid=True) == MouseRequest("cancel", in_grid=True)


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
    assert parse("close grid", grid=True) == MouseRequest("cancel", in_grid=True)


@pytest.mark.parametrize("said, direction, steps", [
    ("left", "left", 1), ("Left.", "left", 1), ("left 3", "left", 3),
    ("left three", "left", 3), ("up two", "up", 2), ("down too", "down", 2),
    ("Right 10.", "right", 10), ("Up to.", "up", 2), ("right for", "right", 4),
    ("left too", "left", 2)])
def test_with_a_grid_a_direction_nudges(said, direction, steps):
    assert parse(said, grid=True) == MouseRequest(
        "nudge", steps, direction=direction, in_grid=True)


@pytest.mark.parametrize("said", ["left", "up two", "down 3"])
def test_nudge_words_are_words_without_a_grid(said):
    assert parse(said, grid=False) is None


def test_move_left_is_not_a_nudge():
    assert parse("move left", grid=True) is None


def test_left_click_still_clicks_with_and_without_a_grid():
    assert parse("left click", grid=True) == MouseRequest("click", in_grid=True)
    assert parse("left click", grid=False) == MouseRequest("click")


@pytest.mark.parametrize("said, steps", [("Write.", 1), ("write four", 4), ("Write 4.", 4)])
def test_with_a_grid_write_is_heard_as_right(said, steps):
    """Parakeet writes "Right four." as "Write four."."""
    assert parse(said, grid=True) == MouseRequest("nudge", steps, direction="right", in_grid=True)


def test_with_a_grid_write_click_is_a_right_click():
    assert parse("Write click.", grid=True) == MouseRequest("right click", in_grid=True)
    assert parse("write click five", grid=True) == MouseRequest("right click", 5, in_grid=True)


@pytest.mark.parametrize("said", ["write", "Write four.", "write click", "up to", "right for"])
def test_homophones_are_words_without_a_grid(said):
    assert parse(said, grid=False) is None


def test_literal_types_write_four_with_a_grid():
    assert command_phrases.literal_words(
        "literal write four", navigation=True, actions=True, grid=True) == "write four"


@pytest.mark.parametrize("said", ["write it down", "writer", "up to you", "right for me"])
def test_a_sentence_with_a_homophone_is_not_a_nudge(said):
    assert parse(said, grid=True) is None


@pytest.mark.parametrize("said, wanted", [
    ("Free.", MouseRequest("zoom", 3, in_grid=True)),
    ("click free", MouseRequest("click", 3, in_grid=True)),
    ("move free", MouseRequest("move", 3, in_grid=True)),
    ("right free", MouseRequest("nudge", 3, direction="right", in_grid=True))])
def test_with_a_grid_free_is_three(said, wanted):
    """Parakeet writes "Three." said alone as "Free"."""
    assert parse(said, grid=True) == wanted


@pytest.mark.parametrize("said", ["Free.", "click free", "move free", "right free"])
def test_free_is_a_word_without_a_grid(said):
    assert parse(said, grid=False) is None
