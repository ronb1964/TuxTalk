"""Type at pause: what is said after the last pause, through the real
recording and pipeline with a fake engine and fake typing (type_at_pause_rig).

A word too short to cut a phrase of its own ("yes", 0.2 s) said after a
pause is still the dictation's last word: it must reach the engine, whether
the hotkey or hands-free ends the recording, and however the stop falls
against the audio callback. A click too brief to hold a word is still left
out, so the engine can't invent one for it.
"""
import threading

import numpy as np
import pytest

from tuxtalk import engine, silence
from tuxtalk import recording as capture
from tuxtalk import recording_state as rs
from tuxtalk.beeps import READY_BEEP
from type_at_pause_rig import (SR, fake_pipeline, feed, feed_block, noise, speech,
                               start_chunked_recording, stop)


@pytest.fixture
def heard(monkeypatch):
    """A Type at pause recording under way, with the fake engine and typing,
    and the length in seconds of each piece of audio the engine was given.
    Returns (texts, typed, beeps, lengths)."""
    texts, typed, beeps = fake_pipeline(monkeypatch)
    lengths = []

    def transcribe(audio, lang):
        lengths.append(len(audio) / engine.SAMPLE_RATE)
        return texts.pop(0)

    monkeypatch.setattr(engine, "transcribe", transcribe)
    start_chunked_recording(monkeypatch)
    yield texts, typed, beeps, lengths
    rs.state.is_recording = False


def click():
    """A key click: loud for 10 ms, shorter than any word."""
    return speech(0.01)


def test_a_short_last_word_after_a_pause_is_typed(heard):
    texts, typed, beeps, lengths = heard
    texts.extend(["we meet", "yes", "we meet yes"])
    feed(np.concatenate([speech(1.0), noise(1.0), speech(0.2), noise(1.0)]))
    stop()
    assert rs.state.current.chunks_submitted == 1
    assert texts == []
    assert len(lengths) == 3                    # the phrase, the "yes", the two together
    assert lengths[1] >= 1.2                    # the whole rest, the word and the quiet
    assert "".join(typed) == "We meet yes "
    assert beeps == [READY_BEEP]


def test_a_short_last_word_is_typed_when_hands_free_stops(heard):
    texts, typed, _, _ = heard
    texts.extend(["we meet", "yes", "we meet yes"])
    rec = rs.state.current
    rec.silence = silence.SilenceDetector(2.0, SR)
    feed(np.concatenate([speech(1.0), noise(1.0), speech(0.2), noise(2.5)]))
    assert rec.went_quiet
    stop()
    assert texts == []
    assert "".join(typed) == "We meet yes "


def test_a_stop_while_the_last_word_arrives_still_types_it(heard, monkeypatch):
    """The key is let go while the audio callback takes in the word's block.
    The stop waits until the pause detector has heard that block: taking
    the audio before, it found no speech counted and threw the word away."""
    texts, typed, _, _ = heard
    texts.extend(["we meet", "yes", "we meet yes"])
    feed(np.concatenate([speech(1.0), noise(1.0)]))
    detector = rs.state.current.chunker
    hear = detector.feed
    stopper = threading.Thread(target=stop)

    def stop_meanwhile(block):
        stopper.start()
        stopper.join(0.5)           # it waits for this block, or it's done already
        return hear(block)

    monkeypatch.setattr(detector, "feed", stop_meanwhile)
    feed_block(speech(0.2))         # the whole word in one block
    stopper.join(5)
    assert not stopper.is_alive()
    assert "".join(typed) == "We meet yes "


def test_a_cough_after_a_pause_cuts_no_phrase(heard):
    """Where a cough goes, only: however long the quiet after it, it never
    cuts a phrase of its own. Whether its sound reaches the engine isn't
    pinned. Today it does, as it can't be told from a short word by level
    and length; a better filter may keep it out."""
    texts, typed, _, _ = heard
    texts.extend(["we meet", ""])
    feed(np.concatenate([speech(1.0), noise(1.0), speech(0.2), noise(3.0)]))
    stop()
    assert rs.state.current.chunks_submitted == 1
    assert "".join(typed) == "We meet "


@pytest.mark.parametrize("offset", [0.0, 0.015])
def test_a_click_after_a_pause_never_reaches_the_engine(heard, offset):
    """At 15 ms the click straddles two of the pause detector's 20 ms
    windows, which count it as a moment of speech; the rest is still left
    out, as it holds no word's worth of sound."""
    texts, typed, _, lengths = heard
    texts.extend(["we meet", "yeah"])
    feed(np.concatenate([speech(1.0), noise(1.0), noise(offset), click(), noise(1.0)]))
    stop()
    assert rs.state.current.chunks_submitted == 1
    assert texts == ["yeah"]
    assert len(lengths) == 1
    assert "".join(typed) == "We meet "


def test_esc_after_a_short_last_word_types_none_of_it(heard):
    """A guard, not this fix: Esc after a pause and a short word drops the
    rest, word included, as Esc drops anything not yet typed."""
    texts, typed, _, _ = heard
    texts.extend(["we meet", "yes", "we meet yes"])
    feed(np.concatenate([speech(1.0), noise(1.0), speech(0.2), noise(0.5)]))
    capture.cancel_recording(rs.state.current, True, False, "Cancelled by ESC")
    assert texts == ["yes", "we meet yes"]
    assert "yes" not in "".join(typed)
