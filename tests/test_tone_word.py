"""A tone said at the end of a dictation ("..., make that formal") rewrites
the rest before it's typed (TuxTalk Labs, with voice editing on). These pin
which endings count as a tone and what the AI's rewrite must keep."""
import pytest

from tuxtalk.voice_edit_rules import restyle_keeps_facts, tone_at_end


@pytest.mark.parametrize("raw, message, instruction", [
    ("Tell him the cabinets are late, make that formal.", "Tell him the cabinets are late",
     "make this more formal"),
    ("Tell him the cabinets are late. Make that formal.", "Tell him the cabinets are late.",
     "make this more formal"),
    ("see you thursday, make that more professional", "see you thursday",
     "make it sound more professional"),
    ("Thanks for the help, make that sound friendlier.", "Thanks for the help",
     "make it friendlier"),
    ("We start Monday, make that a bit more casual", "We start Monday", "make it more casual"),
    ("Send it back - make that polite", "Send it back", "make this more polite"),
    ("Thanks, MAKE THAT FORMAL!", "Thanks", "make this more formal"),
])
def test_a_tone_at_the_end_is_taken_off_and_names_the_change(raw, message, instruction):
    assert tone_at_end(raw) == (message, instruction)


@pytest.mark.parametrize("raw", [
    # Self-corrections stay corrections: "make that" with a value, not a tone.
    "Meet at three, make that four.",
    "Paint it blue, make that green.",
    # A sentence that says it, with no pause before the phrase.
    "Let's make that formal.",
    "We should make that more casual this year.",
    # Nothing to rewrite.
    "Make that formal.",
    ", make that formal",
    # Not at the end.
    "Make that formal, he said, and left.",
    # Not a tone on the list: shorter is a length, said as "edit that, make it shorter".
    "Thanks, make that spicy.",
    "Thanks for everything you did, make that shorter.",
    "",
])
def test_anything_else_is_typed_as_words(raw):
    assert tone_at_end(raw) is None


def test_a_rewrite_that_keeps_every_number_is_used():
    assert restyle_keeps_facts(
        "can you do thursday the 20th at 3:30, it's $1,500",
        "Would Thursday the 20th at 3:30 work for you? The cost is $1500.")


@pytest.mark.parametrize("after", [
    "Would Thursday work for you?",              # the date and time are gone
    "Would Thursday the 21st at 3:30 work? The cost is $1,500.",   # a changed date
    "",
    "   ",
])
def test_a_rewrite_that_loses_a_number_or_says_nothing_is_not(after):
    assert not restyle_keeps_facts("can you do thursday the 20th at 3:30, it's $1,500", after)


def test_a_rewrite_padded_with_made_up_details_is_not_used():
    before = "running late, there in ten"
    padded = ("Dear team, I hope this message finds you well. I wanted to let you know that I "
              "am running slightly behind schedule this morning due to unexpected traffic on "
              "the highway, and I expect to arrive in approximately ten minutes. Best regards.")
    assert not restyle_keeps_facts(before, padded)
    assert restyle_keeps_facts(before, "I am running late and will arrive in ten minutes.")


# --- in a dictation -------------------------------------------------------------------

class _FakeAI:
    """The local AI: ready, answering every edit with *answer*, keeping what it was asked."""

    def __init__(self, answer, ready=True):
        self.answer, self.ready, self.asked = answer, ready, []

    def is_ready(self):
        return self.ready

    def edit(self, text, instruction):
        self.asked.append((text, instruction))
        return self.answer


