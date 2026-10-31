"""prefs_style.css must actually reach the widgets it styles.

In GTK3 a provider attached to a *window's* style context does not reach that
window's children — measured with a real widget: a child button kept the theme
default (50,50,50) under a window-scoped provider that set it red, and took the
colour correctly from a screen-wide one. Window-scoped, every rule in the file
except `window` silently does nothing.

So two things are required, and BOTH matter:

  1. install the provider screen-wide, so it reaches child widgets at all
  2. scope every selector to .talktype-prefs, so it styles only the windows
     that carry the class (Preferences and Help, ui_style.use_tuxtalk_style)
     and no other GTK window in the process

Half of this is worse than none: screen-wide with unscoped selectors would
repaint unrelated windows.
"""

import pathlib

import pytest
from conftest import unscoped_selectors

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS = ROOT / "src/tuxtalk/prefs_style.css"

SCOPE_CLASS = "talktype-prefs"


def test_every_selector_is_scoped_to_the_prefs_window():
    """An unscoped selector would repaint unrelated windows in this process."""
    unscoped = unscoped_selectors(CSS.read_text(), SCOPE_CLASS)
    assert not unscoped, (
        f"These selectors are not scoped to .{SCOPE_CLASS}, so once the "
        f"provider is installed screen-wide they would restyle every GTK "
        f"window in the process: {unscoped}"
    )


@pytest.mark.parametrize("css, unscoped", [
    (".talktype-prefs label, button { color: red; }", ["button"]),
    ("* { color: red; }", ["*"]),
    (".talktype-prefs-old label { color: red; }", [".talktype-prefs-old label"]),
    ("window:not(.talktype-prefs) { color: red; }", ["window:not(.talktype-prefs)"]),
    ("window.talktype-prefs { color: red; }", []),
    ("window.talktype-prefs:not(.dialog) label { color: red; }", []),
])
def test_the_scope_check_catches_each_unscoped_selector(css, unscoped):
    assert unscoped_selectors(css, SCOPE_CLASS) == unscoped


def test_stylesheet_reaches_the_widgets_inside_the_window(prefs_window):
    """A window-scoped provider never reaches child widgets: a label would
    keep the theme's colour instead of the sheet's #d4d4d4."""
    from gi.repository import Gtk
    from gtk_text import find
    label = find(prefs_window().window, Gtk.Label, "Paste Last Dictation:")
    color = label.get_style_context().get_color(Gtk.StateFlags.NORMAL)
    assert [round(c * 255) for c in (color.red, color.green, color.blue)] == [0xd4] * 3


def test_prefs_window_carries_the_scope_class(prefs_window):
    """Without the class, every scoped selector matches nothing."""
    assert prefs_window().window.get_style_context().has_class(SCOPE_CLASS)


def test_stylesheet_parses():
    """A syntax error would silently drop the rest of the file."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    provider = Gtk.CssProvider()
    try:
        provider.load_from_path(str(CSS))
    except Exception as exc:  # GLib.Error
        pytest.fail(f"prefs_style.css does not parse: {exc}")


def test_every_tab_has_an_outline_not_only_the_open_one(prefs_window):
    """Unselected tabs drew no edge at all, so on Breeze dark the row read
    as words run together, not tabs. GTK 3's tabs are CSS nodes, not
    widgets, so the style is read for the node path notebook > header >
    tabs > tab."""
    from gi.repository import Gtk
    notebook = prefs_window().notebook
    path = notebook.get_path().copy()
    for node in ("header", "tabs", "tab"):
        path.iter_set_object_name(path.append_type(Gtk.Widget), node)
    tab = Gtk.StyleContext()
    tab.set_screen(notebook.get_screen())
    tab.set_path(path)
    edge = tab.get_border(Gtk.StateFlags.NORMAL)
    assert edge.left >= 1 and edge.right >= 1 and edge.top >= 1
