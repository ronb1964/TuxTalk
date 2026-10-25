"""Voice editing without its key: "edit that, make it shorter" right after a
dictation selects what TuxTalk typed and changes it (voice_edit.handle_spoken_edit,
voice_edit_rules.spoken_edit). Labs, with voice editing on."""
import time
import types

import numpy as np
import pytest

from tuxtalk import edit_notices
from tuxtalk import pipeline
from tuxtalk import type_at_pause
from tuxtalk import keyboard_output
from tuxtalk import recording_state as rs
from tuxtalk.voice_edit_rules import spoken_edit
from tuxtalk import live_settings as live
from tuxtalk import engine
from tuxtalk import notices
from tuxtalk import delivery
from tuxtalk import voice_edit

S = "SHIFT"


@pytest.mark.parametrize("raw, instruction", [
    ("Edit that, make it shorter.", "make it shorter."),
    ("edit that make it shorter", "make it shorter"),
    ("Edit that: more formal", "more formal"),
    ("Please edit that, fix the grammar.", "fix the grammar."),
    ("Edit that.", ""),
    ("edit that", ""),
    ("Edit that, file please", "file please"),        # a pause after it: a command
])
def test_a_dictation_starting_with_edit_that_is_an_instruction(raw, instruction):
    assert spoken_edit(raw) == instruction


@pytest.mark.parametrize("raw", [
    "Edit that file and send it.",     # no pause, and "file" isn't a change
    "We should edit that later.",      # not at the start
    "Edited that yesterday.",
    "Edit thatch roofs.",
    "Make it shorter.",
    "",
])
def test_anything_else_is_words(raw):
    assert spoken_edit(raw) is None


@pytest.fixture
def editing(monkeypatch):
    """Voice editing on, KWrite in front with a dictation just typed into it.
    Returns (pressed chords, edits asked for, problems shown)."""
    pressed, edits, problems = [], [], []

    class Backend:
        def press_keys(self, chords, claim=None):
            pressed.extend(chords)
            return True

    monkeypatch.setattr(live, "voice_editing", True)
    monkeypatch.setattr(keyboard_output, "is_wayland_session", lambda: True)
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "org.kde.kwrite")
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: Backend())
    monkeypatch.setattr(voice_edit, "edit_selection",
                        lambda get, beeps, mode, focused, t0, message, spoken=False:
                        edits.append((get(), focused, spoken)) or True)
    monkeypatch.setattr(notices, "edit_problem", lambda kind, beeps: problems.append(kind))
    monkeypatch.setattr(rs.state, "last_inserted_text", "We meet at three. ")
    monkeypatch.setattr(rs.state, "last_inserted_window", "org.kde.kwrite")
    monkeypatch.setattr(rs.state, "shown_text", None)
    monkeypatch.setattr(rs.state, "edit_waiting", None)
    monkeypatch.setattr(rs.state, "pending_boundary", None)
    monkeypatch.setattr(rs.state, "held_cue", "")
    monkeypatch.setattr(rs.state, "sent", None)
    return pressed, edits, problems



def _said(raw, chunk_role=None):
    """voice_edit.handle_spoken_edit for *raw*, with beeps, typing and this recording's message."""
    return voice_edit.handle_spoken_edit(raw, chunk_role, True, "type", rs.state.message)

def test_edit_that_selects_the_dictation_and_edits_it(editing):
    pressed, edits, problems = editing
    assert _said("Edit that, make it shorter.")
    # Over the trailing space, then select the sentence, exactly.
    assert pressed == [((), "LEFT")] + [((S,), "LEFT")] * len("We meet at three.")
    assert edits == [("make it shorter.", "org.kde.kwrite", True)]
    assert problems == []
    assert rs.state.last_inserted_text == ""        # undo can't count into the rewrite


def test_with_type_at_pause_it_edits_every_phrase_on_screen(editing):
    """Phrases since the last Enter are one message: "edit that" takes all of
    them, not only the last phrase."""
    pressed, edits, _ = editing
    rs.state.shown_serial = rs.state.recording_serial
    rs.state.shown_text = "Hi Sam. We meet at three. "
    assert _said("edit that make it friendlier", "middle")
    assert pressed.count(((S,), "LEFT")) == len("Hi Sam. We meet at three.")


def test_nothing_just_typed_means_the_words_are_typed(editing):
    _pressed, edits, _ = editing
    rs.state.last_inserted_text = ""
    assert not _said("Edit that, make it shorter.")
    assert edits == []


def test_a_dictation_in_another_window_isnt_edited(editing, monkeypatch):
    _pressed, edits, _ = editing
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "google-chrome")
    assert not _said("Edit that, make it shorter.")
    assert edits == []


def test_off_unless_voice_editing_is_on(editing, monkeypatch):
    _pressed, edits, _ = editing
    monkeypatch.setattr(live, "voice_editing", False)
    assert not _said("Edit that, make it shorter.")
    assert edits == []


def test_not_in_a_terminal(editing, monkeypatch):
    pressed, edits, problems = editing
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "org.kde.konsole")
    monkeypatch.setattr(rs.state, "last_inserted_window", "org.kde.konsole")
    assert _said("Edit that, make it shorter.")
    assert problems == ["terminal"] and pressed == [] and edits == []


def test_edit_that_then_a_pause_waits_for_the_next_phrase(editing):
    pressed, edits, _ = editing
    assert _said("Edit that.", "middle")
    assert pressed == [] and edits == []
    assert _said("Make it more formal.", "middle")
    assert edits == [("Make it more formal.", "org.kde.kwrite", True)]
    assert pressed.count(((S,), "LEFT")) == len("We meet at three.")


