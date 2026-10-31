"""The Voice Commands window lists what the code really recognises.

That includes self-corrections ("no wait", "scratch that"), the words
"literal" really works with, and the fact that the commands are English words
whatever language you dictate in.
"""
import re

from tuxtalk import ai_cleanup, normalize, undo
from tuxtalk.voice_commands_dialog import _build_markup

TEXT = re.sub(r"<[^>]+>", "", _build_markup()).lower()


def test_self_corrections_are_listed():
    for phrase in ("no wait", "i mean", "make that", "scratch that", "or rather"):
        assert phrase in TEXT, phrase
        assert ai_cleanup._RE_CORRECTION.search(f"at 3, {phrase}, 4"), phrase
    assert "fix self-corrections with ai" in TEXT     # it only works with that on


def test_every_clear_everything_phrase_is_listed():
    for phrase in undo.EVERYTHING_PATTERNS:
        assert phrase in TEXT, phrase


def test_alternate_wordings_are_listed_and_work():
    for phrase in ("exclamation mark", "paragraph break", "newline"):
        assert phrase in TEXT, phrase
        assert phrase in normalize.LITERAL_REPLACEMENTS
    assert undo.detect_undo_command("remove last word") == ("word", 1)
    assert "remove last" in TEXT


def test_literal_names_words_it_really_covers():
    """Not "any punctuation or formatting word": brackets, em dash and full
    stop are not covered ("literal open parenthesis" types "("). The words it
    names as examples must all work with "literal"."""
    assert "any punctuation" not in TEXT
    assert "the word" in TEXT
    for word in ("period", "comma", "quote", "dash", "new line"):
        assert word in normalize.LITERAL_REPLACEMENTS, word


def test_help_lists_exactly_the_literal_words(help_pages):
    """Help must not say "Works with any punctuation or formatting command",
    or that "literal new line" types "newline" (it types "new line")."""
    commands = next(text for title, text in help_pages.items() if "Commands" in title)
    commands = re.sub(r"<[^>]+>", "", commands)
    listed = re.search(r"It works with: ([^.]*)\.", commands).group(1).split(", ")
    assert sorted(listed) == sorted(normalize.LITERAL_REPLACEMENTS)
    assert 'literal new line / the word new line → "new line"' in commands
    assert normalize.normalize_text("literal new line").lower().startswith("new line")


def test_says_commands_are_english_words():
    assert "english words" in TEXT


def test_a_settings_error_is_logged_not_shown_as_no_key(monkeypatch, caplog):
    """The window fell back to "set a hotkey" without a word in the log, so a
    real failure looked like a key nobody had set."""
    import logging
    from tuxtalk import voice_commands_dialog as vcd

    def broken():
        raise OSError("settings unreadable")
    monkeypatch.setattr("tuxtalk.config.load_config", broken)
    caplog.set_level(logging.INFO)
    assert vcd._hint_markup().startswith("Tip: Set a hotkey")
    assert any("settings unreadable" in r.getMessage() for r in caplog.records)


def test_the_tip_names_the_key_that_opens_the_window(monkeypatch):
    import types
    from tuxtalk import voice_commands_dialog as vcd
    monkeypatch.setattr("tuxtalk.config.load_config",
                        lambda: types.SimpleNamespace(voice_commands_hotkey="F12"))
    assert vcd._hint_markup() == "Tip: Press <b>F12</b> anytime to open this reference"


def _open_window(monkeypatch):
    """The window, built but never on the desktop."""
    import pytest
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    from tuxtalk import voice_commands_dialog as vcd
    monkeypatch.setattr(Gtk.Window, "show_all", lambda self: None)
    monkeypatch.setattr(Gtk.Window, "present", lambda self: None)
    return vcd.show_voice_commands_dialog()


def _key(widget, keyval):
    from gi.repository import Gdk
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = keyval
    return widget.emit("key-press-event", event)


def test_the_cheat_sheet_scrolls_from_the_keyboard(monkeypatch):
    """Its text takes the focus, so Page Down and the arrows scroll it and a
    screen reader can read it, as in Help. Before, only Close could be
    focused and a keyboard user saw the first third."""
    from gi.repository import Gdk, Gtk
    from gtk_text import widgets
    dialog = _open_window(monkeypatch)
    try:
        text = next(w for w in widgets(dialog)
                    if isinstance(w, Gtk.Label) and "Voice Commands" in w.get_label())
        assert text.get_selectable() and text.get_can_focus()
        assert _key(text, Gdk.KEY_Page_Down) is True
    finally:
        dialog.destroy()


def test_escape_and_close_shut_the_window_once(monkeypatch):
    from gi.repository import Gdk, Gtk
    dialog = _open_window(monkeypatch)
    destroyed = []
    dialog.connect("destroy", lambda _d: destroyed.append(True))
    _key(dialog, Gdk.KEY_Escape)
    assert destroyed == [True]
    dialog = _open_window(monkeypatch)
    destroyed.clear()
    dialog.connect("destroy", lambda _d: destroyed.append(True))
    dialog.get_widget_for_response(Gtk.ResponseType.CLOSE).clicked()
    assert destroyed == [True]
