"""Punctuation said at the start of a dictation (dictation_text): typed,
where the marks an engine leaves at the start of what it heard are
dropped, and what it goes in place of at the end of the text before
(sentence_fit.taken_by_mark)."""
import pytest

from tuxtalk import live_settings as live
from tuxtalk.config import Settings
from tuxtalk.dictation_text import prepare_text
from tuxtalk.sentence_fit import taken_by_mark


def typed(heard, lang="en"):
    live.apply(Settings(command_language=lang))
    return prepare_text(heard, True, True, False, ai=False, settings=live)


@pytest.mark.parametrize(("heard", "lang", "mark"), [
    ("Question mark.", "en", "?"), ("question mark", "en", "?"), ("Comma.", "en", ","),
    ("Colon.", "en", ":"), ("Semicolon.", "en", ";"), ("Period.", "en", "."),
    ("Exclamation point!", "en", "!"),
    ("Fragezeichen.", "de", "?"), ("Komma.", "de", ","), ("Doppelpunkt.", "de", ":"),
])
def test_a_mark_said_on_its_own_is_typed(heard, lang, mark, monkeypatch):
    """It was dropped as a stray mark at the start of the line, so nothing
    was typed; and a comma ending a dictation isn't a full stop here."""
    monkeypatch.setattr(live, "ai_engine", None)
    assert typed(heard, lang) == mark


def test_a_dictation_starting_with_a_said_comma_keeps_it(monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    assert typed("comma and then he left") == ", and then he left."


@pytest.mark.parametrize("heard", [". Hello there.", ", and then he left"])
def test_a_mark_the_engine_left_at_the_start_still_goes(heard, monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    assert typed(heard)[:1].isupper()


def test_a_comma_said_last_after_words_is_still_a_full_stop(monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    assert typed("Dear Anna comma.") == "Dear Anna."


@pytest.mark.parametrize(("before", "taken"), [
    ("Is it ready. ", ". "), ("Is it ready ", " "), ("Is it ready", ""),
    ("Is it ready? ", " "), ("I'll be there at 5. ", ". "), ("In the U.S. ", " "),
    ("and so on etc. ", " "), ("Ask Dr. ", " "), ("John F. ", " "), ("Wait... ", " "),
    ("Neither do I. ", ". "),
    ("Hello.\n", ""), ("Hello.\n  ", ""), ("", ""),
])
def test_what_a_said_mark_goes_in_place_of(before, taken):
    """The space after the last word, and its full stop: not an
    abbreviation's, an initial's or an ellipsis's, and nothing at the start
    of a line."""
    assert taken_by_mark(before) == taken


def test_comma_separated_values_stay_words(monkeypatch):
    """Said first, "comma" now goes on the end of the text before: the
    term must not be taken for the mark."""
    monkeypatch.setattr(live, "ai_engine", None)
    assert typed("comma separated values are easy") == "Comma separated values are easy."


def test_a_german_comma_said_first_is_kept(monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    assert typed("Komma und dann ging er", "de") == ", und dann ging er."
