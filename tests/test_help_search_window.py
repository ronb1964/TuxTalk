"""Help's search box: typing shows the matching cards, each naming its tab,
under a line saying how many; picking one opens its tab at that card."""
import pytest
from gi.repository import Gdk, Gtk
from gtk_text import widgets

from tuxtalk import config, help_dialog, help_search


@pytest.fixture
def help_window(monkeypatch):
    """Help, built but not shown, from default settings."""
    monkeypatch.setattr(config, "load_config", config.Settings)
    monkeypatch.setattr(Gtk.Window, "show_all", lambda self: None)
    monkeypatch.setattr(Gtk.Window, "present", lambda self: None)
    window = help_dialog.show_help_dialog()
    yield window
    window.destroy()


def _one(window, kind):
    return next(w for w in widgets(window) if isinstance(w, kind))


def _search(window, text):
    _one(window, Gtk.SearchEntry).set_text(text)
    return _one(window, Gtk.Stack)


def _texts(root):
    return [w.get_text() for w in widgets(root) if isinstance(w, Gtk.Label)]


def _results(stack):
    return [w for w in widgets(stack.get_visible_child())
            if isinstance(w, Gtk.Button) and w.get_style_context().has_class("tt-result")]


def _key(window, keyval, state=0):
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval, event.state, event.window = keyval, state, window.get_window()
    return window.emit("key-press-event", event)


def test_typing_shows_result_cards_under_a_count(help_window):
    stack = _search(help_window, "labs")
    expected = help_search.search(help_dialog.open_help().entries, "labs")
    assert stack.get_visible_child_name() == "results"
    assert f"{len(expected)} results for 'labs'" in _texts(stack.get_visible_child())
    assert len(_results(stack)) == len(expected)


def test_each_result_names_its_tab(help_window):
    stack = _search(help_window, "uninstall")
    texts = _texts(_results(stack)[0])
    assert "Uninstalling" in texts and "Problems" in texts


def test_picking_a_result_opens_its_tab_at_the_card(help_window):
    stack = _search(help_window, "live text")
    _results(stack)[0].clicked()
    notebook = _one(help_window, Gtk.Notebook)
    page = notebook.get_nth_page(notebook.get_current_page())
    assert stack.get_visible_child_name() == "tabs"
    assert notebook.get_tab_label_text(page) == "Hands-free"
    assert any(w.get_style_context().has_class("tt-found") for w in widgets(page))
    assert _one(help_window, Gtk.SearchEntry).get_text() == ""


def test_clearing_the_box_brings_the_tabs_back(help_window):
    stack = _search(help_window, "labs")
    _search(help_window, "")
    assert stack.get_visible_child_name() == "tabs"


def test_escape_clears_a_search_before_it_closes_help(help_window):
    _search(help_window, "labs")
    assert _key(help_window, Gdk.KEY_Escape)
    assert _one(help_window, Gtk.SearchEntry).get_text() == ""
    assert help_dialog.open_help() is not None


def test_ctrl_f_goes_to_the_search_box(help_window):
    entry = _one(help_window, Gtk.SearchEntry)
    _key(help_window, Gdk.KEY_f, Gdk.ModifierType.CONTROL_MASK)
    assert help_window.get_focus() is entry


def test_no_match_says_so(help_window):
    stack = _search(help_window, "zzzz")
    assert "No results for 'zzzz'" in _texts(stack.get_visible_child())


def test_a_search_with_markup_characters_shows_safely(help_window):
    stack = _search(help_window, "<b>&")
    assert "No results for '<b>&'" in _texts(stack.get_visible_child())


def test_search_reads_the_new_text_after_help_is_rewritten(help_window, monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(hotkey="F6"))
    help_dialog.show_help_dialog()      # brings the open Help forward, rewritten
    stack = _search(help_window, "f6")
    assert _results(stack)


def test_the_card_jumped_to_is_near_the_top_and_outlined():
    """On a shown Help: the card a result goes to sits near the top of its
    page, not wherever focusing it scrolled, and is outlined."""
    from gtk_keys import settle
    window = help_dialog.show_help_dialog()
    try:
        settle()
        parts = help_dialog.open_help()
        parts.box.set_text("live text")
        settle()
        entry = parts.matches[0]
        parts.jump(entry)
        settle()
        focus, card = parts.found[(entry.tab, entry.part, entry.title)]
        notebook = parts.notebook
        page = notebook.get_nth_page(notebook.get_current_page())
        _x, y = card.translate_coordinates(page, 0, 0)
        assert 0 <= y < 80
        assert card.get_style_context().has_class("tt-found")
    finally:
        window.destroy()
