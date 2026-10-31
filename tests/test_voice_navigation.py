"""Select and move by voice (TuxTalk Labs): which phrases are commands, and
the keys each one presses. See docs/superpowers/specs/2026-10-14-select-and-move-by-voice-design.md."""
import pytest

from tuxtalk import voice_navigation as nav
from tuxtalk import recording_state as rs
from tuxtalk import live_settings as live
from tuxtalk import keyboard_output
from tuxtalk import notices
from tuxtalk import voice_commands
from tuxtalk import delivery
from tuxtalk import focused_window

C, S = "CTRL", "SHIFT"


def keys(said, last=""):
    cmd = nav.parse(said)
    assert cmd is not None, f"not a command: {said!r}"
    return nav.chords(cmd, last)


# --- what is and isn't a command ----------------------------------------------------------

@pytest.mark.parametrize("said", [
    "I want to select the last sentence",      # inside a sentence: typed as text
    "go to the end of the road and turn left",
    "select the best one",
    "we should go back to the start",
    "select",
    "the end",
    "",
    "go back 11 words",                        # counts stop at 10
])
def test_not_commands(said):
    assert nav.parse(said) is None


@pytest.mark.parametrize("said", [
    "Select all.", "select all", "SELECT ALL!", "please select all", "select all please",
])
def test_punctuation_case_and_please_are_ignored(said):
    assert nav.parse(said) is not None


# --- what I just said ---------------------------------------------------------------------

@pytest.mark.parametrize("said", ["select that", "Select it.", "select what I said",
                                  "select what I just said", "select the last dictation"])
def test_select_that_selects_the_whole_last_dictation(said):
    assert keys(said, "Hello world.") == [((S,), "LEFT")] * 12


def test_a_leading_space_is_left_out():
    """Auto-space puts a space before a dictation; it stays unselected."""
    assert keys("select that", " Hello.") == [((S,), "LEFT")] * 6


def test_trailing_spaces_are_stepped_over_first():
    assert keys("select that", "Hello.\n") == [((), "LEFT")] + [((S,), "LEFT")] * 6


def test_select_the_last_sentence():
    assert keys("select the last sentence", "One thing. Two things.") == [((S,), "LEFT")] * 11


@pytest.mark.parametrize("said, n", [
    ("select the last word", 5), ("select last word", 5),
    ("select the last two words", 14), ("select the last 2 words", 14),
])
def test_select_the_last_words(said, n):
    """The period after the last word is stepped over, not selected (Ron,
    2026-10-18): the word is what gets replaced or fixed."""
    assert keys(said, "We finished early.") == [((), "LEFT")] + [((S,), "LEFT")] * n


def test_a_word_with_a_quote_and_question_mark_after_it():
    assert keys("select the last word", 'Did she say \u201cyes?\u201d ') == \
        [((), "LEFT")] * 3 + [((S,), "LEFT")] * 4


def test_a_last_word_with_no_mark_is_selected_whole():
    assert keys("select the last word", "We finished early ") == \
        [((), "LEFT")] + [((S,), "LEFT")] * 5


def test_what_i_said_needs_something_said():
    assert nav.chords(nav.parse("select that"), "") is None


# --- around the cursor --------------------------------------------------------------------

@pytest.mark.parametrize("said, expected", [
    ("select all", [((C,), "A")]),
    ("select everything", [((C,), "A")]),
    ("select the line", [((), "HOME"), ((S,), "END")]),
    ("select this line", [((), "HOME"), ((S,), "END")]),
    ("select the next word", [((C, S), "RIGHT")]),
    ("select the previous 3 words", [((C, S), "LEFT")] * 3),
    ("select the next two lines", [((S,), "DOWN")] * 2),
    ("select the previous line", [((S,), "UP")]),
    ("select to the end of the line", [((S,), "END")]),
    ("select to the start of the line", [((S,), "HOME")]),
    ("select to the beginning of the line", [((S,), "HOME")]),
    ("select to the end", [((C, S), "END")]),
    ("select to the top", [((C, S), "HOME")]),
])
def test_select_around_the_cursor(said, expected):
    assert keys(said) == expected


# --- moving -------------------------------------------------------------------------------

