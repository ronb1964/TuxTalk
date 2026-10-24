"""Custom voice commands are called Snippets wherever users read about them.

"Commands" was easy to confuse with the built-in voice commands ("period",
"new line", "undo last word"). The saved file and the code keep their old
names; only the words on screen say Snippets.
"""
import types

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from gtk_text import find, shown_text, widgets
from tuxtalk import custom_words
from tuxtalk.prefs import PreferencesWindow
from tuxtalk.voice_commands_dialog import _build_markup


def _window(*tabs):
    notebook = Gtk.Notebook()
    for name in tabs:
        page = Gtk.Box()
        page.show()          # GtkNotebook won't switch to a hidden page
        notebook.append_page(page, Gtk.Label(label=name))
    return types.SimpleNamespace(notebook=notebook)


def shown_buttons(root):
    return [w for w in widgets(root) if isinstance(w, Gtk.Button)]


def _words_tab(window):
    notebook = window.notebook
    return next(notebook.get_nth_page(i) for i in range(notebook.get_n_pages())
                if notebook.get_tab_label_text(notebook.get_nth_page(i)) == "Words")


def test_the_preferences_tab_is_called_words(prefs_window):
    """Your Words and the Snippets share one tab, called Words."""
    window = prefs_window()
    tabs = [window.notebook.get_tab_label_text(page) for page in window.notebook.get_children()]
    assert "Words" in tabs and "Snippets" not in tabs and "Commands" not in tabs
    window.select_tab("words")
    shown = shown_text(_words_tab(window))
    assert "Add Snippet" in shown and "Add Word" in shown


def test_both_lists_have_the_same_buttons(prefs_window):
    """Your Words and Snippets work the same way: Add, Edit..., Remove."""
    buttons = [b.get_label() for b in shown_buttons(_words_tab(prefs_window()))]
    assert buttons.count("Edit...") == 2
    assert buttons.count("Remove") == 2
    assert "Remove Selected" not in buttons


def test_every_old_tab_name_opens_words():
    """--tab=commands or --tab=snippets from an older launcher or script still lands there."""
    window = _window("General", "Audio", "Advanced", "Words", "Stats")
    for asked in ("words", "snippets", "commands", "Commands"):
        window.notebook.set_current_page(0)
        assert PreferencesWindow.select_tab(window, asked) is True
        assert window.notebook.get_current_page() == 3


def test_help_and_quick_reference_say_words(help_pages):
    for name, text in (("Help", "\n".join(help_pages.values())),
                       ("Voice Commands", _build_markup())):
        assert "Preferences → Words" in text, name
        assert "Preferences → Snippets" not in text, name
        assert "Commands tab" not in text, name
        assert "Preferences → Commands" not in text, name


def test_the_words_tab_offers_fix_a_word(prefs_window, monkeypatch):
    from tuxtalk import fix_word_dialog
    opened = []
    monkeypatch.setattr(fix_word_dialog, "show_fix_word_dialog", opened.append)
    find(_words_tab(prefs_window()), Gtk.Button, "Fix a Word...").clicked()
    assert opened == [0]        # on the newest dictation


def _answer_dialogs(monkeypatch, answer, word=None):
    """Every dialog Preferences runs answers *answer*, with *word* typed in
    its text box first."""
    def run(dialog):
        if word is not None:
            find(dialog, Gtk.Entry).set_text(word)
        return answer
    monkeypatch.setattr(Gtk.Dialog, "run", run)


def test_a_word_added_on_the_words_tab_is_saved_and_listed(prefs_window, monkeypatch):
    window = prefs_window()
    _answer_dialogs(monkeypatch, Gtk.ResponseType.OK, word="TuxTalk")
    find(_words_tab(window), Gtk.Button, "Add Word").clicked()
    assert "TuxTalk" in custom_words.load()
    assert [row[0] for row in window.words_tab.words_store] == ["TuxTalk"]


def test_a_word_removed_on_the_words_tab_is_gone(prefs_window, monkeypatch):
    custom_words.save(["TuxTalk"])
    window = prefs_window()
    window.words_tab.words_tree.get_selection().select_path(0)
    _answer_dialogs(monkeypatch, Gtk.ResponseType.YES)
    remove = [b for b in shown_buttons(_words_tab(window)) if b.get_label() == "Remove"][0]
    remove.clicked()
    assert custom_words.load() == []
    assert len(window.words_tab.words_store) == 0


def test_the_command_line_opens_the_words_tab(prefs_main):
    """--tab=words, not only the old snippets/commands names, gets past argparse."""
    for tab in ("words", "snippets", "commands"):
        assert f"shows the {tab} tab" in prefs_main(f"--tab={tab}")
