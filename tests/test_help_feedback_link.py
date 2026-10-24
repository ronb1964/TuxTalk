"""The Help window offers a feedback link, and never asks for feedback itself.

Nothing in TuxTalk pops up asking for feedback, but a link people can go
looking for in Help is welcome. The link sits under the tabs so it's there
whichever tab is open.
"""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from gtk_text import widgets  # noqa: E402
from tuxtalk import help_dialog  # noqa: E402


@pytest.fixture
def dialog(monkeypatch):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    shown = []
    # Build the window without putting it on screen.
    monkeypatch.setattr(Gtk.Dialog, "show_all", lambda self: shown.append(self))
    monkeypatch.setattr(Gtk.Dialog, "present", lambda self: None)
    help_dialog.show_help_dialog()
    yield shown[0]
    shown[0].destroy()


def test_help_has_a_feedback_link_to_discussions(dialog):
    links = [w for w in widgets(dialog) if isinstance(w, Gtk.LinkButton)]
    assert [l.get_uri() for l in links] == [help_dialog.FEEDBACK_URL]
    assert help_dialog.FEEDBACK_URL == "https://github.com/ronb1964/TuxTalk/discussions"


def test_the_link_is_outside_the_sections_so_every_section_shows_it(dialog):
    link = next(w for w in widgets(dialog) if isinstance(w, Gtk.LinkButton))
    stack = next(w for w in widgets(dialog) if isinstance(w, Gtk.Stack))
    assert link not in list(widgets(stack))


def test_clicking_the_link_does_not_close_help(dialog):
    """Action-area buttons close Help (its response handler destroys it), so
    the link must not be one of them."""
    link = next(w for w in widgets(dialog) if isinstance(w, Gtk.LinkButton))
    assert dialog.get_response_for_widget(link) == Gtk.ResponseType.NONE


def test_troubleshooting_links_can_be_clicked(dialog):
    markup = " ".join(w.get_label() or "" for w in widgets(dialog) if isinstance(w, Gtk.Label))
    assert '<a href="https://github.com/ronb1964/TuxTalk/issues"' in markup
    assert f'<a href="{help_dialog.FEEDBACK_URL}"' in markup


def _help_labels(dialog):
    return [w for w in widgets(dialog) if isinstance(w, Gtk.Label) and w.get_use_markup()]


def test_every_help_tab_is_valid_markup(dialog):
    """A stray & or < leaves a whole tab blank (GTK drops bad markup)."""
    from gi.repository import Pango
    labels = _help_labels(dialog)
    assert labels
    import re
    for label in labels:
        # GTK labels add <a href> links on top of Pango markup.
        markup = re.sub(r"</?a\b[^>]*>", "", label.get_label())
        Pango.parse_markup(markup, -1, "\0")   # raises on bad markup
        assert label.get_text().strip()


def test_help_lists_every_tray_preset(dialog):
    """Help must name each of today's tray presets."""
    from tuxtalk import recommend
    text = "\n".join(l.get_text() for l in _help_labels(dialog))
    for preset in recommend.presets("en", recommend.Hardware(None)):
        assert f"{preset.label}:" in text, f"Help doesn't mention the {preset.label} preset"


def _sections(dialog):
    """(the section list's rows, the stack of pages)."""
    stack = next(w for w in widgets(dialog) if isinstance(w, Gtk.Stack))
    rows = [w for w in widgets(dialog) if isinstance(w, Gtk.ListBoxRow)]
    return rows, stack


def test_every_section_is_listed_in_order_and_opens_from_the_list(dialog):
    """Sections are a list down the left, not tabs: each one is in it, in
    Help's order with Start Here first and open, and choosing it (with the
    mouse or up and down) shows its page."""
    from tuxtalk import help_text
    rows, stack = _sections(dialog)
    titles = [title for title, _ in help_text.tabs()]
    assert [next(w.get_text() for w in widgets(row) if isinstance(w, Gtk.Label))
            for row in rows] == titles
    assert titles[0] == "🚀 Start Here"
    assert stack.child_get_property(stack.get_visible_child(), "title") == titles[0]
    for row, title in zip(rows, titles):
        row.get_parent().select_row(row)
        assert stack.child_get_property(stack.get_visible_child(), "title") == title


def test_a_page_takes_the_focus_for_the_keyboard_and_a_screen_reader(dialog):
    """Tab from the list goes into the page, so Page Up and Down scroll it;
    focusing it doesn't leave the whole text selected."""
    rows, stack = _sections(dialog)
    assert all(row.get_can_focus() for row in rows)
    label = next(w for w in widgets(stack.get_visible_child()) if isinstance(w, Gtk.Label))
    assert label.get_can_focus() and label.get_selectable()
    label.grab_focus()
    assert not label.get_selection_bounds()[0]
    sidebar = next(w for w in widgets(dialog) if isinstance(w, Gtk.StackSidebar))
    assert sidebar.get_accessible().get_name() == "Help sections"


def test_help_opens_on_the_section_asked_for():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    opened = help_dialog.show_help_dialog(tab="tips")
    try:
        stack = next(w for w in widgets(opened) if isinstance(w, Gtk.Stack))
        assert stack.child_get_property(stack.get_visible_child(), "title") == "💡 Tips"
    finally:
        opened.destroy()


def test_help_is_no_wider_with_another_section(monkeypatch):
    """A row of tabs made Help wider with every one added (Mint's font
    needed more than 760 px for eight). The list down the side doesn't."""
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    from tuxtalk import help_text, ui_style
    monkeypatch.setattr(Gtk.Dialog, "present", lambda self: None)
    # A screen wide enough for any row of tabs, which only a tab row reads.
    monkeypatch.setattr(ui_style, "workarea_width", lambda: 4000, raising=False)
    widths = []
    for extra in (0, 4):
        sections = help_text.tabs() + [(f"Section {n}", "More.") for n in range(extra)]
        monkeypatch.setattr(help_text, "tabs", lambda sections=sections: sections)
        opened = help_dialog.show_help_dialog()
        widths.append(opened.get_default_size()[0])
        opened.destroy()
    assert widths[0] == widths[1] and widths[0] <= 900
