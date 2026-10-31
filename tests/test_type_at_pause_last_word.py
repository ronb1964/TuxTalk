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


def test_a_soft_voice_in_hands_free_keeps_its_phrases(heard):
    """A voice too soft for hands-free (it needs int16 RMS 300) that Type at
    pause hears: at 10 s hands-free decided nothing was said and cancelled
    the recording while the user talked on, dropping the phrase in the
    works and all since the last cut, with "Nothing heard". It stops instead
    and types the rest, none of it trimmed as quiet."""
    texts, typed, beeps, lengths = heard
    texts.extend(["hello"] * 9)              # each phrase, then each with the one before
    rec = rs.state.current
    rec.silence = silence.SilenceDetector(2.0, SR)
    phrase = np.concatenate([speech(1.5, level=300.0, noise_level=4.0), noise(1.0, level=4.0)])
    audio = np.tile(phrase, 6)[int(0.7 * SR):]      # at 10 s, mid-phrase
    for i in range(0, len(audio), 320):
        feed_block(audio[i:i + 320])
        if rec.went_quiet:
            break
    assert rec.silence.nothing_said and rec.chunks_submitted == 4
    rest = sum(len(f) for f in rec.frames) // 2
    stop()
    assert not rec.message.cancelled
    assert "".join(typed).lower().count("hello") == 5      # four phrases and the rest
    assert beeps == [READY_BEEP]
    assert lengths[-2] * SR >= rest - 1     # the rest alone, none of it trimmed


def test_hands_free_judges_a_short_last_word_against_the_whole_rest(heard, monkeypatch):
    """The quiet hands-free waited through is the room the word stands out
    from. Judged after that quiet was trimmed, in a noisy room the room
    looked louder and real short words were thrown away."""
    texts, typed, _, _ = heard
    texts.extend(["we meet", "yes", "we meet yes"])
    judged = []

    def has_a_word(audio, rate, ignoring_hz=None):
        judged.append(len(audio) / rate)
        return real_has_a_word(audio, rate, ignoring_hz)

    real_has_a_word = silence.has_a_word
    monkeypatch.setattr(silence, "has_a_word", has_a_word)
    rec = rs.state.current
    rec.silence = silence.SilenceDetector(2.0, SR)
    feed(np.concatenate([speech(1.0), noise(1.0), speech(0.2), noise(2.5)]))
    stop()
    assert judged and judged[0] >= 2.4          # the word and all the quiet after it
    assert "".join(typed) == "We meet yes "


def test_a_word_cut_short_by_the_key_still_reaches_the_engine(heard):
    """Let go while still talking: the rest goes to the engine whatever it
    holds, as it always did, even a word's first 30 ms."""
    texts, typed, _, lengths = heard
    texts.extend(["we meet", "we", "we meet we"])
    feed(np.concatenate([speech(1.0), noise(1.0), noise(0.315), speech(0.03)]))
    stop()
    assert texts == []
    assert len(lengths) == 3
    assert "".join(typed) == "We meet we "


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
