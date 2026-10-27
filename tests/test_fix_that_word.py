""""fix that word" opens Fix a Word over the app, on the newest dictation
(2026-10-14): learning from corrections, the way TuxTalk can, since it can't
see a word retyped by hand."""
import pytest

from tuxtalk import explain, focused_window
from tuxtalk import voice_commands
from tuxtalk import keyboard_output
from tuxtalk import recording_state as rs


@pytest.fixture
def opened(monkeypatch):
    started = []
    monkeypatch.setattr(explain, "start_helper_window", lambda module, *a: started.append(module))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a: None)
    return started


@pytest.mark.parametrize("said", ["Fix that word.", "fix that", "Fix a word!", "fix the last word",
                                  "fix this word", "Fix that, please."])
def test_the_phrases_open_fix_a_word(opened, said):
    assert voice_commands.handle_fix_word(said, False) is True
    assert opened == ["tuxtalk.fix_word_dialog"]


@pytest.mark.parametrize("said", ["we need to fix that word in the report", "fix the sink",
                                  "fix", "that word"])
def test_inside_a_sentence_it_is_typed(opened, said):
    assert voice_commands.handle_fix_word(said, False) is False
    assert opened == []


def test_a_window_that_cannot_start_says_so(monkeypatch):
    notices = []
    monkeypatch.setattr(explain, "start_helper_window",
                        lambda *a: (_ for _ in ()).throw(OSError("no python")))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, msg: notices.append(msg))
    assert voice_commands.handle_fix_word("fix that word", False) is True
    assert notices and "TuxTalk menu" in notices[0]


def test_the_voice_window_shows_only_the_newest_dictation(monkeypatch):
    """No dropdown: it would break on XWayland, where the window is placed."""
    pytest.importorskip("gi")
    from tuxtalk import fix_word_dialog as fw
    built = {}

    class Dialog:
        def __init__(self, entries, index, copy_text, before_show=None, in_place=False):
            built.update(entries=entries, before_show=before_show, in_place=in_place)
            self.fixed_for_text = None
            self.window = type("W", (), {"connect": lambda *a: None, "present": lambda *a: None})()
    monkeypatch.setattr(fw, "_FixWordDialog", Dialog)
    monkeypatch.setattr(fw, "get_entries", lambda: ["newest one", "older", "oldest"])
    monkeypatch.setattr(fw.Gtk, "main", lambda: None)
    assert fw.main([]) == 0
    assert built["entries"] == ["newest one"] and built["in_place"] is False
    assert built["before_show"] is explain.place_over_active_window


# --- fixing it in the text (Ron, 2026-10-14) ----------------------------------------------

@pytest.fixture
def typed(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(rs.state, "last_inserted_text", " Mary had a lamb whose fleece was white. ")
    monkeypatch.setattr(rs.state, "last_inserted_window", "org.kde.kwrite")
    return tmp_path


def test_right_after_a_dictation_it_can_fix_the_text(opened, typed, monkeypatch):
    calls = []
    monkeypatch.setattr(explain, "start_helper_window", lambda module, *a: calls.append(a))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "org.kde.kwrite")
    voice_commands.handle_fix_word("fix that word", False)
    flag, path = calls[0]
    import json, os, stat
    assert flag == "--in-place"
    assert json.load(open(path)) == {"text": " Mary had a lamb whose fleece was white. ",
                                     "window": "org.kde.kwrite", "where": None}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600          # only this user
    assert rs.state.last_inserted_text == ""                     # undo can't trust it now


def test_fix_a_word_gets_the_window_the_dictation_went_to(opened, typed, monkeypatch):
    import json
    from tuxtalk import fix_word_dialog as fw
    calls, fixed = [], []
    monkeypatch.setattr(rs.state, "last_inserted_where", ("org.kde.kwrite", ("tray", "{first}")))
    monkeypatch.setattr(explain, "start_helper_window", lambda module, *a: calls.append(a))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "org.kde.kwrite")
    voice_commands.handle_fix_word("fix that word", False)
    assert json.load(open(calls[0][1]))["where"] == ["tray", "{first}"]

    class Dialog:
        def __init__(self, *a, **k):
            self.fixed_for_text = (0, 4, "Marie")
            self.window = type("W", (), {"connect": lambda *a: None, "present": lambda *a: None})()
    monkeypatch.setattr(fw, "_FixWordDialog", Dialog)
    monkeypatch.setattr(fw.Gtk, "main", lambda: None)
    monkeypatch.setattr(fw, "fix_in_text", lambda *a: fixed.append(a) or False)
    fw.main(list(calls[0]))
    assert fixed[0][2:] == ("org.kde.kwrite", ("tray", "{first}"))     # tuples, as looked


