"""Every Help tab shows its topics as cards, like the Commands tab.

Help's text marks where each card starts (help_pages.parse reads the
marks); these tests pin the marks being read right, every tab using them,
and the cards a keyboard and a screen reader can reach.
"""
import re

import pytest

from tuxtalk import help_pages, help_text

SAMPLE = """# Dictating

Before any card.

### Without holding a key
Tap F9 to start.
Tap it again to stop.

### Performance presets (TuxTalk menu → Performance)
  • Lightest

## 🧪 Voice Editing
Change text by saying how.

### How to use it
1. Select.

### 🧪 Changing it before it's sent
Hold F8."""


def test_the_marks_make_a_title_parts_and_cards():
    page = help_pages.parse(SAMPLE)
    assert page.title == "Dictating"
    first, editing = page.parts
    assert first.title == "" and first.intro == "Before any card."
    assert [s.title for s in first.sections] == ["Without holding a key", "Performance presets"]
    assert first.sections[0].body == "Tap F9 to start. Tap it again to stop."
    assert first.sections[1].note == "(TuxTalk menu → Performance)"
    assert editing.title == "Voice Editing" and editing.labs
    assert editing.intro == "Change text by saying how."
    assert [(s.title, s.labs) for s in editing.sections] == [
        ("How to use it", False), ("Changing it before it's sent", True)]


def test_lines_broken_by_hand_flow_on_but_lists_keep_their_lines():
    """Help's text is broken at about 85 characters in the source; in a card
    as wide as the window that left half of it empty."""
    page = help_pages.parse("""# T

### Taking it back
Each of these is a dictation of its own, said right
after the one it takes back.
  • <b>delete last word</b> and <b>delete last sentence</b>
    remove what you just dictated.
  • Ctrl+Z works too.
  1. Click into the first box.
     – <b>.deb:</b> the package

A new paragraph.""")
    assert page.parts[0].sections[0].body == (
        "Each of these is a dictation of its own, said right after the one it takes back.\n"
        "  • <b>delete last word</b> and <b>delete last sentence</b> remove what you just "
        "dictated.\n"
        "  • Ctrl+Z works too.\n"
        "  1. Click into the first box.\n"
        "     – <b>.deb:</b> the package\n"
        "\n"
        "A new paragraph.")


def _prose_tabs():
    return [(title, text) for title, text in help_text.tabs()
            if title != help_text.COMMANDS_TITLE]


@pytest.mark.parametrize("title", [t for t, _ in _prose_tabs()])
def test_every_tab_is_cards(title):
    page = help_pages.parse(dict(_prose_tabs())[title])
    assert page.title
    sections = [s for part in page.parts for s in part.sections]
    assert sections, title
    for section in sections:
        assert section.title and section.body.strip(), (title, section.title)


@pytest.mark.parametrize("title", [t for t, _ in _prose_tabs()])
def test_no_heading_is_left_as_plain_bold_text(title):
    """A heading still written as a bold line, after a blank one, would sit
    inside the card above it instead of starting its own."""
    text = dict(_prose_tabs())[title]
    stray = re.findall(r"\n\n(<b>[^<]+</b>(?: \([^)]*\))?)\n", text)
    assert stray == [], title


@pytest.fixture
def commands_free_help(monkeypatch):
    """Help, built but not shown, from default settings."""
    from gi.repository import Gtk
    from tuxtalk import config, help_dialog
    monkeypatch.setattr(config, "load_config", config.Settings)
    monkeypatch.setattr(Gtk.Window, "show_all", lambda self: None)
    monkeypatch.setattr(Gtk.Window, "present", lambda self: None)
    window = help_dialog.show_help_dialog()
    yield window
    window.destroy()


def _pages(window):
    from gi.repository import Gtk
    from gtk_text import widgets
    notebook = next(w for w in widgets(window) if isinstance(w, Gtk.Notebook))
    return {notebook.get_tab_label_text(notebook.get_nth_page(n)): notebook.get_nth_page(n)
            for n in range(notebook.get_n_pages())}


def test_each_card_shows_its_heading_in_a_band_in_the_tabs_colour(commands_free_help):
    from gtk_text import widgets
    for title, page in _pages(commands_free_help).items():
        if title == help_text.COMMANDS_TITLE:
            continue
        heads = [w for w in widgets(page) if w.get_style_context().has_class("tt-card-head")]
        assert heads, title
        look = help_pages.TAB_LOOK[title][0]
        assert all(h.get_style_context().has_class(f"tt-{look}") for h in heads), title


def test_a_screen_reader_hears_each_cards_heading_on_its_text(commands_free_help):
    """Tab reaches each card's text (it can be read, selected and its links
    followed), and its name is the card's heading."""
    from gi.repository import Gtk
    from gtk_text import widgets
    page = _pages(commands_free_help)["Writing"]
    bodies = [w for w in widgets(page) if isinstance(w, Gtk.Label)
              and w.get_style_context().has_class("tt-card-text")]
    names = [b.get_accessible().get_name() for b in bodies]
    assert "Filling in a form" in names and "How to use it" in names
    assert all(b.get_selectable() for b in bodies)


def test_the_links_in_problems_still_open(commands_free_help):
    from gi.repository import Gtk
    from gtk_text import widgets
    page = _pages(commands_free_help)[help_text.PROBLEMS_TITLE]
    markup = " ".join(w.get_label() for w in widgets(page) if isinstance(w, Gtk.Label))
    assert f'href="{help_text.ISSUES_URL}"' in markup
    assert f'href="{help_text.REPORT_LINK}"' in markup


def test_report_a_problem_in_a_card_opens_its_window(commands_free_help, monkeypatch):
    from gi.repository import Gtk
    from gtk_text import widgets
    from tuxtalk import help_dialog
    opened = []
    monkeypatch.setattr(help_dialog, "_open_report", lambda widget: opened.append(widget))
    page = _pages(commands_free_help)[help_text.PROBLEMS_TITLE]
    label = next(w for w in widgets(page) if isinstance(w, Gtk.Label)
                 and help_text.REPORT_LINK in w.get_label())
    assert label.emit("activate-link", help_text.REPORT_LINK) is True
    assert opened == [label]
