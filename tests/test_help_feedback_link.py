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


def test_the_link_is_outside_the_tabs_so_every_tab_shows_it(dialog):
    link = next(w for w in widgets(dialog) if isinstance(w, Gtk.LinkButton))
    notebook = next(w for w in widgets(dialog) if isinstance(w, Gtk.Notebook))
    assert link not in list(widgets(notebook))


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


@pytest.mark.parametrize("screen, arrows", [(4000, False), (300, True)])
def test_every_tab_shows_without_scroll_arrows_when_the_screen_allows(monkeypatch, screen,
                                                                     arrows):
    """On Mint's wider font the eight tabs needed more than 760 px and GTK
    hid some behind scroll arrows (VM, 2026-10-22). Help is as wide as its
    tabs need; only a screen too narrow for that keeps the arrows."""
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    from tuxtalk import ui_style
    monkeypatch.setattr(ui_style, "workarea_width", lambda: screen)
    shown = []
    monkeypatch.setattr(Gtk.Dialog, "show_all", lambda self: shown.append(self))
    monkeypatch.setattr(Gtk.Dialog, "present", lambda self: None)
    help_dialog.show_help_dialog()
    dialog = shown[0]
    try:
        notebook = next(w for w in widgets(dialog) if isinstance(w, Gtk.Notebook))
        assert notebook.get_scrollable() is arrows
        if not arrows:
            assert dialog.get_default_size()[0] >= dialog.get_preferred_width()[0]
    finally:
        dialog.destroy()
