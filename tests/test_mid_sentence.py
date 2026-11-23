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

from tuxtalk import dictation_text, keyboard_output, pipeline
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs


def _prep(text, continues=True):
    return dictation_text.prepare_text(text, False, True, False, settings=live,
                                       continues=continues)


def test_the_pronoun_i_is_not_lowercased():
    assert _prep("I think so") == "I think so."


def test_i_contractions_are_not_lowercased():
    assert _prep("I'm going now").startswith("I'm")


def test_an_acronym_is_not_mangled():
    assert _prep("NASA launched it") == "NASA launched it."


def test_an_ordinary_word_is_still_lowercased():
    """The actual purpose of the feature must still work."""
    assert _prep("The cat sat") == "the cat sat."


def test_normal_dictation_is_untouched():
    assert _prep("The cat sat", continues=False) == "The cat sat."


@pytest.mark.parametrize("elsewhere, carries_on", [(False, True), (True, False)])
def test_a_sentence_carries_on_only_in_the_window_it_was_left_in(
        monkeypatch, elsewhere, carries_on):
    """A message after "meet me at noon and" in KWrite mustn't begin with a
    small letter."""
    monkeypatch.setattr(rs.state, "continue_mid_sentence", True)
    monkeypatch.setattr(keyboard_output, "not_where_typed", lambda focused: elsewhere)
    monkeypatch.setattr(pipeline.focused_window, "window_class", lambda: "kwrite")
    assert pipeline._continues_sentence() is carries_on


def test_words_typed_end_the_sentence_left_unfinished(monkeypatch):
    """Carried on once: the dictation after it starts its own sentence."""
    monkeypatch.setattr(rs.state, "continue_mid_sentence", True)
    monkeypatch.setattr(pipeline, "_continues_sentence", lambda: True)
    job = pipeline._Job(False, False, False, None, False, True, "type", None, rs.Message())
    assert pipeline._prepared("The cat sat", job, True, "", ai=False).startswith("the cat")
    assert rs.state.continue_mid_sentence is False
