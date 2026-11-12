"""KWrite and Kate move the cursor to the line's start when Ctrl+C copies the
whole line with nothing selected (KTextEditor's copy()). Voice editing's
selection check presses that Ctrl+C, so it puts the cursor back where the
last dictation left it: "select the last sentence" selected text in the
paragraph before, and the next dictation landed at the line's start."""
import pytest

from tuxtalk import keyboard_output, voice_edit
from tuxtalk import recording_state as rs

PARAGRAPH = ("Okay, this is another little dictation. It's going to be a new "
             "paragraph, and I'll see if select works. ")
KWRITE = ("org.kde.kwrite", "0x1")


@pytest.fixture
def pressed(monkeypatch):
    """The keys sent, as [(modifiers, key)]; the last dictation went into KWrite."""
    keys = []

    class Backend:
        def press_keys(self, chords, claim=None):
            keys.extend(chords)
            return True
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: Backend())
    monkeypatch.setattr(keyboard_output, "not_where_typed", lambda focused, now=None: False)
    monkeypatch.setattr(rs.state, "last_inserted_text", PARAGRAPH)
    return keys


def test_after_a_dictation_the_cursor_goes_back_to_its_end(pressed):
    voice_edit._put_cursor_back(PARAGRAPH + "\n", KWRITE)
    assert pressed == [((), "END"), ((), "END")]


def test_kate_by_its_other_name_too(pressed):
    voice_edit._put_cursor_back(PARAGRAPH + "\n", ("kate", "0x2"))
    assert pressed == [((), "END"), ((), "END")]


@pytest.mark.parametrize("where", [("libreoffice-writer", "0x3"), ("code", "0x4")],
                         ids=["Writer", "VS Code"])
def test_editors_that_dont_move_the_cursor_get_no_keys(pressed, where):
    voice_edit._put_cursor_back(PARAGRAPH + "\n", where)
    assert pressed == []


def test_a_line_that_doesnt_end_with_the_dictation_is_left_alone(pressed):
    """The cursor was somewhere else on that line: where can't be known."""
    voice_edit._put_cursor_back("Some other line the user wrote.\n", KWRITE)
    assert pressed == []


def test_a_dictation_in_another_window_is_no_guide(pressed, monkeypatch):
    monkeypatch.setattr(keyboard_output, "not_where_typed", lambda focused, now=None: True)
    voice_edit._put_cursor_back(PARAGRAPH + "\n", KWRITE)
    assert pressed == []


def test_a_dictation_that_ended_its_line_leaves_the_cursor_where_it_is(pressed, monkeypatch):
    """It ended with a line break: the cursor was at the next line's start,
    exactly where KWrite put it."""
    monkeypatch.setattr(rs.state, "last_inserted_text", "First line.\n")
    voice_edit._put_cursor_back("\n", KWRITE)
    assert pressed == []


def test_the_line_an_editor_copied_is_handed_on(monkeypatch):
    """The selection check gets the line back, to put the cursor back with."""
    from test_voice_editing import FakeBoard
    from tuxtalk import clipboard as cb
    board = FakeBoard(selected="the whole line\n")
    board.install(monkeypatch)
    monkeypatch.setattr(cb, "read_primary", lambda: "an older selection")
    lines = []
    text, _ = cb.copy_selection(board.ctrl_c, sleep=lambda s: None, on_line_copied=lines.append)
    assert text is None and lines == ["the whole line\n"]
