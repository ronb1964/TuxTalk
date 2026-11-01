"""Type at pause: a question said over pauses ends with a question mark.

Each phrase is heard alone, so in "don't you think it would be smart"
(pause) "items" (pause) "depending on who you're buying for" the last, a
statement on its own, got a period. When a sentence that ran over a pause
ends with a period, the engine hears the whole sentence once more, and only
its final mark is taken from that: a period may become a question mark,
nothing else changes.
"""
import pytest

from type_at_pause_rig import fake_pipeline, frames_of, speech, type_chunk
from tuxtalk import engine
from tuxtalk import recording_state as rs
from tuxtalk import type_at_pause


@pytest.fixture
def pipeline(monkeypatch):
    """The fake engine and typing: texts, what the engine 'hears' in call
    order (a phrase alone, then with the one before it, then, ending a
    sentence over pauses, the whole sentence). Returns (texts, typed)."""
    texts, typed, _ = fake_pipeline(monkeypatch)
    monkeypatch.setattr(engine, "is_parakeet", lambda: True)
    return texts, typed


DECOY = "Never asked, or this would be typed with a question mark?"


def test_a_question_said_over_two_pauses_ends_with_a_question_mark(pipeline):
    """A real dictation, cut at two pauses: heard whole it's a question."""
    texts, typed = pipeline
    texts.extend([
        "but don't you think it would be smart to pick multiple holiday",
        "items",
        "smart to pick multiple holiday items",                       # in context
        "depending on the people you are buying for.",
        "items depending on the people you are buying for",            # in context
        "But don't you think it would be smart to pick multiple holiday items "
        "depending on the people you are buying for?",                 # whole
    ])
    type_chunk(frames_of(speech(2.0)), "middle")
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(2.0)), "last", auto_period=True)
    assert texts == []
    assert typed[-1].rstrip() == "depending on the people you are buying for?"
    assert rs.state.current.message.sentence_audio == []


def test_a_statement_said_over_a_pause_keeps_its_period(pipeline):
    texts, typed = pipeline
    texts.extend(["we should change the settings", "so people can pick more.",
                  "we should change the settings so people can pick more",
                  "We should change the settings so people can pick more."])
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(1.0)), "last", auto_period=True)
    assert texts == []
    assert typed[-1].rstrip() == "so people can pick more."


def test_only_a_period_is_ever_changed(pipeline):
    """The whole pass decides the final mark and nothing else: its words,
    commas and capitals aren't typed."""
    texts, typed = pipeline
    texts.extend(["so tell me", "what's the plan!", "so tell me what's the plan",
                  "So, tell me. What's the plan?"])
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(1.0)), "last", auto_period=True)
    assert typed[-1].rstrip() == "what's the plan!"
    assert texts == ["So, tell me. What's the plan?"]      # never asked


def test_a_sentence_said_in_one_phrase_isnt_heard_twice(pipeline):
    texts, typed = pipeline
    texts.extend(["is it ready?", "yes it is.", "is it ready? Yes it is.", DECOY])
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(1.0)), "last", auto_period=True)
    assert texts == [DECOY]                # the question ended at the pause
    assert typed[-1].rstrip() == "Yes it is."


def test_a_question_that_ends_on_a_pause_gets_its_question_mark(pipeline):
    """The recording ended in a pause: the last phrase's held-back period is
    typed then, and it's decided from the whole sentence too."""
    texts, typed = pipeline
    texts.extend(["don't you think", "it would be smart.", "don't you think it would be smart",
                  "Don't you think it would be smart?"])
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(1.0)), "middle")
    type_at_pause.end_in_a_pause(rs.state.current.message, False, True, True, "type", None)
    assert texts == []
    assert typed[-1].rstrip() == "?"


def test_a_failed_whole_pass_keeps_the_period(pipeline, monkeypatch):
    """The engine failing on the whole sentence types the period it had."""
    texts, typed = pipeline
    texts.extend(["don't you think", "it would be smart.", "don't you think it would be smart"])
    calls = []

    def hear(audio, language):
        calls.append(len(audio))
        if not texts:
            raise RuntimeError("the engine stopped")
        return texts.pop(0)

    monkeypatch.setattr(engine, "transcribe", hear)
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(1.0)), "last", auto_period=True)
    assert len(calls) == 4
    assert typed[-1].rstrip() == "it would be smart."


