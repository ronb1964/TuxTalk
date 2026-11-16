"""A dictation fitted into the text on either side of the cursor: the
capital, the spaces and the closing period (sentence_fit)."""
import pytest

from tuxtalk.sentence_fit import capitalised_nouns, fit, fit_end


@pytest.mark.parametrize("before, after, typed", [
    ("the big ", " dog", "very"),       # between two spaces
    ("the big", " dog", " very"),       # right after the word
    ("the big ", "dog", "very "),       # right before the next word
    ("the big", ", dog", " very"),      # before a comma
], ids=["between spaces", "after a word", "before a word", "before a comma"])
def test_a_word_said_into_the_middle_of_a_sentence_joins_it(before, after, typed):
    assert fit("Very. ", before, after, True) == typed


def test_at_the_end_of_a_sentence_still_being_written_it_carries_it_on():
    assert fit("Very. ", "the big", "", True) == " very. "


def test_after_a_full_stop_with_no_space_a_new_sentence_gets_its_space():
    assert fit("Next one. ", "done.", "", True) == " Next one. "


@pytest.mark.parametrize("before", ["", "end.\n", "end. "])
def test_at_the_start_of_a_line_or_sentence_nothing_changes(before):
    assert fit("Hello there. ", before, "", True) == "Hello there. "


def test_i_keeps_its_capital_mid_sentence():
    assert fit("I think. ", "and ", "", True) == "I think. "


def test_one_of_your_words_keeps_its_capitals():
    assert fit("TuxTalk works. ", "so ", "", True, words=["TuxTalk"]) == "TuxTalk works. "


def test_a_language_that_capitalises_nouns_keeps_the_capital():
    assert fit("Hund. ", "der große", "", False) == " Hund. "


def test_no_space_goes_after_an_opening_bracket():
    assert fit("Very. ", "the (", "", True) == "very. "


def test_a_line_break_after_the_cursor_ends_nothing_early():
    """Text on the next line doesn't follow on the same line: the period stays."""
    assert fit("Very. ", "the big", "\nNext", True) == " very. "


@pytest.mark.parametrize("language, spoken, system, nouns", [
    ("de", (), "en_US", True),
    ("lb", (), "en_US", True),
    ("de-CH", (), "en_US", True),
    (None, ("lb-LU",), "en_US", True),
    (None, ("en", "de"), "en_US", True),
    (None, (), "de_CH", True),
    ("en", ("de",), "de_DE", False),
    (None, (), "en_US", False),
    (None, (), "", False),
])
def test_which_languages_capitalise_their_nouns(language, spoken, system, nouns):
    assert capitalised_nouns(language, spoken, system) is nouns


def test_said_punctuation_joins_the_word_before_it():
    assert fit(", ", "Hello", "", True) == ", "


@pytest.mark.parametrize("before", ["(see the note.)", 'He said "Stop."', "end.”"])
def test_a_closing_bracket_or_quote_after_a_full_stop_still_ends_the_sentence(before):
    assert fit("Next one. ", before, "", True) == " Next one. "


def test_no_space_goes_after_an_opening_straight_quote():
    assert fit("Very. ", 'he said "', "", True) == "very. "


def test_qt_s_paragraph_separator_counts_as_a_line_break():
    assert fit("Thanks a lot. ", "Hi John, ", "", True) == "Thanks a lot. "


def test_a_sentence_put_in_front_of_another_keeps_its_period():
    assert fit("I am back. ", "Hi. ", "How are", True) == "I am back. "


def test_a_dictation_ending_in_a_line_break_starts_the_next_line_clean():
    assert fit("Very.\n", "the big", "dog", True) == " very.\n"


def test_a_dictation_starting_with_a_line_break_gets_no_space_before_it():
    assert fit("\nHello. ", "the end", "", True) == "\nHello. "


@pytest.mark.parametrize("before, said, following, typed", [
    ("Yesterday ", "After lunch. ", "I went", "after lunch "),
    ("Yesterday ", "After lunch. ", "NASA said", "after lunch "),
    ("I live in ", "Beautiful. ", "New York", "beautiful "),
], ids=["before I", "before an acronym", "before a name"])
def test_a_word_put_inside_a_sentence_keeps_no_period_before_a_capital(before, said, following,
                                                                       typed):
    assert fit(said, before, following, True) == typed


def test_a_sentence_put_before_one_starting_with_i_keeps_its_period():
    assert fit("I am back. ", "Hi. ", "I went home", True) == "I am back. "


def test_two_sentences_put_inside_one_keep_the_period_before_the_next():
    assert fit("After lunch. We left. ", "Yesterday ", "Today we", True) == \
        "after lunch. We left. "


@pytest.mark.parametrize("said, typed", [
    ("The U.S. near. ", "the U.S. near "),
    ("Near Dr. Hill. ", "near Dr. Hill "),
    ("Near J. Smith. ", "near J. Smith "),
], ids=["U.S.", "Dr.", "an initial"])
def test_an_abbreviation_s_period_is_no_sentence_break(said, typed):
    assert fit(said, "I live in ", "New York", True) == typed


@pytest.mark.parametrize("said", ["It cost 3.14. We left. ", "See github.com. We left. "])
def test_a_number_or_an_address_before_a_sentence_end_is_still_a_break(said):
    assert fit(said, "Yesterday ", "Today we", True).endswith("We left. ")



@pytest.mark.parametrize("text, after, fitted", [
    ("after lunch. ", " dog", "after lunch"),       # mid-line: no period, no second space
    ("after lunch. ", "dog", "after lunch "),       # right before the next word: its space
    (". ", " dog", ""),                             # a held-back end alone
    ("after lunch. ", "", "after lunch. "),         # the end of the field: as it was
    ("after lunch. ", "\nNext", "after lunch. "),   # the end of the line
    ("after lunch. ", " I went", "after lunch."),   # before a capital the period stays
    ("I go. Go. ", " Now", "I go. Go."),            # two sentences: so does theirs
], ids=["mid-line", "before a word", "held end", "end of field", "end of line",
        "before a capital", "two sentences"])
def test_the_end_of_a_type_at_pause_phrase_fits_the_text_after_the_cursor(text, after, fitted):
    assert fit_end(text, after) == fitted
