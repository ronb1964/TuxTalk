import pytest

from tuxtalk.undo import (
    detect_undo_command,
    calculate_undo_length,
    single_unit_length,
    EVERYTHING_PATTERNS,
    NUMBER_WORDS,
)


# ---------------------------------------------------------------------------
# detect_undo_command — pattern matching and count parsing
# ---------------------------------------------------------------------------

def test_single_word_undo():
    assert detect_undo_command("delete last word") == ("word", 1)
    assert detect_undo_command("undo last word") == ("word", 1)
    assert detect_undo_command("remove last word") == ("word", 1)
    assert detect_undo_command("delete the last word") == ("word", 1)


def test_single_sentence_undo():
    assert detect_undo_command("delete last sentence") == ("sentence", 1)
    assert detect_undo_command("undo the last sentence") == ("sentence", 1)


def test_single_paragraph_undo():
    assert detect_undo_command("delete last paragraph") == ("paragraph", 1)


def test_counted_words_word_form():
    assert detect_undo_command("delete last two words") == ("word", 2)
    assert detect_undo_command("undo last five words") == ("word", 5)
    assert detect_undo_command("remove the last ten words") == ("word", 10)


def test_counted_words_digit_form():
    assert detect_undo_command("delete last 3 words") == ("word", 3)
    assert detect_undo_command("undo last 12 words") == ("word", 12)
    assert detect_undo_command("delete the last 1 word") == ("word", 1)


def test_counted_sentences():
    assert detect_undo_command("delete last two sentences") == ("sentence", 2)
    assert detect_undo_command("undo last 4 sentences") == ("sentence", 4)


def test_counted_paragraphs():
    assert detect_undo_command("delete last three paragraphs") == ("paragraph", 3)


def test_a_an_means_one():
    # "a"/"an" are in NUMBER_WORDS so they parse as count=1 in counted form,
    # but "delete a word" (without "last") doesn't match the command regex.
    assert detect_undo_command("delete a word") is None
    assert detect_undo_command("delete last a word") == ("word", 1)
    assert detect_undo_command("undo last an sentence") == ("sentence", 1)


def test_everything_patterns_all_match():
    for phrase in EVERYTHING_PATTERNS:
        assert detect_undo_command(phrase) == ("everything", 1), phrase


def test_everything_with_punctuation():
    # Whisper output often includes punctuation/casing
    assert detect_undo_command("Delete everything.") == ("everything", 1)
    assert detect_undo_command("CLEAR ALL!") == ("everything", 1)


def test_non_undo_returns_none():
    assert detect_undo_command("hello world") is None
    assert detect_undo_command("please delete my account") is None
    assert detect_undo_command("undo this please") is None
    assert detect_undo_command("") is None


def test_number_words_table_is_complete():
    # Spot-check the table covers what's advertised in the UI (one through ten)
    for word in ["one", "two", "three", "four", "five",
                 "six", "seven", "eight", "nine", "ten"]:
        assert word in NUMBER_WORDS


# ---------------------------------------------------------------------------
# calculate_undo_length — counted iteration
# ---------------------------------------------------------------------------

def test_everything_returns_full_length():
    text = "Hello world. How are you?"
    assert calculate_undo_length(text, "everything") == len(text)
    # count is ignored for 'everything'
    assert calculate_undo_length(text, "everything", count=5) == len(text)


def test_word_single():
    # "Hello world. " — last word is "world." (trailing space ignored by rfind)
    text = "Hello world. "
    # Trailing space gets included in the cut
    assert calculate_undo_length(text, "word") == len("world. ")


def test_word_count_two():
    text = "one two three four"  # 18 chars
    # Iter 1: removes "four" (4 chars), leaves "one two three " (trailing space).
    # Iter 2: trailing space gets included in the cut, removes "three " (6 chars).
    # Result: 10 chars total, "one two " remains.
    assert calculate_undo_length(text, "word", count=2) == 10


def test_sentence_single():
    text = "First. Second."  # 14 chars
    # Removing last sentence — back to the previous "." and the space after
    # it, which stays for the next dictation.
    result = calculate_undo_length(text, "sentence")
    # Should remove "Second." (7 chars)
    assert result == 7


def test_sentence_count_two():
    text = "First. Second. Third."  # 21 chars
    # Should remove "Second. Third." (14 chars) — both, back to "First. "
    result = calculate_undo_length(text, "sentence", count=2)
    assert result == 14


