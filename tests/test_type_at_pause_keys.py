"""Spoken keys with Type at pause: "press enter" and "press tab" said at the
end of a phrase or after a pause, and "Send each dictation automatically",
through the real pipeline with a fake engine and fake typing
(type_at_pause_rig.py)."""

import pytest

from tuxtalk import keyboard_output
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs
from tuxtalk.beeps import READY_BEEP
from type_at_pause_rig import fake_pipeline, frames_of, speech, type_chunk


@pytest.fixture
def pipeline(monkeypatch):
    """The fake engine and typing (type_at_pause_rig.fake_pipeline): texts,
    what the engine 'hears' in call order. Returns (texts, typed, beeps)."""
    return fake_pipeline(monkeypatch)


@pytest.fixture
def enter(pipeline, monkeypatch):
    """The pipeline, with Enter presses recorded among the typing as "<ENTER>"."""
    texts, typed, beeps = pipeline

    def fake_enter(key, why, claim=None):
        typed.append(f"<{key}>")
        rs.state.last_inserted_text = ""
        return True
    monkeypatch.setattr(keyboard_output, "press_key", fake_enter)
    monkeypatch.setattr(live, "press_enter_after", False)
    monkeypatch.setattr(rs.state.current.message, "held_cue", "")
    return texts, typed, beeps


def test_press_enter_at_the_end_of_a_phrase_sends_it(enter):
    """Hands-free: "see you at noon, press enter", then a pause. The sentence
    is finished (no mark held back for later) and sent at once, and what's
    said next starts a new message."""
    texts, typed, _ = enter
    texts.extend(["see you at noon, press enter", "how are you"])
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["See you at noon.", "<ENTER>"]
    assert rs.state.current.message.held_mark is None and not rs.state.current.message.has_words
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed[2:] == ["How are you"]


def test_an_enter_that_couldnt_be_pressed_leaves_the_message_open(enter, monkeypatch):
    """A Right Ctrl stuck down stopped the Enter: the phrase is still in the
    box, so "edit that" must still reach all of it, not start afresh."""
    texts, typed, _ = enter
    monkeypatch.setattr(keyboard_output, "press_key", lambda key, why, claim=None: False)
    texts.extend(["see you at noon, press enter", "press enter"])
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["See you at noon."]
    assert rs.state.current.message.shown == "See you at noon."
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert rs.state.current.message.shown == "See you at noon."


def test_press_enter_after_a_pause_ends_the_phrase_before_it(enter):
    """ "we meet at three" ... pause ... "press enter": the held-back end of
    the phrase is typed (the auto-period, no space) before Enter, with no
    context pass deciding a mark against the words "press enter"."""
    texts, typed, beeps = enter
    texts.extend(["we meet at three", "press enter"])
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["We meet at three", ".", "<ENTER>"]
    assert texts == []                       # transcribed once each, no joint pass
    assert READY_BEEP not in beeps       # mid-recording: no beep
    assert rs.state.current.message.held_mark is None


def test_press_tab_at_the_end_of_a_phrase_leaves_the_box_without_a_mark(enter):
    """Hands-free in a form: "john, press tab", a pause, "smith": no period
    or space in the first box, and the next one starts afresh."""
    texts, typed, _ = enter
    texts.extend(["john, press tab", "smith"])
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["John", "<TAB>"]
    assert rs.state.current.message.held_mark is None and not rs.state.current.message.has_words
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed[2:] == ["Smith"]


def test_a_box_left_with_press_tab_isnt_part_of_the_next_sentence(enter):
    """"Smith" in the next box mustn't be heard with "John" for its final mark."""
    texts, typed, _ = enter
    texts.extend(["john, press tab"])
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert rs.state.current.message.sentence_audio == []


def test_press_tab_after_a_pause_types_no_mark_before_it(enter):
    texts, typed, _ = enter
    texts.extend(["john", "press tab"])
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["John", "<TAB>"]
    assert texts == []                       # no context pass against "press tab"
    assert rs.state.current.message.held_mark is None


def test_press_enter_after_each_dictation_waits_for_the_end(enter, monkeypatch):
    texts, typed, _ = enter
    monkeypatch.setattr(live, "press_enter_after", True)
    texts.extend(["we meet", "at three", "we meet at three"])
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert "<ENTER>" not in typed
    type_chunk(frames_of(speech(1.0)), "last", auto_period=True)
    assert typed == ["We meet", " ", "at three.", "<ENTER>"]


def test_press_enter_after_each_dictation_when_it_ends_in_a_pause(enter, monkeypatch):
    texts, typed, _ = enter
    monkeypatch.setattr(live, "press_enter_after", True)
    texts.append("we meet")
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    type_chunk([], "last", auto_period=True)
    assert typed == ["We meet", ".", "<ENTER>"]


def test_a_press_enter_misheard_alone_is_caught_by_the_context_pass(enter):
    """Alone, the short phrase came out "Press center.", heard
    with the end of the one before it was "... to say. Press enter."."""
    texts, typed, _ = enter
    texts.extend(["we meet at three", "Press center.", "We meet at three. Press enter."])
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["We meet at three", ".", "<ENTER>"]


def test_words_the_context_pass_doesnt_hear_as_press_enter_stay_words(enter):
    texts, typed, _ = enter
    texts.extend(["we meet at three", "press center.", "We meet at three. Press center."])
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert "<ENTER>" not in typed and typed[-1] == "Press center"


def test_a_longer_phrase_is_never_turned_into_press_enter(enter):
    texts, typed, _ = enter
    texts.extend(["we meet at three", "then go to the press center",
                  "We meet at three. Then go to the press enter."])
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    type_chunk(frames_of(speech(1.0)), "middle", auto_period=True)
    assert "<ENTER>" not in typed
