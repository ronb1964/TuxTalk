"""Voice editing without its key: "edit that, make it shorter" right after a
dictation selects what TuxTalk typed and changes it (app._handle_spoken_edit,
voice_edit_rules.spoken_edit). Labs, with voice editing on."""
import numpy as np
import pytest

from tuxtalk import app, edit_notices
from tuxtalk.voice_edit_rules import spoken_edit

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

    monkeypatch.setattr(app, "_voice_editing", True)
    monkeypatch.setattr(app, "_is_wayland_session", lambda: True)
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "org.kde.kwrite")
    monkeypatch.setattr(app, "_output_backend", lambda: Backend())
    monkeypatch.setattr(app, "_edit_selection",
                        lambda get, beeps, mode, focused, t0, message, spoken=False:
                        edits.append((get(), focused, spoken)))
    monkeypatch.setattr(app, "_edit_problem", lambda kind, beeps: problems.append(kind))
    monkeypatch.setattr(app.state, "last_inserted_text", "We meet at three. ")
    monkeypatch.setattr(app.state, "last_inserted_window", "org.kde.kwrite")
    monkeypatch.setattr(app.state, "shown_text", None)
    monkeypatch.setattr(app.state, "edit_waiting", None)
    monkeypatch.setattr(app.state, "pending_boundary", None)
    monkeypatch.setattr(app.state, "held_cue", "")
    monkeypatch.setattr(app.state, "sent", None)
    return pressed, edits, problems


def test_edit_that_selects_the_dictation_and_edits_it(editing):
    pressed, edits, problems = editing
    assert app._handle_spoken_edit("Edit that, make it shorter.", None, True, "type", app.state.message)
    # Over the trailing space, then select the sentence, exactly.
    assert pressed == [((), "LEFT")] + [((S,), "LEFT")] * len("We meet at three.")
    assert edits == [("make it shorter.", "org.kde.kwrite", True)]
    assert problems == []
    assert app.state.last_inserted_text == ""        # undo can't count into the rewrite


def test_with_type_at_pause_it_edits_every_phrase_on_screen(editing):
    """Phrases since the last Enter are one message: "edit that" takes all of
    them, not only the last phrase."""
    pressed, edits, _ = editing
    app.state.shown_serial = app.state.recording_serial
    app.state.shown_text = "Hi Sam. We meet at three. "
    assert app._handle_spoken_edit("edit that make it friendlier", "middle", True, "type", app.state.message)
    assert pressed.count(((S,), "LEFT")) == len("Hi Sam. We meet at three.")


def test_nothing_just_typed_means_the_words_are_typed(editing):
    _pressed, edits, _ = editing
    app.state.last_inserted_text = ""
    assert not app._handle_spoken_edit("Edit that, make it shorter.", None, True, "type", app.state.message)
    assert edits == []


def test_a_dictation_in_another_window_isnt_edited(editing, monkeypatch):
    _pressed, edits, _ = editing
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "google-chrome")
    assert not app._handle_spoken_edit("Edit that, make it shorter.", None, True, "type", app.state.message)
    assert edits == []


def test_off_unless_voice_editing_is_on(editing, monkeypatch):
    _pressed, edits, _ = editing
    monkeypatch.setattr(app, "_voice_editing", False)
    assert not app._handle_spoken_edit("Edit that, make it shorter.", None, True, "type", app.state.message)
    assert edits == []


def test_not_in_a_terminal(editing, monkeypatch):
    pressed, edits, problems = editing
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "org.kde.konsole")
    monkeypatch.setattr(app.state, "last_inserted_window", "org.kde.konsole")
    assert app._handle_spoken_edit("Edit that, make it shorter.", None, True, "type", app.state.message)
    assert problems == ["terminal"] and pressed == [] and edits == []