def test_a_switch_during_the_pause_after_edit_that_changes_nothing(editing, monkeypatch):
    """"edit that", a pause, a click into another app, then what to change:
    as many characters of the other app's text were selected and rewritten."""
    pressed, edits, problems = editing
    assert _said("Edit that.", "middle")
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "google-chrome")
    assert _said("Make it more formal.", "middle")       # not typed into Chrome either
    assert pressed == [] and edits == [] and problems == ["focus_moved"]


def test_the_wait_ends_with_the_recording(editing):
    _pressed, edits, _ = editing
    assert _said("Edit that.", "middle")
    rs.state.recording_serial += 1                  # a new recording
    assert not _said("Make it more formal.", "middle")
    assert edits == []


def test_edit_that_alone_at_the_end_asks_what_to_change(editing):
    _pressed, edits, problems = editing
    assert _said("Edit that.")
    assert problems == ["edit_that_what"] and edits == []


def test_it_is_never_typed_as_text(editing, monkeypatch):
    """Through the whole pipeline: the command phrase is acted on, not typed."""
    typed = []
    monkeypatch.setattr(engine, "transcribe", lambda audio, lang: "Edit that, make it shorter.")
    monkeypatch.setattr(delivery, "inject_text", lambda text, *a, **k: typed.append(text) or "ok")
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    monkeypatch.setattr(live, "voice_navigation", False)
    monkeypatch.setattr(live, "usage_stats", False)
    speech = (np.sin(np.linspace(0, 2000, 16000)) * 8000).astype(np.int16)
    pipeline.transcribe_and_inject([speech.tobytes()], 16000, True, True, False, None,
                               True, True, "type", chunk_role="middle")
    assert typed == []
    _pressed, edits, _ = editing
    assert edits and edits[0][0] == "make it shorter."


def test_edit_that_right_after_an_automatic_send_says_why(editing, monkeypatch):
    """Ron, 2026-10-18: the countdown sent the sentence (in KWrite, a new
    line), then "edit that, make it more formal" was typed as words."""
    _pressed, edits, problems = editing
    monkeypatch.setattr(rs.state, "last_inserted_text", "")
    monkeypatch.setattr(rs.state, "sent", {"at": time.time(), "automatic": True})
    assert _said("Edit that, make it more formal.")
    assert problems == ["already_sent_auto"] and edits == []


def test_edit_that_after_saying_press_enter_says_it_was_sent(editing, monkeypatch):
    _pressed, _edits, problems = editing
    monkeypatch.setattr(rs.state, "last_inserted_text", "")
    monkeypatch.setattr(rs.state, "sent", {"at": time.time(), "automatic": False})
    assert _said("Edit that, make it shorter.")
    assert problems == ["already_sent"]


def test_long_after_a_send_edit_that_is_words_again(editing, monkeypatch):
    _pressed, _edits, problems = editing
    monkeypatch.setattr(rs.state, "last_inserted_text", "")
    monkeypatch.setattr(rs.state, "sent", {"at": time.time() - 3600, "automatic": True})
    assert not _said("Edit that, make it shorter.")
    assert problems == []


def test_every_notice_has_a_short_label():
    assert set(edit_notices.NOTICES) == set(edit_notices.LABELS)


def test_pressing_enter_remembers_the_send(monkeypatch):
    class Backend:
        def press_keys(self, chords, claim=None):
            return True
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: Backend())
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(rs.state, "sent", None)
    keyboard_output.press_key("ENTER", "Send each dictation automatically is on")
    assert rs.state.sent["automatic"] is True
    keyboard_output.press_key("ENTER", 'said "press enter"')
    assert rs.state.sent["automatic"] is False


def test_an_edit_that_went_in_leaves_the_message_to_be_sent(editing):
    assert _said("Edit that, make it shorter.")
    assert rs.state.message.complete


@pytest.mark.parametrize("why", ["no rewrite", "not selected", "nothing asked"])
def test_an_edit_that_didnt_change_the_text_isnt_sent_as_it_was(editing, monkeypatch, why):
    """Type at pause with "Send each dictation automatically": "edit that,
    make it less angry", the AI had no answer, and the stop sent the angry
    original."""
    pressed, edits, problems = editing
    raw = "Edit that, make it less angry."
    if why == "no rewrite":
        monkeypatch.setattr(voice_edit, "edit_selection", lambda *a, **k: False)
    elif why == "not selected":
        monkeypatch.setattr(keyboard_output, "output_backend", lambda: types.SimpleNamespace(
            press_keys=lambda chords, claim=None: False))
    else:
        raw = "Edit that."
    assert _said(raw)
    assert not rs.state.message.complete


def test_edit_that_then_only_a_pause_isnt_sent_as_it_was(editing, monkeypatch):
    """Type at pause and "Send each dictation automatically": "edit that",
    a pause, then the recording ended with nothing said. The message went
    out unchanged."""
    pressed, edits, problems = editing
    sent = []
    monkeypatch.setattr(live, "press_enter_after", True)
    monkeypatch.setattr(pipeline, "send_automatically", lambda *a: sent.append(a))
    rs.state.message.add("ok", rs.state.message.to)       # the message's words
    assert _said("Edit that.", "middle")                    # waits for what to change
    type_at_pause.end_in_a_pause(rs.state.message, False, True, True, "type")
    assert sent == [] and problems == ["edit_that_what"]
    assert rs.state.edit_waiting is None