@pytest.mark.parametrize("said, expected", [
    ("go to the end", [((C,), "END")]),
    ("go to the bottom", [((C,), "END")]),
    ("go to the end of the document", [((C,), "END")]),
    ("go to the start", [((C,), "HOME")]),
    ("go to the beginning", [((C,), "HOME")]),
    ("go to the top", [((C,), "HOME")]),
    ("go to the end of the line", [((), "END")]),
    ("go to the start of the line", [((), "HOME")]),
    ("go back three words", [((C,), "LEFT")] * 3),
    ("go back a word", [((C,), "LEFT")]),
    ("go forward 2 words", [((C,), "RIGHT")] * 2),
    ("next word", [((C,), "RIGHT")]),
    ("previous word", [((C,), "LEFT")]),
    ("go back 5 characters", [((), "LEFT")] * 5),
    ("go forward one letter", [((), "RIGHT")]),
    ("go up two lines", [((), "UP")] * 2),
    ("go down a line", [((), "DOWN")]),
])
def test_moving(said, expected):
    assert keys(said) == expected


@pytest.mark.parametrize("said", ["unselect", "deselect", "unselect that", "clear the selection"])
def test_unselect(said):
    assert keys(said) == [((), "RIGHT")]


def test_every_command_has_a_name_for_the_log():
    assert nav.parse("go back three words").name == "go back 3 words"
    assert nav.parse("Select the last sentence.").name == "select the last sentence"


# --- in dictation (app.py) ----------------------------------------------------------------

@pytest.fixture
def dictation(monkeypatch):
    from tuxtalk import app
    seen = {"pressed": [], "problems": [], "beeps": 0}

    class Backend:
        ok = True

        def press_keys(self, chords):
            seen["pressed"].append(chords)
            return self.ok

    backend = Backend()
    monkeypatch.setattr(live, "voice_navigation", True)
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: backend)
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "org.kde.kwrite")
    # Of the window a test names by its class; which of its windows isn't told.
    monkeypatch.setattr(focused_window, "look", lambda: (focused_window.window_class(), None))
    monkeypatch.setattr(notices, "edit_problem", lambda kind, beeps: seen["problems"].append(kind))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda on, *a: seen.__setitem__("beeps", seen["beeps"] + 1))
    monkeypatch.setattr(rs.state, "last_inserted_text", " We finished early.")
    seen["app"], seen["backend"] = app, backend
    return seen


def test_a_command_presses_its_keys_and_clears_undo(dictation):
    assert voice_commands.handle_navigation("select the last word", False) is True
    assert dictation["pressed"] == [[((), "LEFT")] + [((S,), "LEFT")] * 5]
    assert rs.state.last_inserted_text == ""      # undo can't trust its count now
    assert dictation["beeps"] == 1 and dictation["problems"] == []


def test_ordinary_dictation_is_not_a_command(dictation):
    assert voice_commands.handle_navigation("we should go to the end of the road", False) is False
    assert dictation["pressed"] == []


def test_off_means_typed_as_text(dictation, monkeypatch):
    monkeypatch.setattr(live, "voice_navigation", False)
    assert voice_commands.handle_navigation("select all", False) is False


def test_a_terminal_is_refused(dictation, monkeypatch):
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "org.kde.konsole")
    assert voice_commands.handle_navigation("select all", False) is True
    assert dictation["pressed"] == [] and dictation["problems"] == ["nav_terminal"]


def test_select_that_with_nothing_dictated_says_so(dictation, monkeypatch):
    monkeypatch.setattr(rs.state, "last_inserted_text", "")
    assert voice_commands.handle_navigation("select that", False) is True
    assert dictation["pressed"] == [] and dictation["problems"] == ["nothing_said"]


def test_keys_that_could_not_be_pressed_say_so(dictation):
    dictation["backend"].ok = False
    assert voice_commands.handle_navigation("go to the end", False) is True
    assert dictation["problems"] == ["shortcut_failed"]


@pytest.mark.parametrize("said", ["select the last word", "delete last word",
                                  "clear everything"])
def test_a_voice_command_presses_nothing_once_another_window_has_the_focus(
        dictation, monkeypatch, keyboard, said):
    """Said in KWrite, but the user clicked into Konsole before the keys
    went out (a Right Ctrl dictation key still held): the keys went into the
    terminal."""
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    looks = iter([("org.kde.kwrite", ("tray", "{1}"))])
    monkeypatch.setattr(focused_window, "look",
                        lambda: next(looks, ("org.kde.konsole", ("tray", "{2}"))))
    monkeypatch.setattr(keyboard_output, "output_backend", VirtualKeyboardOutputBackend)
    assert (voice_commands.handle_undo(said, False, False)
            or voice_commands.handle_navigation(said, False)) is True
    assert keyboard.events == [] and dictation["problems"] == ["focus_moved"]
    if said == "delete last word":       # nothing deleted: a second try still can
        assert rs.state.last_inserted_text == " We finished early."


