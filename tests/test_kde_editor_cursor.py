"""KWrite and Kate move the cursor to the line's start when Ctrl+C copies the
whole line with nothing selected (KTextEditor's copy()). Voice editing's
selection check presses that Ctrl+C, so it puts the cursor back where the
last dictation left it: "select the last sentence" selected text in the
paragraph before, and the next dictation landed at the line's start."""
import pytest

from tuxtalk import clipboard as cb
from tuxtalk import keyboard_output, voice_edit
from tuxtalk import recording_state as rs

PARAGRAPH = ("Okay, this is another little dictation. It's going to be a new "
             "paragraph, and I'll see if select works. ")
HEBREW = "שלום, זו הכתבה קצרה. היא ממשיכה עוד קצת כדי לבדוק את הבחירה. "
KWRITE = ("org.kde.kwrite", "0x1")


class KateLine:
    """One line in KTextEditor, and its cursor, as its End and Left keys move
    it (KateViewInternal::moveCursorToLineEnd, in
    docs/superpowers/sdd/release-0.23/kte). *rows* are where each row of a
    wrapped line starts; *smart_end* is the Smart Home setting, on by default.
    On a right-to-left line Left and Right swap (ViewPrivate::cursorLeft)."""

    def __init__(self, text, rows=(0,), smart_end=True, rtl=False):
        self.text, self.rows, self.smart_end, self.rtl = text, list(rows), smart_end, rtl
        self.column = 0         # where copy() leaves it

    def press(self, key):
        if key in ("LEFT", "RIGHT"):
            # Below 0 is the line above, past the end the line below.
            self.column += -1 if (key == "LEFT") != self.rtl else 1
            assert 0 <= self.column <= len(self.text), "the cursor left the line"
            return
        assert key == "END"
        row = max(i for i, start in enumerate(self.rows) if start <= self.column)
        if row < len(self.rows) - 1 and self.column < self.rows[row + 1] - 1:
            self.column = self.rows[row + 1] - 1      # the end of this row
        elif self.smart_end and self.column == len(self.text):
            self.column = len(self.text.rstrip())     # back before trailing spaces
        else:
            self.column = len(self.text)


def cursor_after(keys, line):
    for _mods, key in keys:
        line.press(key)
    return line.column


@pytest.mark.parametrize("line, dictated", [
    (KateLine(PARAGRAPH), PARAGRAPH),
    (KateLine(PARAGRAPH.rstrip()), PARAGRAPH.rstrip()),
    (KateLine(PARAGRAPH, rows=(0, 64)), PARAGRAPH),
    (KateLine(PARAGRAPH, rows=(0, 40, 80)), PARAGRAPH),
    (KateLine(PARAGRAPH, smart_end=False), PARAGRAPH),
    (KateLine(PARAGRAPH, rows=(0, 64), smart_end=False), PARAGRAPH),
    (KateLine(PARAGRAPH + "   "), PARAGRAPH + "   "),
    (KateLine(PARAGRAPH.rstrip()), PARAGRAPH),
    (KateLine(HEBREW, rtl=True), HEBREW),
    (KateLine(HEBREW.rstrip(), rows=(0, 30), rtl=True), HEBREW.rstrip()),
], ids=["one row", "no space after", "wrapped in two rows", "three rows",
        "Smart End off", "wrapped, Smart End off", "several spaces after",
        "saved, which took the space off", "right to left", "right to left, wrapped"])
def test_the_cursor_goes_back_to_the_end_of_the_dictation(line, dictated):
    """The space after a dictation counts: the next one mustn't join its last word."""
    keys = voice_edit._keys_back_to_dictation_end(line.text, dictated)
    assert cursor_after(keys, line) == len(line.text)


def test_after_a_dictation_that_ended_its_line_the_cursor_goes_after_kates_indent():
    line = KateLine("    ")
    assert cursor_after(voice_edit._keys_back_to_dictation_end(line.text, ""), line) == 4


def test_after_a_dictation_that_ended_its_line_an_empty_line_needs_no_keys():
    assert voice_edit._keys_back_to_dictation_end("", "") == []


@pytest.mark.parametrize("text, tail", [
    ("Some other line the user wrote.", PARAGRAPH),
    ("Text the user typed after a line break.", ""),
    ("Ends with spaces   ", "   "),
], ids=["another line", "typed on the new line", "a dictation of spaces"])
def test_a_line_that_doesnt_show_where_the_cursor_was_is_left_alone(text, tail):
    assert voice_edit._keys_back_to_dictation_end(text, tail) is None


@pytest.fixture
def kwrite(monkeypatch):
    """KWrite as the voice edit's selection check meets it: the clipboard, the
    primary selection, the keys sent, and the last dictation typed there."""
    from voice_edit_rig import FakeBoard
    board = FakeBoard(selected=PARAGRAPH + "\n")
    board.install(monkeypatch)
    world = {"board": board, "primary": None, "keys": []}
    monkeypatch.setattr(cb, "_primary_to_keep", lambda: world["primary"])

    class Backend:
        def press_keys(self, chords, claim=None):
            world["keys"].extend(chords)
            return True
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: Backend())
    monkeypatch.setattr(keyboard_output, "send_copy_keystroke", lambda claim=None: board.ctrl_c())
    monkeypatch.setattr(keyboard_output, "not_where_typed", lambda focused, now=None: False)
    monkeypatch.setattr(rs.state, "last_inserted_text", PARAGRAPH)
    return world