def test_sentence_clamps_to_available():
    text = "Only sentence."
    # Asking for 5 sentences but only have 1 → return all
    assert calculate_undo_length(text, "sentence", count=5) == len(text)


# The undo buffer holds line breaks as '\n' (app.py replaces the
# line-break markers before storing it), so these use '\n' too.
def test_paragraph_with_line_breaks():
    text = "First.\nSecond.\nThird."
    # Last paragraph "Third." is after the second line break
    result = calculate_undo_length(text, "paragraph")
    assert result == len("Third.")


def test_paragraph_count_two():
    text = "First.\nSecond.\nThird."
    # Removes "Third." + the break + "Second.". The break between "First."
    # and "Second." is left intact, matching the single-paragraph-undo
    # convention.
    result = calculate_undo_length(text, "paragraph", count=2)
    assert result == len("Second.\nThird.")
    # Sanity-check the remaining text
    assert text[:-result] == "First.\n"


def test_paragraph_count_with_blank_lines():
    assert calculate_undo_length("a\n\n\n", "paragraph", 3) == 4


def test_paragraph_count_clamps():
    text = "First.\nSecond.\nThird."
    # Asking for more than available wipes everything in the buffer
    assert calculate_undo_length(text, "paragraph", count=10) == len(text)


def test_empty_text_returns_zero():
    assert calculate_undo_length("", "word") == 0
    assert calculate_undo_length("", "sentence", count=3) == 0
    assert calculate_undo_length("", "everything") == 0


def test_zero_or_negative_count_returns_zero():
    assert calculate_undo_length("hello world", "word", count=0) == 0
    assert calculate_undo_length("hello world", "word", count=-1) == 0


# ---------------------------------------------------------------------------
# single_unit_length — boundary detection sanity
# ---------------------------------------------------------------------------

def test_single_unit_word_no_space():
    # No space in text → whole thing is the "last word"
    assert single_unit_length("oneword", "word") == 7


def test_single_unit_unknown_type():
    assert single_unit_length("text", "bogus") == 0


# =====================================================================
# The undo buffer stores line breaks as '\n'
# (one char = one backspace), so word boundaries must treat newlines
# and tabs as separators, not as part of the word.
# =====================================================================

def test_word_boundary_at_newline():
    """'hello\\nworld' — last word is 'world' (5 chars), not the whole string."""
    assert single_unit_length("hello\nworld", "word") == 5


def test_word_boundary_at_tab():
    assert single_unit_length("hello\tworld", "word") == 5


def test_paragraph_length_with_newline_breaks():
    """Guard: '\\n'-separated paragraphs measure correctly."""
    assert calculate_undo_length("first\nsecond", "paragraph", 1) == 6


# --- sentence undo must not cut inside numbers or abbreviations -------------
# The sentence boundary search must not accept any '.', or a decimal or an
# abbreviation is mistaken for the end of a sentence and undo deletes far too
# little — leaving behind something that still looks like a finished sentence.

def test_sentence_undo_does_not_cut_inside_a_price():
    text = "I paid $19.99 for the book."
    assert calculate_undo_length(text, "sentence", 1) == len(text)


def test_sentence_undo_does_not_cut_inside_a_version_number():
    text = "Version 2.5 shipped today."
    assert calculate_undo_length(text, "sentence", 1) == len(text)


def test_sentence_undo_does_not_cut_inside_a_mid_sentence_abbreviation():
    text = "I live in the U.S. and it is nice."
    assert calculate_undo_length(text, "sentence", 1) == len(text)


def test_sentence_undo_does_not_cut_inside_a_time_abbreviation():
    text = "Meet at 9 a.m. sharp tomorrow."
    assert calculate_undo_length(text, "sentence", 1) == len(text)


def test_abbreviation_ending_a_sentence_is_still_a_boundary():
    """When the next word is capitalized the period is doing double duty."""
    text = "I live in the U.S. It is nice."
    assert calculate_undo_length(text, "sentence", 1) == len("It is nice.")


def test_sentence_undo_still_finds_an_ordinary_boundary():
    text = "First sentence. Second sentence."
    assert calculate_undo_length(text, "sentence", 1) == len("Second sentence.")


def test_sentence_undo_handles_a_decimal_in_an_earlier_sentence():
    text = "It cost $19.99. That was too much."
    assert calculate_undo_length(text, "sentence", 1) == len("That was too much.")


