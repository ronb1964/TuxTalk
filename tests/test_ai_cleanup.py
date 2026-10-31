"""AI self-correction fixing: which sentences are sent, and how the answers go back.

The model itself is never trusted, so these tests use a fake engine that
returns chosen answers, good and bad, and check that only safe edits land
(the check itself: test_correction_safety).
"""
import pytest

from tuxtalk import ai_cleanup as ai
from tuxtalk import ai_download
from tuxtalk import live_settings as live


@pytest.mark.parametrize("sentence,expected", [
    ("Set it to 5, actually 10.", True),
    ("Call Dave, sorry, Steve.", True),
    ("No wait, stop.", True),
    ("Make that four boxes.", True),
    ("I actually like it.", False),
    ("Sorry I'm late.", False),
    ("The van is ready.", False),
])
def test_only_correction_sentences_reach_the_ai(sentence, expected):
    assert ai.has_correction(sentence) is expected


class FakeEngine:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def correct(self, sentence):
        self.asked.append(sentence)
        return self.answers.get(sentence)


def test_only_the_correction_sentence_is_sent_and_fixed():
    engine = FakeEngine({"Meet at 3, no wait, 4.": "Meet at 4."})
    text = "The van is ready. Meet at 3, no wait, 4. See you there. "
    assert ai.fix_self_corrections(text, engine) == "The van is ready. Meet at 4. See you there. "
    assert engine.asked == ["Meet at 3, no wait, 4."]


def test_a_correction_split_by_a_period_is_sent_with_the_sentence_before():
    """Speech-to-text often puts a period at the pause before "No, wait".
    It goes to the AI joined back into one sentence, the way it was meant:
    measured on the real model, split corrections were fixed 4 times in 10
    as they came and 8 in 10 joined, with nothing else changed."""
    engine = FakeEngine({"Order two boxes of screws, no wait, three boxes.":
                         "Order three boxes of screws."})
    text = "Hi George. Order two boxes of screws. No, wait, three boxes. Thanks. "
    assert ai.fix_self_corrections(text, engine) == "Hi George. Order three boxes of screws. Thanks. "
    assert engine.asked == ["Order two boxes of screws, no wait, three boxes."]


@pytest.mark.parametrize("said,asked", [
    ("Meet me at three. No wait four o'clock.", "Meet me at three, no wait, four o'clock."),
    ("Meet me at three. No wait. Four o'clock.", "Meet me at three, no wait, Four o'clock."),
    ("Call Mike at the office. I mean, call him at home.",
     "Call Mike at the office, I mean, call him at home."),
    ("Pick up the red paint. Make that the blue paint.", "Pick up the red paint, make that, the blue paint."),
    ("We need ten feet of wire. No wait. Twelve feet.", "We need ten feet of wire, no wait, Twelve feet."),
])
def test_the_split_is_joined_before_asking(said, asked):
    engine = FakeEngine({})
    ai.fix_self_corrections(said, engine)
    assert engine.asked == [asked]


def test_an_answer_that_keeps_the_correction_phrase_is_not_used():
    """The joined sentence with only its punctuation tidied is no fix, and
    would change what was said ("office. I mean" -> "office, I mean")."""
    said = "Call Mike at the office. I mean, call him at home."
    engine = FakeEngine({"Call Mike at the office, I mean, call him at home.":
                         "Call Mike at the office, I mean, call him at home!"})
    assert ai.fix_self_corrections(said, engine) == said


def test_an_opening_correction_with_nothing_before_it_is_sent_alone():
    engine = FakeEngine({})
    ai.fix_self_corrections("No wait, stop the order.", engine)
    assert engine.asked == ["No wait, stop the order."]


def test_an_unsafe_answer_leaves_the_sentence_as_spoken():
    engine = FakeEngine({"Set it to 5, actually 10.": "Set it to 5."})
    text = "Set it to 5, actually 10."
    assert ai.fix_self_corrections(text, engine) == text


def test_no_answer_leaves_the_text_alone():
    engine = FakeEngine({})
    text = "Order two, no wait, three boxes."
    assert ai.fix_self_corrections(text, engine) == text


def test_markers_are_never_sent_and_survive():
    engine = FakeEngine({"Order two, no wait, three boxes.": "Order three boxes."})
    text = "Hi.\xa7SHIFT_ENTER\xa7Order two, no wait, three boxes.\xa7SHIFT_ENTER\xa7Bye."
    assert ai.fix_self_corrections(text, engine) == \
        "Hi.\xa7SHIFT_ENTER\xa7Order three boxes.\xa7SHIFT_ENTER\xa7Bye."
    assert all("\xa7" not in s for s in engine.asked)