def test_a_long_sentence_is_heard_whole_for_its_last_thirty_seconds(monkeypatch):
    """A run-on keeps only its newest WHOLE_SENTENCE_S, cut to the sample,
    one long phrase too: the end of the sentence decides its mark, and ten
    minutes of audio would be held for nothing."""
    monkeypatch.setattr(type_at_pause, "WHOLE_SENTENCE_S", 3.0)
    message = rs.Message()
    for seconds in (1.0, 2.5, 0.7):
        type_at_pause.keep_sentence_audio(message, speech(seconds).astype("float32"))
    assert sum(len(a) for a in message.sentence_audio) == 3 * engine.SAMPLE_RATE
    type_at_pause.keep_sentence_audio(message, speech(5.0).astype("float32"))
    assert [len(a) for a in message.sentence_audio] == [3 * engine.SAMPLE_RATE]


def test_esc_while_the_whole_sentence_is_heard_types_nothing(pipeline, monkeypatch):
    """Hearing the whole sentence takes a moment; Esc meanwhile stops the phrase."""
    texts, typed = pipeline
    texts.extend(["don't you think", "it would be smart.", "don't you think it would be smart"])

    def whole_pass(audio, language):
        if texts:
            return texts.pop(0)
        rs.state.current.message.cancelled = True       # Esc meanwhile
        return "Don't you think it would be smart?"

    monkeypatch.setattr(engine, "transcribe", whole_pass)
    monkeypatch.setattr(rs.state.current.message, "cancelled", False)
    type_chunk(frames_of(speech(1.0)), "middle")
    before = list(typed)
    type_chunk(frames_of(speech(1.0)), "last", auto_period=True)
    assert typed[len(before):] in ([], [" "])           # the held space at most, no words


def test_a_password_field_starts_the_sentence_afresh(pipeline, monkeypatch):
    """Phrases refused there mustn't join the sentence typed after it."""
    texts, typed = pipeline
    texts.extend(["don't you think"])
    type_chunk(frames_of(speech(1.0)), "middle")
    monkeypatch.setattr("tuxtalk.focused_window.is_password", lambda: True)
    monkeypatch.setattr("tuxtalk.notices.password_field_refused", lambda beeps: None)
    texts.extend(["my secret"])
    type_chunk(frames_of(speech(1.0)), "middle")
    assert rs.state.current.message.sentence_audio == []


def test_a_sentence_in_another_window_doesnt_hear_the_last_ones_phrases(pipeline, monkeypatch):
    """Without the held marks no boundary sees the switch; the kept phrases
    remember their window."""
    from tuxtalk import focused_window
    from tuxtalk import live_settings as live
    texts, typed = pipeline
    monkeypatch.setattr(live, "chunk_context_marks", False)
    focus = {"now": ("kwrite", None)}
    monkeypatch.setattr(focused_window, "look", lambda: focus["now"])
    monkeypatch.setattr(focused_window, "changed", lambda a, b: a != b)
    texts.extend(["don't you think"])
    rs.state.last_inserted_where = ("kwrite", None)
    type_chunk(frames_of(speech(1.0)), "middle")
    focus["now"] = ("brave-browser", None)
    texts.extend(["it would be smart.", DECOY])
    type_chunk(frames_of(speech(1.0)), "last", auto_period=True)
    assert texts == [DECOY] and typed[-1].rstrip().endswith("smart.")


def test_a_question_that_ended_inside_the_last_phrase_keeps_the_period(pipeline):
    """Heard whole, the sentence ends with the statement after it."""
    texts, typed = pipeline
    texts.extend(["don't you think", "it would be smart? I do.",
                  "don't you think it would be smart",
                  "Don't you think it would be smart? I do."])
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(3.0)), "last", auto_period=True)
    assert texts == [] and typed[-1].rstrip().endswith("I do.")


def test_switching_windows_starts_the_next_sentence_afresh(pipeline, monkeypatch):
    texts, typed = pipeline
    from tuxtalk import focused_window
    focus = {"now": "kwrite"}
    monkeypatch.setattr(focused_window, "look", lambda: (focus["now"], None))
    texts.extend(["don't you think", "it would be smart.", DECOY])
    type_chunk(frames_of(speech(1.0)), "middle")
    rs.state.current.message.held_mark["where"] = ("kwrite", None)
    focus["now"] = "brave-browser"
    type_chunk(frames_of(speech(1.0)), "last", auto_period=True)
    assert texts == [DECOY] and typed[-1].rstrip() == "It would be smart."


def test_the_whole_pass_hears_the_phrases_with_no_silence_put_between(pipeline, monkeypatch):
    """Half a second of silence between the phrases made Parakeet hear a
    sentence end there ("... items? Depending on ... buying for."): each
    phrase keeps a little of its pause already."""
    texts, typed = pipeline
    texts.extend(["don't you think", "it would be smart.", "don't you think it would be smart",
                  "Don't you think it would be smart?"])
    heard = []
    hear = engine.transcribe
    monkeypatch.setattr(engine, "transcribe", lambda audio, lang: heard.append(len(audio))
                        or hear(audio, lang))
    first, second = speech(1.0), speech(1.5)
    type_chunk(frames_of(first), "middle")
    type_chunk(frames_of(second), "last", auto_period=True)
    assert heard[-1] == len(first) + len(second)
    assert typed[-1].rstrip() == "it would be smart?"