def copy_in(where, message=None):
    selected, _put_back = voice_edit._copy_in(
        where, lambda: keyboard_output.send_copy_keystroke(), message)
    return selected


@pytest.mark.parametrize("primary", [None, "an older selection"],
                         ids=["nothing highlighted", "something else highlighted"])
def test_a_whole_line_copy_in_kwrite_is_no_selection_and_the_cursor_goes_back(kwrite, primary):
    """With nothing highlighted yet (a new session), the copied line used to
    count as the selection: "make this uppercase" typed at the line's start."""
    kwrite["primary"] = primary
    assert copy_in(KWRITE) is None
    line = KateLine(PARAGRAPH)
    assert cursor_after(kwrite["keys"], line) == len(PARAGRAPH)


def test_kate_by_its_other_name_too(kwrite):
    assert copy_in(("kate", "0x2")) is None and kwrite["keys"]


def test_a_real_selection_in_kwrite_is_kept_and_no_keys_go(kwrite):
    """A triple-clicked line ends with its line break too, and is highlighted."""
    kwrite["primary"] = PARAGRAPH + "\n"
    assert copy_in(KWRITE) == PARAGRAPH + "\n"
    assert kwrite["keys"] == []


@pytest.mark.parametrize("where", [("libreoffice-writer", "0x3"), ("code", "0x4")],
                         ids=["Writer", "VS Code"])
def test_other_apps_with_nothing_highlighted_keep_the_line_and_get_no_keys(kwrite, where):
    """Other apps may never set the primary selection: an empty one is no proof."""
    assert copy_in(where) == PARAGRAPH + "\n"
    assert kwrite["keys"] == []


@pytest.mark.parametrize("text, rtl", [
    (HEBREW, True), ("مرحبا بكم. ", True), (PARAGRAPH, False),
    ("123 " + HEBREW, True), ("", False),
    ("hello \u05e9\u05dc\u05d5\u05dd ", False), ("\u05e9\u05dc\u05d5\u05dd hello ", True),
    ("\u2066hello\u2069 \u05e9\u05dc\u05d5\u05dd ", True),
    ("\u2067\u05e9\u05dc\u05d5\u05dd\u2069 hello ", False)],
    ids=["Hebrew", "Arabic", "English", "digits first", "empty", "English first",
         "Hebrew first", "English in an isolate", "Hebrew in an isolate"])
def test_a_line_is_right_to_left_as_qt_decides_it(text, rtl):
    """By its first letter that has a direction, outside an isolate; digits
    and spaces have none. The last four are Qt 6's own answers."""
    assert voice_edit._right_to_left(text) is rtl


def test_after_a_save_select_that_selects_the_dictation_not_a_space_before_it(kwrite):
    """KWrite took the space after the dictation off when it saved; still
    counted, "select that" selected the space before it and left the period."""
    from tuxtalk import voice_navigation as nav
    kwrite["board"].selected = PARAGRAPH.rstrip() + "\n"
    assert copy_in(KWRITE) is None
    assert rs.state.last_inserted_text == PARAGRAPH.rstrip()
    keys = nav.chords(nav.parse("select that"), rs.state.last_inserted_text)
    assert keys and ((), "LEFT") not in keys      # no step over a space that's gone


def test_a_dictation_in_another_window_is_no_guide(kwrite, monkeypatch):
    monkeypatch.setattr(keyboard_output, "not_where_typed", lambda focused, now=None: True)
    assert copy_in(KWRITE) is None and kwrite["keys"] == []


@pytest.mark.parametrize("esc_during_check", [False, True],
                         ids=["Esc before the copy", "Esc while the keys wait"])
def test_esc_stops_all_four_keys(kwrite, keyboard, monkeypatch, esc_during_check):
    """Esc can land before the copy, or while TuxTalk's keyboard waits for
    the window check after it: none of the keys go, through the real one."""
    from tuxtalk import focused_window, output_backends
    monkeypatch.setattr(keyboard_output, "output_backend",
                        lambda: output_backends.VirtualKeyboardOutputBackend())
    message = rs.Message()
    message.cancelled = not esc_during_check

    def window_check(window, now=None):
        message.cancelled = True
        return lambda: None
    monkeypatch.setattr(focused_window, "claim_into", window_check)
    assert copy_in(KWRITE, message) is None
    assert keyboard.events == []


def test_without_esc_the_keys_go_through_the_real_keyboard(kwrite, keyboard, monkeypatch):
    """The Esc test's other half: the same path does send them."""
    from tuxtalk import focused_window, output_backends
    monkeypatch.setattr(keyboard_output, "output_backend",
                        lambda: output_backends.VirtualKeyboardOutputBackend())
    monkeypatch.setattr(focused_window, "claim_into", lambda window, now=None: lambda: None)
    assert copy_in(KWRITE, rs.Message()) is None
    assert keyboard.events


def test_the_check_after_edit_that_leaves_the_cursor_alone(kwrite, monkeypatch):
    """Letting go of a selection "edit that" made copies it again; one lost
    meanwhile is a whole-line copy. "edit that" forgets the dictation it
    selected (handle_spoken_edit), so where the cursor was isn't known, and
    no keys go (CODE_HEALTH 96)."""
    monkeypatch.setattr(rs.state, "last_inserted_text", "")
    assert voice_edit._still_selected("some words", KWRITE) is False
    assert kwrite["keys"] == []