def test_in_another_window_it_only_saves_the_fix(opened, typed, monkeypatch):
    calls = []
    monkeypatch.setattr(explain, "start_helper_window", lambda module, *a: calls.append(a))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "libreoffice-writer")
    voice_commands.handle_fix_word("fix that word", False)
    assert calls == [()]


def test_fix_in_text_swaps_only_the_word(monkeypatch):
    """It retyped the whole dictation; only the word should change (Ron)."""
    from tuxtalk import fix_word_dialog as fw
    pressed, typed_text = [], []

    class Backend:
        def press_keys(self, chords):
            pressed.append(chords)
            return True

        def type_text(self, text):
            typed_text.append(text)
            return True
    monkeypatch.setattr(fw, "_REFOCUS_S", 0)
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: Backend())
    monkeypatch.setattr(focused_window, "window_class", lambda: "org.kde.kwrite")
    old = " Whose fleas was white. "                  # as typed, outer spaces and all
    core = old.strip()
    start = core.index("fleas")
    assert fw.fix_in_text(old, (start, start + 5, "fleece"), "org.kde.kwrite") is True
    after = len(" was white.") + 1                     # the rest, plus the trailing space
    S = ("SHIFT",)
    assert pressed[0] == [((), "LEFT")] * after + [(S, "LEFT")] * 5
    assert typed_text == ["fleece"]                    # just the word
    assert pressed[1] == [((), "RIGHT")] * after       # back to the end



@pytest.fixture
def selection(monkeypatch):
    """fix_in_text's keys, and what the app's selection reads back as."""
    from tuxtalk import clipboard
    from tuxtalk import fix_word_dialog as fw
    pressed, typed_text, reads = [], [], {"text": "fleas"}

    class Backend:
        def press_keys(self, chords):
            pressed.append(chords)
            return True

        def type_text(self, text):
            typed_text.append(text)
            return True
    monkeypatch.setattr(fw, "_REFOCUS_S", 0)
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: Backend())
    monkeypatch.setattr(keyboard_output, "is_wayland_session", lambda: True)
    monkeypatch.setattr(focused_window, "window_class", lambda: "org.kde.kwrite")
    monkeypatch.setattr(clipboard, "copy_selection",
                        lambda send: (reads["text"], lambda also=(): None))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda t, m: reads.setdefault("notice", m))
    return pressed, typed_text, reads


def test_a_cursor_moved_since_the_dictation_changes_nothing(selection):
    """A click or the arrow keys after dictating: counting back from where
    the dictation ended selected other words, and the fix replaced them."""
    from tuxtalk import fix_word_dialog as fw
    pressed, typed_text, reads = selection
    reads["text"] = "was w"                            # what those keys selected now
    old = " Whose fleas was white. "
    start = old.strip().index("fleas")
    assert fw.fix_in_text(old, (start, start + 5, "fleece"), "org.kde.kwrite") is False
    after = len(" was white.") + 1
    assert typed_text == [] and pressed[1] == [((), "RIGHT")] * (after + 1)   # put back
    assert "wasn't where the dictation ended" in reads["notice"]


def test_the_picked_words_still_selected_are_fixed(selection):
    from tuxtalk import fix_word_dialog as fw
    pressed, typed_text, reads = selection
    old = " Whose fleas was white. "
    start = old.strip().index("fleas")
    assert fw.fix_in_text(old, (start, start + 5, "fleece"), "org.kde.kwrite") is True
    assert typed_text == ["fleece"]


def test_a_terminal_isnt_changed(selection, monkeypatch):
    """Shift+Left selects nothing there: the replacement went in mid-line."""
    from tuxtalk import fix_word_dialog as fw
    pressed, typed_text, reads = selection
    monkeypatch.setattr(focused_window, "window_class", lambda: "org.kde.konsole")
    assert fw.fix_in_text(" git stats ", (4, 9, "status"), "org.kde.konsole") is False
    assert pressed == [] and "terminal" in reads["notice"]

def test_the_word_span_leaves_its_punctuation_out():
    from tuxtalk import vocabulary
    text = "I use Bamboo Studio, daily."
    start, end = vocabulary.core_span(text, 2, 3)
    assert text[start:end] == "Bamboo Studio"


