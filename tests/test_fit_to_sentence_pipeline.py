"""A whole dictation, transcribed and typed, fitted into the sentence at
the cursor where the app says what's there (pipeline._read_cursor and
_fit_to_cursor), and typed as always where it doesn't."""
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
    monkeypatch.setattr("locale.getlocale", lambda *a: ("en_US", "UTF-8"))
    return seen


def dictate(monkeypatch, said, language=None):
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: said)
    frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    pipeline.transcribe_and_inject(frames, engine.SAMPLE_RATE, beeps_on=False,
                                   smart_quotes=False, notify_on=False, language=language,
                                   message=rs.state.current.message)


def test_a_word_said_into_the_middle_of_a_sentence_joins_it(desktop, monkeypatch):
    desktop["around"] = ("the big ", " dog")
    dictate(monkeypatch, "Very.")
    assert desktop["typed"] == ["very"]


def test_where_the_app_doesnt_say_it_is_typed_as_always(desktop, monkeypatch):
    dictate(monkeypatch, "Very.")
    assert desktop["typed"] == ["Very. "]


def test_a_terminal_is_never_asked(desktop, monkeypatch):
    desktop["class"] = "org.gnome.Terminal"
    monkeypatch.setattr(live, "adapt_to_app", True, raising=False)
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