def test_each_modifier_is_held_around_its_key(keyboard):
    assert keyboard_output.send_chords([((C, S), "LEFT"), ((), "END")]) is True
    assert keyboard.events == [(29, 1), (42, 1), (105, 1), (105, 0), (42, 0), (29, 0),
                               (107, 1), (107, 0)]


def test_shift_is_held_once_across_a_run_of_keys(keyboard):
    """Selecting a sentence crawled across the line: forty Shift presses at
    ydotool's typing speed (Ron, 2026-10-14)."""
    keyboard_output.send_chords([((), "LEFT")] + [((S,), "LEFT")] * 3)
    assert keyboard.events == [(105, 1), (105, 0), (42, 1), (105, 1), (105, 0), (105, 1),
                               (105, 0), (105, 1), (105, 0), (42, 0)]


def test_what_i_said_is_only_trusted_in_the_window_it_went_to(dictation, monkeypatch):
    """Ron dictated a chat message, then said "select the last sentence" in
    Writer: it selected the chat sentence's length of Writer's text."""
    monkeypatch.setattr(rs.state, "last_inserted_window", "claude")
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "libreoffice-writer")
    assert voice_commands.handle_navigation("select the last sentence", False) is True
    assert dictation["pressed"] == [] and dictation["problems"] == ["nothing_said"]
    # A word move there uses the app's own keys instead of counting.
    voice_commands.handle_navigation("go back three words", False)
    assert dictation["pressed"] == [[((C,), "LEFT")] * 3]


def test_the_same_window_is_trusted(dictation, monkeypatch):
    monkeypatch.setattr(rs.state, "last_inserted_window", "org.kde.kwrite")
    voice_commands.handle_navigation("select the last word", False)
    assert dictation["pressed"] == [[((), "LEFT")] + [((S,), "LEFT")] * 5]


def test_the_setting_is_off_by_default_and_live():
    from tuxtalk import config
    assert config.Settings().voice_navigation is False
    assert "voice_navigation" in config.LIVE_APPLIED_KEYS


# --- exact word moves in what TuxTalk typed (Ron, 2026-10-14) -----------------------------

def test_go_back_three_words_counts_the_period_with_its_word():
    """KWrite's Ctrl+Left stops at the period, so three presses went two words."""
    p = nav.plan(nav.parse("go back three words"), "We finished the job early.", "")
    assert p.chords == [((), "LEFT")] * len("the job early.")
    assert (p.left, p.right) == ("We finished ", "the job early.")


def test_moves_keep_counting_from_where_the_last_one_left_off():
    p = nav.plan(nav.parse("go back two words"), "We finished the job early.", "")
    p = nav.plan(nav.parse("go forward one word"), p.left, p.right)
    assert p.chords == [((), "RIGHT")] * len("job ")
    assert (p.left, p.right) == ("We finished the job ", "early.")


def test_letters_and_the_start_of_the_text():
    p = nav.plan(nav.parse("go back 3 letters"), "Hi.", "")
    assert p.chords == [((), "LEFT")] * 3
    p = nav.plan(nav.parse("go back 5 words"), "Hi there.", "")
    assert p.chords == [((), "LEFT")] * len("Hi there.")      # stops at the start


def test_select_previous_words_is_exact_too_and_ends_the_tracking():
    p = nav.plan(nav.parse("select the previous 2 words"), "We finished early.", "")
    assert p.chords == [((S,), "LEFT")] * len("finished early.") and p.left is None


def test_select_that_after_moving_goes_back_to_the_end_first():
    p = nav.plan(nav.parse("select that"), "Hello ", "world.")
    assert p.chords == [((), "RIGHT")] * 6 + [((S,), "LEFT")] * 12


def test_unknown_text_falls_back_to_the_apps_own_keys():
    p = nav.plan(nav.parse("go back three words"), None, "")
    assert p.chords == [((C,), "LEFT")] * 3 and p.left is None
    assert nav.plan(nav.parse("go to the end"), "Hello.", "").left is None