@pytest.fixture
def dictate(monkeypatch):
    """Voice editing on; dictate(raw, ai, chunk_role=None) runs one dictation
    (or Type at pause phrase) through pipeline.transcribe_and_inject and
    returns what was typed, pressed and shown, in order."""
    import numpy as np

    from tuxtalk import (delivery, dictation_text, engine, focused_window, keyboard_output,
                         notices, pipeline, voice_commands, voice_edit)
    from tuxtalk import live_settings as live
    from tuxtalk import recording_state as rs

    events = []
    monkeypatch.setattr(live, "voice_editing", True)
    monkeypatch.setattr(live, "press_enter_after", False)
    monkeypatch.setattr(live, "swiss_spelling", False)
    monkeypatch.setattr(dictation_text, "prepare_text", lambda r, *a, **k: r + " ")
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    for handler in ("handle_undo", "handle_navigation", "handle_fix_word"):
        monkeypatch.setattr(voice_commands, handler, lambda *a, **k: False)
    monkeypatch.setattr(focused_window, "window_class", lambda: "brave-browser")
    monkeypatch.setattr(focused_window, "look", lambda: ("brave-browser", None))
    monkeypatch.setattr(notices, "edit_problem", lambda kind, beeps: events.append(kind))
    monkeypatch.setattr(delivery, "inject_text",
                        lambda text, mode, message=None: events.append(("typed", text)) or "ok")
    monkeypatch.setattr(keyboard_output, "press_key",
                        lambda key, why, claim=None: events.append(("pressed", key)))
    monkeypatch.setattr(voice_edit, "edit_last_dictation",
                        lambda instruction, *a: events.append(("edit on screen", instruction)))

    def run(raw, ai, chunk_role=None):
        monkeypatch.setattr(live, "ai_engine", ai)
        monkeypatch.setattr(engine, "transcribe", lambda audio, language: raw)
        frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
        pipeline.transcribe_and_inject(frames, engine.SAMPLE_RATE, beeps_on=False,
                                       smart_quotes=False, notify_on=False,
                                       chunk_role=chunk_role, message=rs.state.current.message)
        return events
    return run


def test_the_message_is_typed_already_rewritten_in_the_tone(dictate):
    ai = _FakeAI("Please let him know the cabinets will arrive a week late.")
    events = dictate("tell him the cabinets are a week late, make that formal", ai)
    assert ai.asked == [("tell him the cabinets are a week late", "make this more formal")]
    assert events == [("typed", "Please let him know the cabinets will arrive a week late. ")]


def test_a_rewrite_is_never_sent_automatically(dictate, monkeypatch):
    """The AI can change what a sentence means ("under a van" made formal
    came out "under the influence"): the user reads it before it goes."""
    from tuxtalk import live_settings as live
    monkeypatch.setattr(live, "press_enter_after", True)
    events = dictate("see you thursday, make that friendlier",
                     _FakeAI("Looking forward to seeing you Thursday!"))
    assert events == [("typed", "Looking forward to seeing you Thursday! ")]


def test_a_key_said_after_the_tone_is_pressed_after_the_rewrite(dictate):
    events = dictate("see you thursday, make that friendlier, press enter",
                     _FakeAI("Looking forward to seeing you Thursday!"))
    assert events == [("typed", "Looking forward to seeing you Thursday!"), ("pressed", "ENTER")]


@pytest.mark.parametrize("ai", [
    None,                                               # the AI is off
    _FakeAI("Anything.", ready=False),                  # still starting
    _FakeAI(None),                                      # no answer
    _FakeAI("We can install it next week."),            # the date is gone
])
def test_when_the_ai_cant_the_words_are_typed_as_spoken_and_the_user_told(dictate, ai):
    events = dictate("can we install it on the 20th, make that formal", ai)
    assert events == ["tone_not_changed", ("typed", "can we install it on the 20th ")]


def test_with_voice_editing_off_the_phrase_is_words(dictate, monkeypatch):
    from tuxtalk import live_settings as live
    monkeypatch.setattr(live, "voice_editing", False)
    ai = _FakeAI("Should not be asked.")
    events = dictate("see you there, make that formal", ai)
    assert ai.asked == []
    assert events == [("typed", "see you there, make that formal ")]


def test_a_self_correction_is_not_a_tone(dictate):
    ai = _FakeAI("Should not be asked.")
    events = dictate("meet at three, make that four", ai)
    assert ai.asked == []
    assert events == [("typed", "meet at three, make that four ")]


def test_type_at_pause_changes_the_recording_on_screen_and_doesnt_send_it(dictate, monkeypatch):
    """The earlier phrases are on screen already: the last one goes in, then
    all of it is changed as "edit that" would, and the automatic Enter waits."""
    from tuxtalk import live_settings as live
    monkeypatch.setattr(live, "press_enter_after", True)
    ai = _FakeAI("Should not be asked here.")
    events = dictate("and the paint is ready, make that formal", ai, chunk_role="last")
    assert ai.asked == []
    assert events == [("typed", "and the paint is ready "),
                      ("edit on screen", "make this more formal")]


