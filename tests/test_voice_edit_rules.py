"""Voice edits that don't go to the AI (voice_edit_rules.py).

The 1.5B model is unreliable at exact jobs: on two selected lines "Testing one,
two, three, four." / "Testing one, two, three." it wrote "DRYWALL ScreWz" for
all caps, never removed a comma, dropped the second line for "remove the word
testing", and could only say "No change" to "make this bold". So exact jobs
are done in code; formatting is the app's own shortcut, or an honest "can't".
"""
import pytest

from tuxtalk import keyboard_layout as kl
from tuxtalk import voice_edit_rules as r

RON = "Testing one, two, three, four.\nTesting one, two, three."


# --- done in code ----------------------------------------------------------------

@pytest.mark.parametrize("said", [
    "Make this all caps.", "Change this to all capital letters.", "All caps",
    "make it uppercase", "Put it in capitals.", "Uppercase this", "Capitalize everything.",
    "Please convert this to upper case.",
])
def test_all_caps_keeps_every_line(said):
    assert r.mechanical_edit(RON, said) == "TESTING ONE, TWO, THREE, FOUR.\nTESTING ONE, TWO, THREE."


@pytest.mark.parametrize("said", ["Make this lowercase.", "all lower case", "Change it to small letters."])
def test_lowercase(said):
    assert r.mechanical_edit("Call GEORGE Monday.", said) == "call george monday."


@pytest.mark.parametrize("said", ["Title case.", "Capitalize each word.", "Capitalize the first letter of every word"])
def test_title_case_leaves_the_rest_of_each_word_alone(said):
    assert r.mechanical_edit("order the iPhone case from NASA", said) == "Order The IPhone Case From NASA"
    assert r.mechanical_edit("don't stop", said) == "Don't Stop"


@pytest.mark.parametrize("said, out", [
    ("Remove the commas.", "Testing one two three four.\nTesting one two three."),
    ("Take out all the periods", "Testing one, two, three, four\nTesting one, two, three"),
    ("get rid of the punctuation", "Testing one two three four\nTesting one two three"),
])
def test_removing_punctuation(said, out):
    assert r.mechanical_edit(RON, said) == out


def test_numbers_keep_their_commas_and_decimal_points():
    assert r.mechanical_edit("It costs 1,250.50, plus tax.", "remove the commas") == "It costs 1,250.50 plus tax."
    assert r.mechanical_edit("Use 3.5 inch screws. Thanks.", "remove the periods") == "Use 3.5 inch screws Thanks"


def test_removing_dashes_takes_list_dashes_too():
    assert r.mechanical_edit("- Buy plywood\n- well-known brand", "remove the dashes") == "Buy plywood\nwell known brand"


@pytest.mark.parametrize("said", ["Remove the word testing.", "delete the word 'testing'", "Take out the word “Testing”"])
def test_removing_a_word_keeps_every_line_and_capitalises_the_new_start(said):
    assert r.mechanical_edit(RON, said) == "One, two, three, four.\nOne, two, three."


def test_removing_a_word_mid_sentence_tidies_the_spaces():
    assert (r.mechanical_edit("We really need the really big van.", "remove the word really")
            == "We need the big van.")
    assert r.mechanical_edit("Wait, so we go.", "remove the word so") == "Wait, we go."


def test_removing_a_phrase():
    assert r.mechanical_edit("Call George, you know, after lunch.", "remove the words you know") == "Call George, after lunch."


@pytest.mark.parametrize("said", ["Remove the bullets.", "take out the bullet points", "remove the numbering"])
def test_removing_bullets_and_numbering(said):
    text = "- Buy plywood\n2. Call George\n  * Order 3.5 inch screws\n• Paint\n10) Sand"
    assert r.mechanical_edit(text, said) == "Buy plywood\nCall George\n  Order 3.5 inch screws\nPaint\nSand"


def test_a_word_that_isnt_there_goes_to_the_ai():
    assert r.mechanical_edit(RON, "remove the word plywood") is None


def test_only_whole_words_are_removed():
    assert r.mechanical_edit("The theme is there.", "remove the word the") == "Theme is there."


@pytest.mark.parametrize("said", [
    "Make this more formal.", "Add an exclamation point.",
    "Remove the word testing and make it formal", "capitalize this sentence", "Shorten this.",
])
def test_everything_else_goes_to_the_ai(said):
    assert r.mechanical_edit(RON, said) is None


# --- formatting ----------------------------------------------------------------------