def test_a_question_sent_with_press_enter_gets_its_question_mark(pipeline, monkeypatch):
    """The main chat case: heard whole, the sentence ends "... Press enter.";
    the key said after it isn't part of it."""
    from tuxtalk import pipeline as dictation_pipeline
    monkeypatch.setattr(dictation_pipeline, "press_said", lambda *a, **k: None)
    texts, typed = pipeline
    texts.extend(["don't you think", "it would be smart, press enter.",
                  "don't you think it would be smart",
                  "Don't you think it would be smart? Press enter."])
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(3.0)), "last", auto_period=True)
    assert texts == [] and typed[-1].rstrip() == "it would be smart?"


def test_undo_said_mid_sentence_forgets_the_words_it_took_away(pipeline, monkeypatch):
    from tuxtalk import voice_commands
    texts, typed = pipeline
    monkeypatch.setattr(voice_commands, "handle_undo", lambda raw, *a: raw == "undo that")
    texts.extend(["don't you think", "undo that"])
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(1.0)), "middle")
    assert rs.state.current.message.sentence_audio == []


def test_a_phrase_that_didnt_go_in_isnt_kept_for_the_sentence(pipeline, monkeypatch):
    from tuxtalk import delivery
    texts, typed = pipeline
    monkeypatch.setattr(delivery, "inject_text", lambda *a, **k: "failed")
    texts.extend(["don't you think"])
    type_chunk(frames_of(speech(1.0)), "middle")
    assert rs.state.current.message.sentence_audio == []


def test_with_whisper_the_period_stays_and_nothing_is_heard_again(pipeline, monkeypatch):
    """Only Parakeet's marks over joined phrases were measured; Whisper pads
    each pass to 30 s, which on the processor takes seconds."""
    texts, typed = pipeline
    monkeypatch.setattr(engine, "is_parakeet", lambda: False)
    texts.extend(["don't you think", "it would be smart.", "don't you think it would be smart",
                  DECOY])
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(1.0)), "last", auto_period=True)
    assert texts == [DECOY] and typed[-1].rstrip() == "it would be smart."


@pytest.mark.parametrize("text,raw,ours", [
    ("it would be smart.", "it would be smart.", True),
    ("we meet at 5 p.m.", "we meet at 5 p.m.", False),
    ("and then...", "and then...", False),
    ("is that it.", "is that it period", False),          # a period the speaker dictated
])
def test_only_a_period_the_engine_put_after_the_last_word_may_change(text, raw, ours):
    from tuxtalk.chunk_text import engine_period
    assert engine_period(text, raw) is ours


def test_starting_afresh_forgets_the_sentence(monkeypatch):
    """After Enter or Tab mid-recording the next words are a new message."""
    message = rs.Message()
    type_at_pause.keep_sentence_audio(message, speech(1.0).astype("float32"))
    type_at_pause.start_afresh(message)
    assert message.sentence_audio == []


def test_esc_during_the_last_whole_pass_sends_nothing(pipeline, monkeypatch):
    """"Send each dictation automatically" on, the recording ending in a
    pause: Esc while the engine hears the whole sentence mustn't send it."""
    from tuxtalk import delivery
    from tuxtalk import live_settings as live
    texts, typed = pipeline
    sent = []
    monkeypatch.setattr(live, "press_enter_after", True)
    monkeypatch.setattr(delivery, "send_automatically", lambda *a, **k: sent.append(1))
    monkeypatch.setattr(rs.state.current.message, "cancelled", False)
    texts.extend(["don't you think", "it would be smart.", "don't you think it would be smart"])
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(1.0)), "middle")

    def whole_pass(audio, language):
        rs.state.current.message.cancelled = True        # Esc meanwhile
        return "Don't you think it would be smart?"

    monkeypatch.setattr(engine, "transcribe", whole_pass)
    type_at_pause.end_in_a_pause(rs.state.current.message, False, True, True, "type", None)
    assert sent == []


def test_a_held_back_abbreviation_keeps_its_period(pipeline):
    """"we meet at 5 p.m." is held back as "p.m" and "."; heard whole as a
    question, it must not become "p.m?"."""
    texts, typed = pipeline
    texts.extend(["so do we meet", "at 5 p.m.", "so do we meet at 5 p.m", DECOY])
    type_chunk(frames_of(speech(1.0)), "middle")
    type_chunk(frames_of(speech(3.0)), "middle")
    type_at_pause.end_in_a_pause(rs.state.current.message, False, True, True, "type", None)
    assert texts == [DECOY] and "?" not in "".join(typed)