# --- a sentence ending inside a closing quote is still a boundary
# normalize.py deliberately moves sentence punctuation INSIDE a closing smart
# quote, so `."` is a shape this app manufactures itself. Requiring whitespace
# immediately after the period would make undo skip that boundary and delete
# the previous sentence too — often the entire dictation.

def test_sentence_ending_in_a_closing_quote_is_a_boundary():
    text = 'He said, "Go." Then he left.'
    assert calculate_undo_length(text, "sentence", 1) == len("Then he left.")


def test_sentence_ending_in_a_closing_smart_quote_is_a_boundary():
    text = 'He said, “Go.” Then he left.'
    assert calculate_undo_length(text, "sentence", 1) == len("Then he left.")


def test_sentence_ending_in_a_closing_parenthesis_is_a_boundary():
    text = "We shipped it (finally.) Then we slept."
    assert calculate_undo_length(text, "sentence", 1) == len("Then we slept.")


def test_undo_does_not_swallow_the_whole_buffer_on_quoted_text():
    """The worst case: everything deleted."""
    text = 'She replied, "No." I walked away.'
    assert calculate_undo_length(text, "sentence", 1) < len(text)


# The last sentence itself can end inside a closing quote or bracket; the
# search must not stop at its own period and measure nothing, or "delete last
# sentence" deletes nothing after 'Hello. Hi.”'.
def test_last_sentence_ending_in_a_closing_smart_quote_is_deleted():
    assert calculate_undo_length("Hello. Hi.”", "sentence", 1) == len("Hi.”")


def test_last_sentence_ending_in_a_closing_quote_is_deleted():
    assert calculate_undo_length('Hello. Hi."', "sentence", 1) == len('Hi."')


def test_only_sentence_ending_in_a_closing_quote_is_deleted():
    assert calculate_undo_length("Hi.”", "sentence", 1) == len("Hi.”")


def test_two_sentences_ending_in_closing_quotes():
    text = "One. “Two.” “Three.”"
    assert calculate_undo_length(text, "sentence", 2) == len("“Two.” “Three.”")


def test_deleting_a_sentence_keeps_the_space_before_the_next_dictation():
    """"delete last sentence", then dictating again, gave
    "message.The power". Each dictation ends with its own space and starts
    with none, so the space after the sentence before must stay."""
    typed = "One thing popped in my head after Mio's message. The power saving feature to. "
    left = typed[:len(typed) - calculate_undo_length(typed, "sentence")]
    assert left == "One thing popped in my head after Mio's message. "
    assert left + "The power management feature. " == (
        "One thing popped in my head after Mio's message. The power management feature. ")


def test_a_line_break_before_the_sentence_stays_too():
    typed = "Dear Sam.\nSee you at noon. "
    assert typed[:len(typed) - calculate_undo_length(typed, "sentence")] == "Dear Sam.\n"


# ---------------------------------------------------------------------------
# German, with the command language set to Deutsch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said, expected", [
    ("Das letzte Wort löschen.", ("word", 1)),
    ("letztes Wort löschen", ("word", 1)),
    ("Lösche das letzte Wort", ("word", 1)),
    ("lösch den letzten Satz", ("sentence", 1)),
    ("Den letzten Absatz löschen.", ("paragraph", 1)),
    ("Die letzten drei Wörter löschen.", ("word", 3)),
    ("die letzten 3 Wörter löschen", ("word", 3)),
    ("die letzten zwei Sätze entfernen", ("sentence", 2)),
    ("Alles löschen.", ("everything", 1)),
    ("lösche alles", ("everything", 1)),
    # English stays understood.
    ("delete last word", ("word", 1)),
])
def test_german_undo_commands_with_german_commands_on(said, expected):
    assert detect_undo_command(said, "de") == expected


@pytest.mark.parametrize("said", [
    "Ich habe das letzte Wort gelöscht.",
    "Das letzte Wort hat er.",
    "Alles gut.",
    "löschen",
    "Wir löschen die Datei.",
])
def test_german_sentences_about_deleting_stay_text(said):
    assert detect_undo_command(said, "de") is None


def test_german_undo_waits_for_german_commands():
    """With the command language English, German words are dictation."""
    assert detect_undo_command("das letzte Wort löschen") is None


def test_deleting_the_last_sentence_stops_at_a_german_closing_quote():
    """„Hallo.“ ends a sentence; without “ as a closer the whole text went."""
    text = "Er sagte „Hallo.“ Danach ging er. "
    assert text[:len(text) - calculate_undo_length(text, "sentence", 1)] == "Er sagte „Hallo.“ "
