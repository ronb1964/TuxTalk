"""The Voice Commands window lists what the code really recognises.

That includes self-corrections ("no wait", "scratch that"), the words
"literal" really works with, and the fact that the commands are English words
whatever language you dictate in.
"""
import re

import pytest

from tuxtalk import ai_cleanup, normalize, undo, voice_commands_dialog


@pytest.fixture
def text():
    """Every tab of the Voice Commands window, as one lower-case text."""
    from gtk_text import tree_text
    dialog = voice_commands_dialog.show_voice_commands_dialog()
    try:
        return "\n".join(tree_text(dialog)).lower()
    finally:
        dialog.destroy()


def test_self_corrections_are_listed(text):
    for phrase in ("no wait", "i mean", "make that", "scratch that", "or rather"):
        assert phrase in text, phrase
        assert ai_cleanup._RE_CORRECTION.search(f"at 3, {phrase}, 4"), phrase
    assert "fix self-corrections with ai" in text     # it only works with that on


def test_every_clear_everything_phrase_is_listed(text):
    for phrase in undo.EVERYTHING_PATTERNS:
        assert phrase in text, phrase


def test_alternate_wordings_are_listed_and_work(text):
    for phrase in ("exclamation mark", "paragraph break", "newline"):
        assert phrase in text, phrase
        assert phrase in normalize.LITERAL_REPLACEMENTS
    assert undo.detect_undo_command("remove last word") == ("word", 1)
    assert "remove last" in text


def test_literal_names_words_it_really_covers(text):
    """Not "any punctuation or formatting word": brackets, em dash and full
    stop are not covered ("literal open parenthesis" types "("). The words it
    names as examples must all work with "literal"."""
    assert "any punctuation" not in text
    assert "the word" in text
    for word in ("period", "comma", "quote", "dash", "new line"):
        assert word in normalize.LITERAL_REPLACEMENTS, word


def test_help_lists_exactly_the_literal_words(help_pages):
    """Help must not say "Works with any punctuation or formatting command",
    or that "literal new line" types "newline" (it types "new line")."""
    commands = next(text for title, text in help_pages.items() if "Commands" in title)
    commands = re.sub(r"<[^>]+>", "", commands)
    listed = re.search(r"It works with: ([^.]*)\.", commands).group(1).split(", ")
    assert sorted(listed) == sorted(normalize.LITERAL_REPLACEMENTS)
    assert 'literal new line, the word new line\n"new line" (not a line break)' in commands
    assert normalize.normalize_text("literal new line").lower().startswith("new line")


def test_says_commands_are_english_words(text):
    assert "english words" in text


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
    from gi.repository import Gtk
    from tuxtalk import voice_commands_dialog as vcd
    monkeypatch.setattr(Gtk.Window, "show_all", lambda self: None)
    monkeypatch.setattr(Gtk.Window, "present", lambda self: None)
    return vcd.show_voice_commands_dialog()


def _key(widget, keyval):
    from gi.repository import Gdk
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = keyval
    return widget.emit("key-press-event", event)


def test_tab_goes_into_each_tab_and_the_keys_scroll_it():
    """From the tabs, Tab goes into the page shown, and Page Down and the
    arrows scroll it, as in Help. Before, only Close could be focused and a keyboard user
    saw the first third."""
    from gi.repository import Gdk, Gtk
    from gtk_keys import press, settle
    from gtk_text import widgets
    dialog = voice_commands_dialog.show_voice_commands_dialog()
    settle()
    try:
        tabs = next(w for w in widgets(dialog) if isinstance(w, Gtk.Notebook))
        for number in range(tabs.get_n_pages()):
            tabs.set_current_page(number)
            tabs.grab_focus()
            press(dialog, Gdk.KEY_Tab)
            page = tabs.get_nth_page(number)
            assert dialog.get_focus() in list(widgets(page)), number
        tabs.set_current_page(0)
        tabs.grab_focus()
        press(dialog, Gdk.KEY_Tab)
        scroll = tabs.get_nth_page(0).get_vadjustment()
        start, card = scroll.get_value(), dialog.get_focus()
        press(dialog, Gdk.KEY_Page_Down)
        paged = scroll.get_value()
        assert paged > start + scroll.get_page_size() / 2
        press(dialog, Gdk.KEY_Down)
        assert scroll.get_value() > paged and dialog.get_focus() is card
    finally:
        dialog.destroy()


def test_the_tabs_are_the_groups_of_commands(monkeypatch):
    from gi.repository import Gtk
    from gtk_text import widgets
    from tuxtalk import commands_reference
    dialog = _open_window(monkeypatch)
    try:
        tabs = next(w for w in widgets(dialog) if isinstance(w, Gtk.Notebook))
        titles = [tabs.get_tab_label_text(tabs.get_nth_page(n)) for n in range(tabs.get_n_pages())]
        assert titles == list(commands_reference.GROUPS)
    finally:
        dialog.destroy()


def test_escape_and_close_shut_the_window(monkeypatch):
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