@pytest.mark.parametrize("said, keys", [
    ("Make this bold.", ["bold"]),
    ("Bold this", ["bold"]),
    ("Make this italics.", ["italic"]),
    ("italicize it", ["italic"]),
    ("Underline this.", ["underline"]),
    ("Make it bold and italic", ["bold", "italic"]),
    ("Put this in bold, please.", ["bold"]),
    ("Make the text bold and underlined.", ["bold", "underline"]),
])
def test_bold_italic_underline_are_shortcuts(said, keys):
    assert r.formatting(said) == keys


@pytest.mark.parametrize("said", [
    "Make this text larger.", "Make the font bigger", "Change the color to red.",
    "make it smaller", "Change the font.", "Highlight this", "Strike through this.",
    "Make it size 14", "Center this.",
])
def test_other_formatting_is_an_honest_cant(said):
    assert r.formatting(said) == r.CANT_FORMAT


@pytest.mark.parametrize("said", [
    "Make this sound bold and confident.", "Make this more formal.", "Change red to blue.",
    "Make this bold and more formal", "Make it longer", "Title case.",
])
def test_wording_requests_are_not_formatting(said):
    assert r.formatting(said) is None


@pytest.mark.parametrize("cls, ok", [
    ("libreoffice-writer", True), ("soffice", True), ("org.libreoffice.LibreOffice", True),
    ("ONLYOFFICE-desktopeditors", True), ("abiword", True),
    ("org.kde.kwrite", False), ("firefox", False), ("betterbird", False), (None, False),
])
def test_shortcuts_are_sent_only_to_word_processors(cls, ok):
    assert r.has_formatting_shortcuts(cls) is ok


# --- which instructions may change the lines -----------------------------------------

@pytest.mark.parametrize("said", [
    "Make a bulleted list.", "Make a numbered list", "Join these into one paragraph.",
    "Shorten this.", "Summarize this", "Turn this into an email", "Split this into sentences",
    "Sort these alphabetically",
])
def test_layout_changes_may_change_the_lines(said):
    assert r.keeps_lines(said) is False


@pytest.mark.parametrize("said", [
    "Make this more formal.", "Add an exclamation point.", "Fix the grammar",
    "Translate this to Spanish.", "Remove the bullets",
])
def test_other_edits_keep_the_lines(said):
    assert r.keeps_lines(said) is True


# --- shortcut keys on the active keyboard layout --------------------------------------

def test_shortcut_keys_on_us_qwertz_and_azerty(monkeypatch):
    for layout in [("us", "", ""), ("de", "", ""), ("fr", "", "")]:
        monkeypatch.setattr(kl, "active_layout", lambda layout=layout: layout)
        assert [kl.shortcut_keycode(c) for c in "biu"] == [48, 23, 22]


def test_shortcut_keys_follow_dvorak(monkeypatch):
    """Dvorak's B is where QWERTY has N: the US key there would be Ctrl+X, cut."""
    monkeypatch.setattr(kl, "active_layout", lambda: ("us", "dvorak", ""))
    assert kl.shortcut_keycode("b") == 49


def test_a_layout_without_latin_letters_uses_the_us_keys(monkeypatch):
    monkeypatch.setattr(kl, "active_layout", lambda: ("ru", "", ""))
    assert kl.shortcut_keycode("b") == 48
    monkeypatch.setattr(kl, "active_layout", lambda: None)
    assert kl.shortcut_keycode("u") == 22


# --- lists, indents and deleting -----------------------------------------------------------
# The AI makes "make this a bulleted list" numbered, changes nothing for "add an
# indent", and answers "delete this" with "None.", so these are done in code.

TWO = "Testing one, two, three, four, five.\nTesting one two three four five six."


@pytest.mark.parametrize("said", ["Make this a bulleted list.", "bullet points", "Turn this into a bullet list",
                                  "Add bullets"])
def test_a_bulleted_list_of_lines_is_made_in_code(said):
    assert r.mechanical_edit(TWO, said) == ("- Testing one, two, three, four, five.\n"
                                            "- Testing one two three four five six.")


@pytest.mark.parametrize("said", ["Make this a numbered list.", "Number these lines", "numbered list"])
def test_a_numbered_list_replaces_bullets(said):
    assert r.mechanical_edit("- Buy plywood\n\n- Call George", said) == "1. Buy plywood\n\n2. Call George"


def test_one_paragraph_into_a_list_needs_the_ai():
    assert r.mechanical_edit("Buy plywood. Call George.", "make this a bulleted list") is None


