"""The Help window offers a feedback link, and never asks for feedback itself.

Nothing in TuxTalk pops up asking for feedback, but a link people can go
looking for in Help is welcome. The link sits under the sections so it's there
whichever section is open.
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


def _notebook(dialog):
    return next(w for w in widgets(dialog) if isinstance(w, Gtk.Notebook))


def _titles(notebook):
    return [notebook.get_tab_label_text(notebook.get_nth_page(n))
            for n in range(notebook.get_n_pages())]


def _title(notebook):
    return notebook.get_tab_label_text(notebook.get_nth_page(notebook.get_current_page()))


def test_every_section_is_a_tab_in_order_and_opens(dialog):
    """Sections are tabs along the top, as in Preferences: each one, in
    Help's order with Start Here first and open, and choosing it shows its
    page."""
    from tuxtalk import help_text
    notebook = _notebook(dialog)
    titles = [title for title, _ in help_text.tabs()]
    assert _titles(notebook) == titles and titles[0] == "Start Here"
    assert _title(notebook) == "Start Here"
    for number, title in enumerate(titles):
        notebook.set_current_page(number)
        assert _title(notebook) == title


def test_help_has_the_look_of_preferences(dialog):
    """The two windows match: Help carries the class Preferences' stylesheet
    is scoped to, and has its title above the tabs."""
    from tuxtalk import ui_style
    assert dialog.get_style_context().has_class(ui_style.STYLE_CLASS)
    assert any(isinstance(w, Gtk.Label) and w.get_text() == "TuxTalk Help"
               for w in widgets(dialog))


@pytest.fixture
def shown_help(monkeypatch):
    """Help as it opens on the (hidden) test display, on the section asked
    for, ready for key presses."""
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    opened = []

    def show(tab=None):
        opened.append(help_dialog.show_help_dialog(tab=tab))
        _settle()
        return opened[-1]
    yield show
    for dialog in opened:
        dialog.destroy()


def _settle():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def _press(dialog, keyval, state=0):
    """Press and release *keyval* in *dialog*, as the keyboard would: GTK
    hands it to the widget with the focus."""
    from gi.repository import Gdk
    seat = Gdk.Display.get_default().get_default_seat()
    keymap = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
    code = keymap.get_entries_for_keyval(keyval)[1][0].keycode
    for kind in (Gdk.EventType.KEY_PRESS, Gdk.EventType.KEY_RELEASE):
        event = Gdk.Event.new(kind)
        event.window, event.send_event = dialog.get_window(), True
        event.keyval, event.state, event.hardware_keycode = keyval, state, code
        event.set_device(seat.get_keyboard())
        Gtk.main_do_event(event)
        _settle()


def _page_text(notebook):
    page = notebook.get_nth_page(notebook.get_current_page())
    label = next(w for w in widgets(page) if isinstance(w, Gtk.Label))
    return label, page.get_vadjustment()


def test_tab_from_the_tabs_goes_into_the_open_sections_text(shown_help):
    """On the tabs the arrows change section; Tab then goes into that
    section's text, and Shift+Tab back to the tabs, on the same section."""
    from gi.repository import Gdk
    dialog = shown_help()
    notebook = _notebook(dialog)
    assert dialog.get_focus() is notebook
    _press(dialog, Gdk.KEY_Right)
    _press(dialog, Gdk.KEY_Right)
    assert _title(notebook) == "Writing"
    _press(dialog, Gdk.KEY_Tab)
    label, _ = _page_text(notebook)
    assert _title(notebook) == "Writing"
    assert dialog.get_focus() is label
    assert not label.get_selection_bounds()[0], "focusing it selected the whole text"
    _press(dialog, Gdk.KEY_ISO_Left_Tab, Gdk.ModifierType.SHIFT_MASK)
    assert dialog.get_focus() is notebook and _title(notebook) == "Writing"


def test_the_keys_scroll_the_text(shown_help):
    """In the text, Page Down and Down scroll it, End and Home go to either
    end, and the keyboard stays in the text."""
    from gi.repository import Gdk
    dialog = shown_help("writing")
    notebook = _notebook(dialog)
    _press(dialog, Gdk.KEY_Tab)
    label, scroll = _page_text(notebook)
    start = scroll.get_value()
    _press(dialog, Gdk.KEY_Page_Down)
    paged = scroll.get_value()
    assert paged > start
    _press(dialog, Gdk.KEY_Down)
    assert scroll.get_value() > paged
    _press(dialog, Gdk.KEY_End)
    assert scroll.get_value() == pytest.approx(scroll.get_upper() - scroll.get_page_size())
    _press(dialog, Gdk.KEY_Home)
    assert scroll.get_value() == scroll.get_lower()
    assert dialog.get_focus() is label


def test_help_opens_on_the_section_asked_for_with_the_keyboard_there(shown_help):
    """Opened on a section (Preferences' Labs link opens Writing): it shows,
    and the keyboard (and a screen reader) is on the tabs, not on a link."""
    dialog = shown_help("tips")
    notebook = _notebook(dialog)
    assert _title(notebook) == "Tips"
    assert dialog.get_focus() is notebook


def test_a_screen_reader_hears_the_tabs_named(dialog):
    assert _notebook(dialog).get_accessible().get_name() == "Help sections"


def test_every_tab_shows_on_a_normal_screen(monkeypatch, shown_help):
    """On a screen with room, all the tabs are in view at once: no arrows."""
    from tuxtalk import ui_style
    monkeypatch.setattr(ui_style, "workarea_width", lambda: 1920)
    dialog = shown_help()
    notebook = _notebook(dialog)
    assert not notebook.get_scrollable()
    assert dialog.get_allocated_width() >= notebook.get_preferred_width()[0]


def test_help_fits_a_narrow_screen(monkeypatch, shown_help):
    """With large text or a small screen, Help opens no wider than the
    screen, its lines wrapping and its tabs scrolling, instead of running
    off its edge: the window as shown, not only the size it asked for."""
    from tuxtalk import ui_style
    monkeypatch.setattr(ui_style, "workarea_width", lambda: 640)
    dialog = shown_help()
    assert dialog.get_allocated_width() <= 640 - 40
    assert _notebook(dialog).get_scrollable()


def test_the_screen_width_is_the_narrowest_monitors(monkeypatch):
    """A portrait screen (600 wide, 1600 tall) beside a landscape one: the
    width came from the shortest monitor, the landscape one, and Help could
    open 800 px wide on the 600 px screen."""
    from types import SimpleNamespace as Fake
    from tuxtalk import ui_style

    def monitor(width, height):
        area = Fake(width=width, height=height)
        return Fake(get_workarea=lambda: area, get_geometry=lambda: area)
    monitors = [monitor(1920, 1080), monitor(600, 1600)]
    display = Fake(get_n_monitors=lambda: 2, get_monitor=monitors.__getitem__)
    monkeypatch.setattr(ui_style.Gdk.Display, "get_default", lambda: display)
    assert ui_style.workarea_width() == 600