@pytest.mark.parametrize("said", ["select that line", "select this line", "select the current line",
                                  "select line", "go to the end of this line",
                                  "go to the start of that line", "select to the end of this line"])
def test_this_that_and_current_line(said):
    assert nav.parse(said) is not None


def test_dictation_remembers_the_place_between_moves(dictation):
    voice_commands.handle_navigation("go back two words", False)       # " We finished early."
    voice_commands.handle_navigation("go back one word", False)
    assert dictation["pressed"][1] == [((), "LEFT")] * len("We ")
    assert rs.state.nav_context == (" ", "We finished early.")


def test_after_press_enter_a_move_doesnt_count_on_an_older_one(dictation, monkeypatch):
    """A move's memory of where the cursor sits outlived "..., press enter":
    the next "select the last word" counted on a dictation long gone and
    selected the wrong text."""
    class Keys:
        def press_keys(self, chords, claim=None):
            return True
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: Keys())
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(rs.state, "nav_context", ("An older dictation.", ""))
    assert delivery.press_key("ENTER", 'said "press enter"')
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: dictation["backend"])
    assert voice_commands.handle_navigation("select the last word", False) is True
    assert dictation["pressed"] == [] and dictation["problems"] == ["nothing_said"]


# --- the same check for "delete last word" ------------------------------------------------

def test_delete_last_word_in_another_window_deletes_nothing(dictation, monkeypatch):
    deleted = []
    dictation["backend"].backspaces = lambda n: deleted.append(n) or True
    monkeypatch.setattr(rs.state, "last_inserted_window", "claude")
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "libreoffice-writer")
    assert voice_commands.handle_undo("delete last word", False, False) is True
    assert deleted == [] and dictation["problems"] == ["undo_other_window"]
    assert rs.state.last_inserted_text == " We finished early."   # still there for its window


def test_undo_in_another_window_of_the_same_app_deletes_nothing(dictation, monkeypatch):
    """Two KWrite documents: dictated into one, "delete last word" in the
    other deleted its text, as only the app was compared."""
    deleted = []
    dictation["backend"].backspaces = lambda n: deleted.append(n) or True
    monkeypatch.setattr(rs.state, "last_inserted_window", "org.kde.kwrite")
    monkeypatch.setattr(rs.state, "last_inserted_where", ("org.kde.kwrite", ("tray", "{first}")))
    monkeypatch.setattr(focused_window, "look", lambda: ("org.kde.kwrite", ("tray", "{second}")))
    assert voice_commands.handle_undo("delete last word", False, False) is True
    assert deleted == [] and dictation["problems"] == ["undo_other_window"]


def test_undo_in_the_same_window_by_its_id_still_works(dictation, monkeypatch):
    deleted = []
    dictation["backend"].backspaces = lambda n: deleted.append(n) or True
    monkeypatch.setattr(rs.state, "last_inserted_window", "org.kde.kwrite")
    monkeypatch.setattr(rs.state, "last_inserted_where", ("org.kde.kwrite", ("tray", "{first}")))
    monkeypatch.setattr(focused_window, "look", lambda: ("org.kde.kwrite", ("tray", "{first}")))
    assert voice_commands.handle_undo("delete last word", False, False) is True
    assert deleted and dictation["problems"] == []


def test_delete_last_word_in_its_own_window_still_works(dictation, monkeypatch):
    deleted = []
    dictation["backend"].backspaces = lambda n: deleted.append(n) or True
    monkeypatch.setattr(rs.state, "last_inserted_window", "org.kde.kwrite")
    assert voice_commands.handle_undo("delete last word", False, False) is True
    assert deleted == [6] and dictation["problems"] == []


def test_delete_everything_in_a_terminal_clears_the_line_the_shells_way(dictation, monkeypatch):
    """Ctrl+A in Konsole jumped to the start of the line and Backspace
    deleted nothing (Ron, 2026-10-14)."""
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "org.kde.konsole")
    dictation["backend"].select_all_delete = lambda: pytest.fail("Ctrl+A in a terminal")
    assert voice_commands.handle_undo("delete everything", False, False) is True
    assert dictation["pressed"] == [[((C,), "E"), ((C,), "U")]]


def test_delete_everything_elsewhere_still_selects_all_and_deletes(dictation):
    cleared = []
    dictation["backend"].select_all_delete = lambda: cleared.append(1) or True
    assert voice_commands.handle_undo("delete everything", False, False) is True
    assert cleared == [1] and dictation["pressed"] == []