def test_mid_recording_the_phrase_is_words(dictate):
    events = dictate("we can do it then, make that formal", _FakeAI("x"), chunk_role="middle")
    assert [(what, text.strip()) for what, text in events] == [
        ("typed", "we can do it then, make that formal")]


# --- the release reviews' cases (2026-11-25) -------------------------------------------

@pytest.mark.parametrize("before, after", [
    ("Pay $1.50.", "Please pay $50.1."),                  # a price's decimals moved
    ("Set it to -5.", "Please set it to 5."),             # its sign dropped
    ("Order 2 red and 2 blue.", "Please order 2 red."),   # one of two 2s dropped
    ("Arrive in ten minutes.", "Please arrive soon."),    # a number said as a word
    ("Pay 1,50 euros.", "Please pay 50,1 euros."),        # a decimal comma's parts swapped
    ("I dont think we should use it.", "We should use it."),   # the "not" dropped
])
def test_a_rewrite_that_changes_a_number_or_the_meaning_is_not_used(before, after):
    assert not restyle_keeps_facts(before, after)


@pytest.mark.parametrize("before, after", [
    ("We cant do it for less than 2400 dollars.", "We cannot do it for less than $2,400."),
    ("I dont think we should use it.", "I do not think we should use it."),
    ("Meet at 3:30.", "Could we meet at 3:30 PM?"),
])
def test_a_fair_rewrite_is_still_used(before, after):
    assert restyle_keeps_facts(before, after)


@pytest.mark.parametrize("raw", [
    "The dress code is casual, no wait, make that formal.",
    "Set the heat to low, make that warmer.",
    "Keep it relaxed, make that relaxed.",
])
def test_a_correction_or_a_value_is_not_read_as_a_tone(raw):
    """"casual, no wait, make that formal" corrects "casual"; rewritten
    formally it would say the opposite. "warmer" and "relaxed" are values."""
    assert tone_at_end(raw) is None


def test_the_ai_sees_real_line_breaks_and_a_marker_in_its_answer_is_refused(dictate):
    from tuxtalk.normalize import LINE_BREAK
    ai = _FakeAI("Hello Anna,\nThank you for the quote.")
    # The text as shaping leaves it (this fixture's prepare_text passes it on).
    events = dictate(f"hello anna,{LINE_BREAK}thanks for the quote, make that formal", ai)
    assert ai.asked[0][0] == "hello anna,\nthanks for the quote"
    assert events == [("typed", f"Hello Anna,{LINE_BREAK}Thank you for the quote. ")]
    events.clear()
    events = dictate("see you there, make that formal", _FakeAI(f"See you{LINE_BREAK}there."))
    assert events[0] == "tone_not_changed"


def test_a_section_sign_in_the_rewrite_is_used(dictate):
    """Only TuxTalk's own line-break marker is refused, not a \u00a7 in the
    text (release re-check, Codex)."""
    events = dictate("refer to \u00a75, make that formal", _FakeAI("Please refer to \u00a75."))
    assert events == [("typed", "Please refer to \u00a75. ")]


def test_the_words_as_said_are_kept_in_recent_dictations(dictate, monkeypatch):
    from tuxtalk import history
    kept = []
    monkeypatch.setattr(history, "add_entry", lambda text, replaces=None: kept.append(text))
    dictate("tell him the van is ready, make that formal", _FakeAI("Please inform him the van is ready."))
    assert kept[0] == "tell him the van is ready"


@pytest.mark.parametrize("answer, kept", [
    ("Please pay promptly.", False),                # the 1500 is gone
    ("Kindly remit $1500.", True),
])
def test_a_tone_on_text_already_typed_gets_the_same_check(answer, kept, monkeypatch):
    """Type at pause and a tone said alone change the text on screen through
    voice editing's own path, which once took any answer."""
    from tuxtalk import live_settings as live
    from tuxtalk import notices, voice_edit
    problems = []
    monkeypatch.setattr(live, "ai_engine", _FakeAI(answer))
    monkeypatch.setattr(live, "swiss_spelling", False)
    monkeypatch.setattr(notices, "edit_problem", lambda kind, beeps: problems.append(kind))
    result = voice_edit._rewritten("Pay $1500.", "make this more formal", False)
    assert (result == answer) is kept
    assert problems == ([] if kept else ["tone_unsafe"])
