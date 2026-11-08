"""Every Help tab shows its topics as cards, like the Commands tab.

Help's text marks where each card starts (help_outline reads the marks);
these tests pin the marks being read right, every tab using them, and the
cards a keyboard and a screen reader can reach.
"""
import re

import pytest

from tuxtalk import help_outline, help_pages, help_text

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
Hold F8.

## Transcribe a File (TuxTalk menu → Transcribe a File...)

### Fish &amp; chips
Both."""


def test_the_marks_make_a_title_parts_and_cards():
    page = help_outline.parse(SAMPLE)
    assert page.title == "Dictating"
    first, editing, transcribe = page.parts
    assert first.title == "" and first.intro == "Before any card."
    assert [s.title for s in first.sections] == ["Without holding a key", "Performance presets"]
    assert first.sections[0].body == "Tap F9 to start. Tap it again to stop."
    assert first.sections[1].note == "(TuxTalk menu → Performance)"
    assert editing.title == "Voice Editing" and editing.labs
    assert editing.intro == "Change text by saying how."
    assert [(s.title, s.labs) for s in editing.sections] == [
        ("How to use it", False), ("Changing it before it's sent", True)]
    assert (transcribe.title, transcribe.note) == ("Transcribe a File",
                                                   "(TuxTalk menu → Transcribe a File...)")


def test_a_heading_is_plain_text_so_it_is_escaped_once():
    """Headings come from the markup, where "&" is "&amp;"; the card shows
    "Fish & chips", not "Fish &amp; chips"."""
    assert help_outline.parse(SAMPLE).parts[2].sections[0].title == "Fish & chips"


def test_lines_broken_by_hand_flow_on_and_each_item_starts_its_own():
    """Help's text is broken at about 85 characters in the source; in a card
    as wide as the window that left part of it empty. A paragraph and a
    list's item both fill the card; an item and a step start a line."""
    page = help_outline.parse("""# T

### Taking it back
Each of these is a dictation of its own, said right
after the one it takes back.
  • <b>delete last word</b> and <b>delete last sentence</b>
    remove what you just dictated.
  • Ctrl+Z works too.
  1. Click into the first box.
     – <b>.deb:</b> the package

A new paragraph,
broken by hand.""")
    assert page.parts[0].sections[0].body == (
        "Each of these is a dictation of its own, said right after the one it takes back.\n"
        "  • <b>delete last word</b> and <b>delete last sentence</b> remove what you just "
        "dictated.\n"
        "  • Ctrl+Z works too.\n"
        "  1. Click into the first box.\n"
        "     – <b>.deb:</b> the package\n"
        "\n"
        "A new paragraph, broken by hand.")


def _prose_tabs():
    return [(title, text) for title, text in help_text.tabs()
            if title != help_text.COMMANDS_TITLE]


@pytest.mark.parametrize("title", [t for t, _ in _prose_tabs()])
def test_every_tab_is_cards(title):
    page = help_outline.parse(dict(_prose_tabs())[title])
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


def test_the_commands_text_is_only_a_title_and_an_intro():
    """Its cards are the commands: a card written in its text would never
    show."""
    commands = help_outline.parse(dict(help_text.tabs())[help_text.COMMANDS_TITLE])
    (part,) = commands.parts
    assert commands.title and part.intro
    assert (part.title, part.note, part.labs, part.sections) == ("", "", False, ())


@pytest.fixture
def help_window(monkeypatch):
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


def _card_texts(page):
    from gi.repository import Gtk
    from gtk_text import widgets
    return [w for w in widgets(page) if isinstance(w, Gtk.Label)
            and w.get_style_context().has_class("tt-card-text")]


def test_each_card_shows_its_heading_in_a_band_in_the_tabs_colour(help_window):
    from gtk_text import widgets
    for title, page in _pages(help_window).items():
        if title == help_text.COMMANDS_TITLE:
            continue
        heads = [w for w in widgets(page) if w.get_style_context().has_class("tt-card-head")]
        assert heads, title
        look = help_pages.TAB_LOOK[title][0]
        assert all(h.get_style_context().has_class(f"tt-{look}") for h in heads), title


def _named_by(label):
    """The texts a screen reader reads before *label*: its labelled-by."""
    from gi.repository import Atk
    relation = label.get_accessible().ref_relation_set().get_relation_by_type(
        Atk.RelationType.LABELLED_BY)
    return [target.get_name() for target in relation.get_target()] if relation else []


def test_a_screen_reader_hears_each_cards_heading_before_its_text(help_window):
    """Tab reaches each card's text (it can be read, selected and copied),
    and the feature's title and the card's heading name it: Writing has
    three cards called "Good to know"."""
    texts = _card_texts(_pages(help_window)["Writing"])
    assert all(t.get_can_focus() and t.get_selectable() for t in texts)
    names = [_named_by(t) for t in texts]
    assert ["Filling in a form"] in names
    assert ["Voice Editing", "Good to know"] in names
    assert ["Select and Move by Voice", "Good to know"] in names
    assert ["Apps, Windows, Keys and the Mouse by Voice", "Good to know"] in names


def test_a_screen_reader_hears_the_feature_before_its_opening_lines(help_window):
    """The lines under a feature's title take the focus too."""
    from gi.repository import Gtk
    from gtk_text import widgets
    page = _pages(help_window)["Writing"]
    intro = next(w for w in widgets(page) if isinstance(w, Gtk.Label)
                 and w.get_text().startswith("Change text by saying how you want it changed"))
    assert _named_by(intro)[0] == "Voice Editing"


def test_a_heading_note_is_read_with_the_heading(help_window):
    texts = _card_texts(_pages(help_window)[help_text.PROBLEMS_TITLE])
    assert ["Performance presets", "(TuxTalk menu → Performance)"] in [_named_by(t)
                                                                       for t in texts]


def test_problems_has_its_links(help_window):
    from gi.repository import Gtk
    from gtk_text import widgets
    page = _pages(help_window)[help_text.PROBLEMS_TITLE]
    markup = " ".join(w.get_label() for w in widgets(page) if isinstance(w, Gtk.Label))
    assert f'href="{help_text.ISSUES_URL}"' in markup
    assert f'href="{help_text.REPORT_LINK}"' in markup


def test_report_a_problem_in_a_card_opens_its_window(help_window, monkeypatch):
    from gi.repository import Gtk
    from gtk_text import widgets
    from tuxtalk import help_dialog
    opened = []
    monkeypatch.setattr(help_dialog, "_open_report", lambda widget: opened.append(widget))
    page = _pages(help_window)[help_text.PROBLEMS_TITLE]
    label = next(w for w in widgets(page) if isinstance(w, Gtk.Label)
                 and help_text.REPORT_LINK in w.get_label())
    assert label.emit("activate-link", help_text.REPORT_LINK) is True
    assert opened == [label]


def test_a_web_link_is_left_to_gtk_to_open(monkeypatch):
    """Help handles only its own Report a problem link; any other is a web
    page, which GTK opens. (The handler is called directly: emitting the
    signal would open the browser.)"""
    from tuxtalk import help_dialog
    monkeypatch.setattr(help_dialog, "_open_report", lambda widget: pytest.fail("opened"))
    assert help_dialog._on_link(None, help_text.ISSUES_URL) is False
