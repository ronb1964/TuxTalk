"""A whole dictation, transcribed and typed, fitted into the sentence at
the cursor where the app says what's there (pipeline._read_cursor and
_fit_to_cursor), and typed as always where it doesn't."""
import types

import numpy as np
import pytest

from tuxtalk import (atspi_helper, delivery, engine, focused_window, history, keyboard_output,
                     pipeline, screen_message)
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs


@pytest.fixture
def desktop(monkeypatch):
    """What's typed, the window class, and the text around the cursor the
    app reports (*around*, None: the app doesn't say); *reads* counts the
    questions."""
    seen = {"typed": [], "around": None, "reads": 0, "class": None}

    class Keyboard:
        def typeable(self, text):
            return text

        def type_text(self, text):
            seen["typed"].append(text)
            return True

    def read():
        seen["reads"] += 1
        return seen["around"]

    monkeypatch.setattr(keyboard_output, "output_backend", lambda: Keyboard())
    monkeypatch.setattr(delivery, "determine_injection_method", lambda mode: ("type", "test"))
    monkeypatch.setattr(history, "add_entry", lambda text, replaces=None: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: None)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda on, *tone, **k: None)
    monkeypatch.setattr(screen_message, "show_over_active_window", lambda text, **k: None)
    monkeypatch.setattr(focused_window, "window_class", lambda: seen["class"])
    monkeypatch.setattr(focused_window, "text_around_cursor", read)
    monkeypatch.setattr(atspi_helper, "focused_is_password", lambda *a, **k: False)
    monkeypatch.setattr(live, "usage_stats", False, raising=False)
    monkeypatch.setattr(live, "words", [], raising=False)
    monkeypatch.setattr(live, "auto_languages", (), raising=False)
    monkeypatch.setattr("tuxtalk.recommend.effective_language", lambda cfg: "en")
    return seen


def dictate(monkeypatch, said, language=None, chunk_role=None):
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: said)
    frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    pipeline.transcribe_and_inject(frames, engine.SAMPLE_RATE, beeps_on=False,
                                   smart_quotes=False, notify_on=False, language=language,
                                   chunk_role=chunk_role, message=rs.state.current.message)


def test_a_word_said_into_the_middle_of_a_sentence_joins_it(desktop, monkeypatch):
    desktop["around"] = ("the big ", " dog")
    dictate(monkeypatch, "Very.")
    assert desktop["typed"] == ["very"]


def test_where_the_app_doesnt_say_it_is_typed_as_always(desktop, monkeypatch):
    dictate(monkeypatch, "Very.")
    assert desktop["typed"] == ["Very. "]


def test_a_terminal_is_never_asked(desktop, monkeypatch):
    desktop["class"] = "org.gnome.Terminal"
    monkeypatch.setattr(live, "adapt_to_app", False, raising=False)
    desktop["around"] = ("the big ", " dog")
    dictate(monkeypatch, "Very.")
    assert desktop["reads"] == 0


def test_german_keeps_its_capital_mid_sentence(desktop, monkeypatch):
    desktop["around"] = ("der große", "")
    dictate(monkeypatch, "Hund.", language="de")
    assert desktop["typed"] == [" Hund. "]


def test_the_screen_not_the_last_dictation_decides_a_new_sentence(desktop, monkeypatch):
    """The last dictation ended on "and", but the user has since written
    "done. " themselves: the next one starts a sentence."""
    rs.state.continue_mid_sentence = True
    desktop["around"] = ("done. ", "")
    dictate(monkeypatch, "Next one.")
    assert desktop["typed"] == ["Next one. "]


def test_the_last_dictation_still_decides_where_the_app_doesnt_say(desktop, monkeypatch):
    rs.state.continue_mid_sentence = True
    dictate(monkeypatch, "Next one.")
    assert desktop["typed"] == ["next one. "]