def test_fix_in_text_leaves_another_window_alone(monkeypatch):
    from tuxtalk import fix_word_dialog as fw
    notices = []
    monkeypatch.setattr(fw, "_REFOCUS_S", 0)
    monkeypatch.setattr(focused_window, "window_class", lambda: "firefox")
    monkeypatch.setattr("tuxtalk.notify.notify", lambda t, m: notices.append(f"{t}\n{m}"))
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: pytest.fail("typed into the wrong window"))
    assert fw.fix_in_text(" Hello. ", (0, 5, "Hi"), "org.kde.kwrite") is False
    assert notices and "wasn't changed" in notices[0]


def test_fix_in_text_leaves_another_window_of_the_same_app_alone(monkeypatch):
    """Two KWrite documents: "fix that word" after dictating into one, then
    a click into the other while Fix a Word was open, replaced its words."""
    from tuxtalk import fix_word_dialog as fw
    monkeypatch.setattr(fw, "_REFOCUS_S", 0)
    monkeypatch.setattr(focused_window, "window_class", lambda: "org.kde.kwrite")
    monkeypatch.setattr(focused_window, "look", lambda: ("org.kde.kwrite", ("tray", "{second}")))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda t, m: None)
    monkeypatch.setattr(keyboard_output, "output_backend",
                        lambda: pytest.fail("typed into the other document"))
    assert fw.fix_in_text(" Hello. ", (0, 5, "Hi"), "org.kde.kwrite", ("tray", "{first}")) is False


def test_the_checkbox_says_what_it_does(monkeypatch):
    pytest.importorskip("gi")
    from gtk_text import widgets
    from tuxtalk import fix_word_dialog as fw
    # Built, never shown: a test must not flash windows on the user's screen.
    monkeypatch.setattr(fw.Gtk.Window, "show_all", lambda self: None)
    voice = fw._FixWordDialog(["whose fleece"], 0, lambda t: None, in_place=True)
    tray = fw._FixWordDialog(["whose fleece"], 0, lambda t: None)
    try:
        assert voice.copy_check.get_label() == "Also fix it in my text"
        assert "whole corrected dictation" in tray.copy_check.get_label()

        def labels(dialog):
            return " ".join(w.get_text() for w in widgets(dialog.window)
                            if isinstance(w, fw.Gtk.Label))
        assert "fix that word" in labels(tray)          # the tray points to the voice command
        assert "fix that word" not in labels(voice)
    finally:
        voice.window.destroy()
        tray.window.destroy()


def test_fixing_in_the_text_uses_the_users_speed_and_waits_for_the_last_keys(monkeypatch, tmp_path):
    """This window types from its own process: the service's speed setting
    isn't there unless it reads it, and its keyboard goes when it ends."""
    pytest.importorskip("gi")
    import json
    from tuxtalk import config, fix_word_dialog as fw
    path = tmp_path / "fix.json"
    path.write_text(json.dumps({"text": " Mary had a lamb. ", "window": "org.kde.kwrite"}))

    class Dialog:
        def __init__(self, entries, index, copy_text, before_show=None, in_place=False):
            self.fixed_for_text = (5, 8, "Marie")
            self.window = type("W", (), {"connect": lambda *a: None, "present": lambda *a: None})()
    monkeypatch.setattr(fw, "_FixWordDialog", Dialog)
    monkeypatch.setattr(fw.Gtk, "main", lambda: None)
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(key_delay=20))
    monkeypatch.setattr(keyboard_output, "key_delay_ms", keyboard_output.key_delay_ms)
    monkeypatch.setattr(fw, "fix_in_text", lambda *a: True)
    waits = []
    monkeypatch.setattr(fw.time, "sleep", waits.append)
    assert fw.main(["--in-place", str(path)]) == 0
    assert keyboard_output.key_delay_ms == 20
    assert waits == [fw.LAST_KEYS_S]


def test_a_fix_the_keyboard_dropped_partway_is_not_done(monkeypatch, keyboard):
    pytest.importorskip("gi")
    from tuxtalk import fix_word_dialog as fw
    monkeypatch.setattr(fw.time, "sleep", lambda s: None)
    monkeypatch.setattr(focused_window, "window_class", lambda: "org.kde.kwrite")
    keyboard.fail_after = 3
    assert fw.fix_in_text(" Hello there. ", (0, 5, "Hi"), "org.kde.kwrite") is False