def test_edit_that_then_a_pause_waits_for_the_next_phrase(editing):
    pressed, edits, _ = editing
    assert app._handle_spoken_edit("Edit that.", "middle", True, "type", app.state.message)
    assert pressed == [] and edits == []
    assert app._handle_spoken_edit("Make it more formal.", "middle", True, "type", app.state.message)
    assert edits == [("Make it more formal.", "org.kde.kwrite", True)]
    assert pressed.count(((S,), "LEFT")) == len("We meet at three.")


def test_the_wait_ends_with_the_recording(editing):
    _pressed, edits, _ = editing
    assert app._handle_spoken_edit("Edit that.", "middle", True, "type", app.state.message)
    app.state.recording_serial += 1                  # a new recording
    assert not app._handle_spoken_edit("Make it more formal.", "middle", True, "type", app.state.message)
    assert edits == []


def test_edit_that_alone_at_the_end_asks_what_to_change(editing):
    _pressed, edits, problems = editing
    assert app._handle_spoken_edit("Edit that.", None, True, "type", app.state.message)
    assert problems == ["edit_that_what"] and edits == []


def test_it_is_never_typed_as_text(editing, monkeypatch):
    """Through the whole pipeline: the command phrase is acted on, not typed."""
    typed = []
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Edit that, make it shorter.")
    monkeypatch.setattr(app, "_inject_text", lambda text, *a, **k: typed.append(text) or "ok")
    monkeypatch.setattr(app, "_beep", lambda *a: None)
    monkeypatch.setattr(app, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(app, "_voice_navigation", False)
    monkeypatch.setattr(app, "_usage_stats", False)
    speech = (np.sin(np.linspace(0, 2000, 16000)) * 8000).astype(np.int16)
    app._transcribe_and_inject([speech.tobytes()], 16000, True, True, False, None,
                               True, True, "type", chunk_role="middle")
    assert typed == []
    _pressed, edits, _ = editing
    assert edits and edits[0][0] == "make it shorter."


def test_edit_that_right_after_an_automatic_send_says_why(editing, monkeypatch):
    """Ron, 2026-10-18: the countdown sent the sentence (in KWrite, a new
    line), then "edit that, make it more formal" was typed as words."""
    _pressed, edits, problems = editing
    monkeypatch.setattr(app.state, "last_inserted_text", "")
    monkeypatch.setattr(app.state, "sent", {"at": app.time.time(), "automatic": True})
    assert app._handle_spoken_edit("Edit that, make it more formal.", None, True, "type", app.state.message)
    assert problems == ["already_sent_auto"] and edits == []


def test_edit_that_after_saying_press_enter_says_it_was_sent(editing, monkeypatch):
    _pressed, _edits, problems = editing
    monkeypatch.setattr(app.state, "last_inserted_text", "")
    monkeypatch.setattr(app.state, "sent", {"at": app.time.time(), "automatic": False})
    assert app._handle_spoken_edit("Edit that, make it shorter.", None, True, "type", app.state.message)
    assert problems == ["already_sent"]


def test_long_after_a_send_edit_that_is_words_again(editing, monkeypatch):
    _pressed, _edits, problems = editing
    monkeypatch.setattr(app.state, "last_inserted_text", "")
    monkeypatch.setattr(app.state, "sent", {"at": app.time.time() - 3600, "automatic": True})
    assert not app._handle_spoken_edit("Edit that, make it shorter.", None, True, "type", app.state.message)
    assert problems == []


def test_every_notice_has_a_short_label():
    assert set(edit_notices.NOTICES) == set(edit_notices.LABELS)


def test_pressing_enter_remembers_the_send(monkeypatch):
    class Backend:
        def press_keys(self, chords, claim=None):
            return True
    monkeypatch.setattr(app, "_output_backend", lambda: Backend())
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    monkeypatch.setattr(app.state, "sent", None)
    app._press_key("ENTER", "Send each dictation automatically is on")
    assert app.state.sent["automatic"] is True
    app._press_key("ENTER", 'said "press enter"')
    assert app.state.sent["automatic"] is False