def test_a_type_at_pause_phrase_after_one_it_typed_isnt_read_again(desktop, monkeypatch):
    """The keys just typed may not have reached the app yet; carrying on
    from its own phrase is known exactly (continue_mid_sentence)."""
    rs.state.current.message.has_words = True
    desktop["around"] = ("the big ", "")
    monkeypatch.setattr(pipeline.type_at_pause, "resolve_boundary", lambda *a, **k: None,
                        raising=False)
    dictate(monkeypatch, "Very.", chunk_role="middle")
    assert desktop["reads"] == 0


def test_a_correction_takes_the_fitted_start_of_the_phrase_it_replaces(monkeypatch):
    """" meet at three" was fitted into "the big|"; "no wait, four" erases
    it, space and all, so the fixed phrase starts the way it did."""
    from tuxtalk import ai_cleanup, type_at_pause
    monkeypatch.setattr(rs.state, "last_inserted_text", " meet at three. ")
    monkeypatch.setattr(ai_cleanup, "fix_self_corrections", lambda text, engine: "Meet at four.")
    monkeypatch.setattr(type_at_pause, "_erase_typed", lambda text: True)
    message = types.SimpleNamespace(held_cue="", cancelled=False)
    fixed, taken_back = type_at_pause.correct_across_pause("no wait, four.", message)
    assert (fixed, taken_back) == (" meet at four.", " meet at three. ")


def test_a_corrected_german_noun_keeps_its_capital(monkeypatch):
    from tuxtalk import ai_cleanup, type_at_pause
    monkeypatch.setattr(rs.state, "last_inserted_text", " drei Katzen ")
    monkeypatch.setattr(ai_cleanup, "fix_self_corrections", lambda text, engine: "Hunde")
    monkeypatch.setattr(type_at_pause, "_erase_typed", lambda text: True)
    message = types.SimpleNamespace(held_cue="", cancelled=False)
    fixed, _ = type_at_pause.correct_across_pause("nein, Hunde", message, lowercase_ok=False)
    assert fixed == " Hunde"


def test_german_by_the_usual_language_keeps_its_capital(desktop, monkeypatch):
    monkeypatch.setattr("tuxtalk.recommend.effective_language", lambda cfg: "de")
    desktop["around"] = ("der große", "")
    dictate(monkeypatch, "Hund.")
    assert desktop["typed"] == [" Hund. "]


def test_a_held_german_cue_doesnt_make_the_noun_after_it_small(monkeypatch):
    """"nein," held from the phrase before, then "Hunde": with the AI
    changing nothing, the noun keeps its capital."""
    from tuxtalk import ai_cleanup, type_at_pause
    monkeypatch.setattr(rs.state, "last_inserted_text", " drei Katzen ")
    monkeypatch.setattr(ai_cleanup, "fix_self_corrections", lambda text, engine: text)
    message = types.SimpleNamespace(held_cue="nein", cancelled=False)
    said, taken_back = type_at_pause.correct_across_pause("Hunde", message, lowercase_ok=False)
    assert (said, taken_back) == ("nein, Hunde", "")


@pytest.mark.parametrize("before, after, typed, end", [
    ("the big ", " dog", "very", []),               # mid-line: no period, no second space
    ("the big ", "", "very", [". "]),               # the end of the line: as always
    ("We met. ", " Then we left.", "Very", ["."]),  # a sentence of its own, before another
], ids=["mid-line", "end of line", "before a sentence"])
def test_type_at_pause_ending_in_a_pause_fits_the_text_after_the_cursor(
        desktop, monkeypatch, before, after, typed, end):
    """A recording ended in a pause types the mark held back after its last
    phrase. Mid-line it left "the big very.  dog"; the cursor is read once
    more for it, now that the phrase is on the screen."""
    reads = iter([(before, after), (before + typed, after)])
    monkeypatch.setattr(focused_window, "text_around_cursor", lambda: next(reads))
    dictate(monkeypatch, "Very.", chunk_role="middle")
    dictate(monkeypatch, "", chunk_role="last")
    assert desktop["typed"] == [typed] + end