def test_text_without_corrections_never_touches_the_engine():
    engine = FakeEngine({})
    ai.fix_self_corrections("Just a normal sentence. Another one.", engine)
    assert engine.asked == []


def test_settings_start_and_stop_the_engine(monkeypatch):
    from tuxtalk.config import Settings

    events = []

    class Engine:
        def start(self): events.append("start")
        def stop(self): events.append("stop")

    monkeypatch.setattr(ai, "CorrectionEngine", Engine)
    monkeypatch.setattr(ai_download, "is_installed", lambda: True)
    monkeypatch.setattr(live, "ai_engine", None)

    live.apply(Settings(remove_fillers=True, ai_corrections=True))
    assert live.remove_fillers and live.ai_engine is not None
    live.apply(Settings(remove_fillers=True, ai_corrections=True))
    live.apply(Settings(remove_fillers=False, ai_corrections=False))
    assert events == ["start", "stop"] and live.ai_engine is None


# --- "Actually" with no comma -----------------------------------------------
# Parakeet often writes "Actually book it" with no comma. A sentence opening
# that way counts as a correction only when the next word repeats a real word
# of the sentence before (not "the", "it" or "I'll"). Measured on the real
# model: of 15 ordinary sentences that pass this gate, all 15 were left
# alone; 4 of 6 real corrections were fixed.

ACTUALLY_CORRECTIONS = [
    "Book the room for Monday. Actually book it for Tuesday.",
    "Send the report to Bob. Actually send it to Jim.",
    "Meet me at the shop. Actually meet me at the house.",
    "Call me at five. Actually call me at six.",
    "Order two sheets of plywood. Actually order three sheets.",
    "Paint it white. Actually paint it gray.",
]

# Never sent: the word after "Actually" repeats nothing real.
ACTUALLY_ORDINARY = [
    "The fridge is installed. Actually the fridge was the easy part.",
    "I finished the report. Actually it was easier than I thought.",
    "I'll call the plumber. Actually I'll text him instead.",
    "We're done for today. Actually we're ahead of schedule.",
    "I actually like the blue one.",
    "Actually book it for Tuesday.",
]


@pytest.mark.parametrize("text", ACTUALLY_CORRECTIONS)
def test_actually_without_a_comma_counts_when_it_restates(text):
    assert ai.has_correction(text)


@pytest.mark.parametrize("text", ACTUALLY_ORDINARY)
def test_actually_without_a_comma_is_otherwise_just_a_word(text):
    assert not ai.has_correction(text)


def test_a_restating_actually_is_joined_and_fixed():
    engine = FakeEngine({"Book the room for Monday, actually, book it for Tuesday.":
                         "Book the room for Tuesday."})
    text = "The van is ready. Book the room for Monday. Actually book it for Tuesday. Thanks. "
    assert ai.fix_self_corrections(text, engine) == \
        "The van is ready. Book the room for Tuesday. Thanks. "
    assert engine.asked == ["Book the room for Monday, actually, book it for Tuesday."]


def test_an_unfixed_actually_keeps_its_original_punctuation():
    """No comma is ever added to what was spoken; the AI's join is its own."""
    text = "Paint it white. Actually paint it gray. "
    assert ai.fix_self_corrections(text, FakeEngine({})) == text


@pytest.mark.parametrize("said,asked", [
    # Two correction phrases in a row.
    ("Meet me at three o'clock. No, actually, make that four o'clock.",
     "Meet me at three o'clock, no actually make that, four o'clock."),
    ("Meet me at three o'clock. Actually make that four o'clock.",
     "Meet me at three o'clock, actually make that, four o'clock."),
    ("Pick up the red paint. Sorry, I mean the blue paint.",
     "Pick up the red paint, sorry I mean, the blue paint."),
])
def test_stacked_correction_phrases_are_joined_too(said, asked):
    """On the real model these are fixed only once joined, while ordinary
    sentences opening with "No," "Sorry," or "Actually," stay as spoken."""
    engine = FakeEngine({})
    ai.fix_self_corrections(said, engine)
    assert engine.asked == [asked]


@pytest.mark.parametrize("sentence", [
    "I'll call you back. No problem at all.",      # "No" alone opens nothing
    "That works. Oh, and bring the drill.",
    # Joined, the real model dropped the "I mean it" that was meant.
    "Thanks for coming. Oh, I mean it, it really helped.",
])
def test_a_plain_no_or_oh_is_not_a_correction_opener(sentence):
    assert not ai._RE_OPENS_WITH_CORRECTION.match(sentence.split(". ", 1)[1])
