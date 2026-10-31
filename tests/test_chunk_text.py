"""Type at pause's text at each pause (chunk_text.py): the mark the engine
put at the seam when it heard two phrases together, what it heard after the
first, and which final mark of a phrase is held back for the next to decide.
"""
import pytest

from tuxtalk.chunk_text import boundary_mark, hold_back_boundary, words_in_context


# --- where a sentence ends at a pause (the engine's word, in context) ---------

@pytest.mark.parametrize("prev,joint,mark", [
    ("Wir treffen uns morgen.", "Wir treffen uns morgen um drei Uhr.", ""),
    ("Das ist gut.", "Das ist gut. Wir gehen.", "."),
    ("Kommst du?", "Kommst du? Ich warte.", "?"),
    ("Wir treffen uns morgen", "Wir treffen uns morgen, um drei.", ","),
    ("Die Straße.", "die strasse ist nass", ""),          # ß, casefolded
    ("Hallo Welt.", "Etwas ganz anderes.", None),       # no match: no verdict
    ("Wir gehen.", "Wir gehen.", None),                  # nothing after it
])
def test_boundary_mark(prev, joint, mark):
    assert boundary_mark(prev, joint) == mark


@pytest.mark.parametrize("prev,joint,heard", [
    ("then let the timer run out.", "then let the timer run out it", "it"),
    ("Wir treffen uns morgen.", "Wir treffen uns morgen, um drei Uhr.", "um drei Uhr."),
    ("Hallo Welt.", "Etwas ganz anderes.", None),
    ("Wir gehen.", "Wir gehen.", None),
])
def test_words_in_context(prev, joint, heard):
    assert words_in_context(prev, joint) == heard


# --- the final mark held back for the next phrase -----------------------------

def test_a_dictated_period_is_not_held_back():
    """A voice command ("Punkt" -> ".") made the period: it is the speaker's own,
    not the engine's guess, so it is typed as it is."""
    text = "Bei jedem Unterbruch ein. "
    assert hold_back_boundary(text, "bei jedem Unterbruch ein Punkt.") == (text, None)


def test_the_engines_period_is_held_back():
    assert hold_back_boundary("Die Strasse ist nass. ", "die Straße ist nass.") == \
        ("Die Strasse ist nass", ".")


def test_a_line_break_is_not_held_back():
    text = "Dear John,\n"
    assert hold_back_boundary(text, "dear john new line") == (text, None)
