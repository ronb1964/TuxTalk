"""Type at pause: what is said after the last pause, through the real
recording and pipeline with a fake engine and fake typing.

A word too short to cut a phrase of its own ("yes", 0.2 s) said after a
pause is still the dictation's last word: it must reach the engine, whether
the hotkey or hands-free ends the recording. A click too brief to be speech
is still left out, so the engine can't invent a word for it.
"""
import numpy as np
import pytest

# pipeline and recording are test_chunking's fixtures: pytest finds them by name.
from test_chunking import feed, noise, pipeline, recording, speech, stop  # noqa: F401
from tuxtalk import engine, silence
from tuxtalk import recording as capture
from tuxtalk import recording_state as rs
from tuxtalk.beeps import READY_BEEP

SR = 16000


@pytest.fixture
def heard(recording, monkeypatch):  # noqa: F811
    """The `recording` rig, with the length in seconds of each piece of audio
    the engine was given. Returns (texts, typed, beeps, lengths)."""
    texts, typed, beeps = recording
    lengths = []

    def transcribe(audio, lang):
        lengths.append(len(audio) / engine.SAMPLE_RATE)
        return texts.pop(0)

    monkeypatch.setattr(engine, "transcribe", transcribe)
    return texts, typed, beeps, lengths


def click():
    """A key click: loud for 10 ms, under the MIN_BURST_S a consonant lasts."""
    return speech(0.01)


def test_a_short_last_word_after_a_pause_is_typed(heard):
    texts, typed, beeps, lengths = heard
    texts.extend(["we meet", "yes", "we meet yes"])
    feed(np.concatenate([speech(1.0), noise(1.0), speech(0.2), noise(1.0)]))
    stop()
    assert rs.state.current.chunks_submitted == 1
    assert texts == []
    assert len(lengths) == 3                    # the phrase, the "yes", the two together
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


def test_a_cough_after_a_pause_cuts_no_phrase_but_is_heard(heard):
    """A cough is as loud and as long as a short word, so it can't be told
    from one here: the engine hears it (and types nothing for a cough). It
    never cuts a phrase of its own, though, however long the quiet after it."""
    texts, typed, _, lengths = heard
    texts.extend(["we meet", ""])
    feed(np.concatenate([speech(1.0), noise(1.0), speech(0.2), noise(3.0)]))
    stop()
    assert rs.state.current.chunks_submitted == 1
    assert texts == []
    assert len(lengths) == 2
    assert "".join(typed) == "We meet "


def test_a_click_after_a_pause_never_reaches_the_engine(heard):
    texts, typed, _, lengths = heard
    texts.extend(["we meet", "yeah"])
    feed(np.concatenate([speech(1.0), noise(1.0), click(), noise(1.0)]))
    stop()
    assert rs.state.current.chunks_submitted == 1
    assert texts == ["yeah"]
    assert len(lengths) == 1
    assert "".join(typed) == "We meet "


def test_esc_after_a_short_last_word_types_none_of_it(heard):
    texts, typed, _, _ = heard
    texts.extend(["we meet", "yes", "we meet yes"])
    feed(np.concatenate([speech(1.0), noise(1.0), speech(0.2), noise(0.5)]))
    capture.cancel_recording(rs.state.current, True, False, "Cancelled by ESC")
    assert texts == ["yes", "we meet yes"]
    assert "yes" not in "".join(typed)