@pytest.mark.parametrize("said", ["Indent this.", "Add an indent.", "indent these lines"])
def test_indenting(said):
    assert r.mechanical_edit("One\n\n  Two", said) == "    One\n\n      Two"


@pytest.mark.parametrize("said", ["Remove the indent.", "unindent this", "outdent", "remove the indentation"])
def test_removing_the_indent(said):
    assert r.mechanical_edit("    One\n\tTwo", said) == "One\nTwo"


def test_removing_the_indent_takes_off_one_level():
    """Undoes "indent this": nested lines keep their place under the others."""
    assert r.mechanical_edit("        One\n    Two\n  Three\n\t\tFour", "unindent this") == \
        "    One\nTwo\nThree\n\tFour"


@pytest.mark.parametrize("said, yes", [
    ("Delete this.", True), ("remove it", True), ("Erase all of this", True), ("delete everything", True),
    ("Get rid of this.", True), ("Delete the selection", True),
    ("Delete the last sentence.", False), ("Cut this", False), ("Remove the commas", False), ("delete the word testing", False),
])
def test_deleting_the_selection(said, yes):
    assert r.is_delete(said) is yes


# --- only what was asked about changes ---------------------------------------------------

def test_removing_an_opening_word_capitalises_only_the_word_after_it():
    """Not every lowercase sentence start on the line ("IPhone")."""
    assert (r.mechanical_edit("Testing this. iPhone is great.", "remove the word testing")
            == "This. iPhone is great.")
    assert (r.mechanical_edit("Testing that, e.g. apples work.", "remove the word testing")
            == "That, e.g. apples work.")


@pytest.mark.parametrize("text, said, out", [
    ("Testing 3 times over.", "remove the word testing", "3 times over."),
    ("Um, 2 of them came.", "remove the word um", "2 of them came."),
])
def test_a_number_after_the_removed_word_leaves_the_next_word_alone(text, said, out):
    """Not the first letter after the number ("3 Times over.")."""
    assert r.mechanical_edit(text, said) == out


def test_lines_without_the_word_are_left_exactly_as_they_were():
    assert (r.mechanical_edit("Testing one\nkeep  this , line\n: label here", "remove the word testing")
            == "One\nkeep  this , line\n: label here")


def test_lines_without_the_mark_are_left_exactly_as_they_were():
    assert r.mechanical_edit("a, b\nx  y : z", "remove the commas") == "a b\nx  y : z"


@pytest.mark.parametrize("said", [
    "Make this red.", "make it blue", "Make the text green.", "Make it 14 point.",
    "make this 12pt", "Make it 16-point font.", "Make it red and bold.",
])
def test_a_colour_or_point_size_is_an_honest_cant(said):
    """"Make this red" must not go to the AI, which rewords the selection."""
    assert r.formatting(said) == r.CANT_FORMAT


@pytest.mark.parametrize("said", [
    "Change red to blue.", "Add an exclamation point.", "Make it say red.", "Make this point clearer.",
])
def test_colour_and_point_words_about_the_wording_still_go_to_the_ai(said):
    assert r.formatting(said) is None


# --- two changes at once (0.15.1) --------------------------------------------------------

@pytest.mark.parametrize("said, steps", [
    ("make it formal and remove the commas", ["make it formal", "remove the commas"]),
    ("Remove the commas, then make it all caps.", ["Remove the commas", "make it all caps"]),
    ("translate it to Spanish and then make it shorter",
     ["translate it to Spanish", "make it shorter"]),
    ("make it formal, remove the commas and add a title",
     ["make it formal", "remove the commas", "add a title"]),
    # One instruction, even with "and" in it: the second part isn't a command.
    ("make it shorter and simpler", ["make it shorter and simpler"]),
    ("remove the commas and periods", ["remove the commas and periods"]),
    ("make it sound friendly and professional", ["make it sound friendly and professional"]),
    ("replace cats and dogs with pets", ["replace cats and dogs with pets"]),
    ("make it formal", ["make it formal"]),
    # Keys pressed in the app (bold, Delete) can't follow a rewrite: one step.
    ("make it bold and remove the commas", ["make it bold and remove the commas"]),
    ("remove the commas and delete it", ["remove the commas and delete it"]),
])
def test_split_steps(said, steps):
    from tuxtalk.voice_edit_rules import split_steps
    assert split_steps(said) == steps


def test_at_most_three_steps():
    from tuxtalk.voice_edit_rules import split_steps
    said = "make it formal and remove the commas and add a title and make it all caps"
    assert split_steps(said) == [said]
