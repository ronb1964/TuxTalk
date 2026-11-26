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
    box = _one(window, Gtk.SearchEntry)
    box.set_text(text)
    box.emit("search-changed")      # GTK waits a moment while typing goes on
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
    expected = help_search.search(help_dialog._open_help.entries, "labs")
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
    assert help_dialog._open_help is not None





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


def test_help_brought_forward_shows_the_commands_in_the_language_set_now(help_window,
                                                                         monkeypatch):
    """Voice commands in changed to Deutsch while Help was open: opening Help
    again shows and searches the German words, though no page's text changed."""
    def commands_tab():
        notebook = _one(help_window, Gtk.Notebook)
        return " ".join(_texts(notebook.get_nth_page(help_dialog._page_of(notebook, "Commands"))))
    assert "Beistrich" not in commands_tab()
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(command_language="de"))
    help_dialog.show_help_dialog()
    assert "Beistrich" in commands_tab()
    assert any("Beistrich" in text for r in _results(_search(help_window, "Beistrich"))
               for text in _texts(r))


def test_the_card_jumped_to_is_near_the_top_and_outlined():
    """On a shown Help: the card a result goes to sits near the top of its
    page, not wherever focusing it scrolled, and is outlined."""
    from gtk_keys import settle
    window = help_dialog.show_help_dialog()
    try:
        settle()
        parts = help_dialog._open_help
        parts.box.set_text("live text")
        parts.show_search()
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


@pytest.fixture
def shown_help(monkeypatch):
    """Help as it opens on the hidden test display, for real key presses."""
    from gtk_keys import settle
    monkeypatch.setattr(config, "load_config", config.Settings)
    window = help_dialog.show_help_dialog()
    settle()
    yield window
    if help_dialog._open_help is not None:
        window.destroy()


def _focused_result(window):
    """The result button with the keyboard, or None."""
    focus = window.get_focus()
    while focus is not None and not (isinstance(focus, Gtk.Button)
                                     and focus.get_style_context().has_class("tt-result")):
        focus = focus.get_parent()
    return focus


def test_down_goes_through_the_results_and_up_back_to_the_box(shown_help):
    from gtk_keys import press
    stack = _search(shown_help, "labs")
    results = _results(stack)
    box = _one(shown_help, Gtk.SearchEntry)
    box.grab_focus()
    press(shown_help, Gdk.KEY_Down)
    assert _focused_result(shown_help) is results[0]
    press(shown_help, Gdk.KEY_Down)
    assert _focused_result(shown_help) is results[1]
    press(shown_help, Gdk.KEY_Up)
    press(shown_help, Gdk.KEY_Up)
    assert shown_help.get_focus() is box


def test_a_letter_typed_on_the_tabs_starts_a_search(shown_help):
    from gtk_keys import press
    assert isinstance(shown_help.get_focus(), Gtk.Notebook)
    press(shown_help, Gdk.KEY_l)
    box = _one(shown_help, Gtk.SearchEntry)
    assert shown_help.get_focus() is box and box.get_text() == "l"


def test_enter_in_the_box_opens_the_first_result(shown_help):
    from gtk_keys import press
    box = _one(shown_help, Gtk.SearchEntry)
    box.grab_focus()
    box.set_text("uninstall")       # Enter comes before GTK's wait for typing ends
    press(shown_help, Gdk.KEY_Return)
    notebook = _one(shown_help, Gtk.Notebook)
    assert notebook.get_tab_label_text(notebook.get_nth_page(notebook.get_current_page())) \
        == "Problems"


def test_escape_in_the_box_empties_it_then_closes_help(shown_help):
    from gtk_keys import press
    _search(shown_help, "labs")
    box = _one(shown_help, Gtk.SearchEntry)
    box.grab_focus()
    press(shown_help, Gdk.KEY_Escape)
    assert box.get_text() == "" and shown_help.get_focus() is box
    assert help_dialog._open_help is not None
    press(shown_help, Gdk.KEY_Escape)
    assert help_dialog._open_help is None


def test_opening_help_on_a_tab_during_a_search_shows_that_tab(shown_help):
    stack = _search(shown_help, "labs")
    help_dialog.show_help_dialog(tab="writing")
    notebook = _one(shown_help, Gtk.Notebook)
    assert stack.get_visible_child_name() == "tabs"
    assert notebook.get_tab_label_text(notebook.get_nth_page(notebook.get_current_page())) \
        == "Writing"


def test_every_result_has_a_card_to_go_to(help_window):
    parts = help_dialog._open_help
    keys = [(e.tab, e.part, e.title) for e in parts.entries]
    assert len(set(keys)) == len(keys)
    assert all(key in parts.found for key in keys)
    widgets_ = [parts.found[key][1] for key in keys]
    assert len(set(map(id, widgets_))) == len(widgets_)


def test_a_command_result_opens_the_commands_tab(help_window):
    stack = _search(help_window, "press tab")
    command = next(r for r in _results(stack) if "Commands" in _texts(r))
    command.clicked()
    notebook = _one(help_window, Gtk.Notebook)
    page = notebook.get_nth_page(notebook.get_current_page())
    assert notebook.get_tab_label_text(page) == "Commands"
    assert any(w.get_style_context().has_class("tt-found") for w in widgets(page))


def test_a_screen_reader_hears_the_count_and_what_each_result_is(help_window):
    stack = _search(help_window, "live text")
    box = _one(help_window, Gtk.SearchEntry)
    count = len(_results(stack))
    assert box.get_accessible().get_description() == f"{count} results for 'live text'"
    labs = next(r for r in _results(stack) if "Labs" in _texts(r))
    assert "Labs" in labs.get_accessible().get_name()
    assert labs.get_accessible().get_description()


def test_ctrl_f_goes_to_the_search_box(shown_help):
    from gtk_keys import press
    press(shown_help, Gdk.KEY_f, Gdk.ModifierType.CONTROL_MASK)
    assert shown_help.get_focus() is _one(shown_help, Gtk.SearchEntry)
