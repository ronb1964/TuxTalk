"""Continuing a sentence after "undo that" lowercases the first letter.

Whisper capitalizes the first word of every utterance, so when the user is
resuming mid-sentence that capital is wrong and gets undone. But some words
are capitalized for reasons that have nothing to do with sentence position,
and lowercasing those corrupts them: the pronoun "I" would become "i", and the
acronym "NASA" "nASA".

Proper nouns ("Paris") are genuinely ambiguous — Whisper capitalizes them both
as sentence starts and as names — so those are left as-is by design.
"""

import pytest

from tuxtalk import dictation_text
from tuxtalk import recording_state as rs


@pytest.fixture
def continuing():
    rs.state.continue_mid_sentence = True
    yield
    rs.state.continue_mid_sentence = False


def _prep(text):
    return dictation_text.prepare_text(text, False, True, False)


def test_the_pronoun_i_is_not_lowercased(continuing):
    assert _prep("I think so") == "I think so."


def test_i_contractions_are_not_lowercased(continuing):
    rs.state.continue_mid_sentence = True
    assert _prep("I'm going now").startswith("I'm")


def test_an_acronym_is_not_mangled(continuing):
    assert _prep("NASA launched it") == "NASA launched it."


def test_an_ordinary_word_is_still_lowercased(continuing):
    """The actual purpose of the feature must still work."""
    assert _prep("The cat sat") == "the cat sat."


def test_the_flag_is_cleared_after_use(continuing):
    _prep("The cat sat")
    assert rs.state.continue_mid_sentence is False


def test_normal_dictation_is_untouched():
    rs.state.continue_mid_sentence = False
    assert _prep("The cat sat") == "The cat sat."
