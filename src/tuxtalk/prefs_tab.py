"""What the tabs of Preferences share: the window they belong to, the amber
of the warnings, and the pieces every tab is built from (section headings,
folds, dropdowns). The window a download or install runs in is
progress_window.run_with_progress, shared with the tray."""
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from .ui_style import Fold, forward_combo_scroll

# How far a Labs feature's options sit in under its heading.
LABS_INDENT = 20

# The keys a closed GTK dropdown answers by picking another entry at once.
_PICKING_KEYS = {getattr(Gdk, f"KEY_{name}") for name in (
    "Up", "Down", "Page_Up", "Page_Down", "Home", "End",
    "KP_Up", "KP_Down", "KP_Page_Up", "KP_Page_Down", "KP_Home", "KP_End")}


def section_header(grid, row, title):
    """A divider line and a bold section title (plain text) across *grid*
    at *row*; returns the next free row."""
    sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    sep.set_margin_top(20)
    sep.set_margin_bottom(15)
    grid.attach(sep, 0, row, 2, 1)
    header = Gtk.Label()
    header.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
    header.set_xalign(0)
    header.set_margin_bottom(10)
    grid.attach(header, 0, row + 1, 2, 1)
    return row + 2


def fold(grid, row, show_label, hide_label):
    """A closed Fold across *grid* at *row*, for settings most people never
    need; fill its .content. Opened, it scrolls its settings into view."""
    def toggled(opening):
        if opening:
            # After the slide-down, so its full height is known.
            scroll_into_view(folded.revealer, folded.revealer.get_transition_duration() + 60)
    folded = Fold(show_label, hide_label, on_toggle=toggled)
    folded.set_margin_top(6)
    grid.attach(folded, 0, row, 2, 1)
    return folded


def scroll_into_view(widget, delay_ms=60):
    """Scroll the tab so *widget*, just shown, is in view, once GTK has laid
    it out (after *delay_ms*, longer for a sliding reveal). Ticking a box
    whose settings appear below the window's edge looked like nothing had
    happened."""
    def scroll():
        """Scroll once (a GLib timeout that doesn't repeat)."""
        sw = widget.get_parent()
        while sw is not None and not isinstance(sw, Gtk.ScrolledWindow):
            sw = sw.get_parent()
        if sw is None or not widget.get_mapped():
            return False
        coords = widget.translate_coordinates(sw, 0, 0)
        if coords is None:
            return False
        top = coords[1]
        bottom = top + widget.get_allocated_height()
        adj = sw.get_vadjustment()
        page = adj.get_page_size()
        if bottom > page:
            # Down until its bottom clears the "more below" fade (70 px,
            # prefs_style.css), but never past its top. At a page's end
            # there's no fade: the page just scrolls to the bottom.
            adj.set_value(adj.get_value() + min(bottom - page + 76, max(0, top - 12)))
        elif top < 0:
            adj.set_value(adj.get_value() + top - 12)
        return False
    GLib.timeout_add(delay_ms, scroll)


def dropdown(combo):
    """Set *combo* up as a Preferences dropdown, and return it: a click
    gives it the keyboard focus, the mouse wheel over it scrolls the page
    instead of changing the choice (ui_style.forward_combo_scroll), and the
    arrow keys open its list instead (_open_on_arrow)."""
    combo.set_can_focus(True)
    combo.connect("button-press-event", _focus_on_press)
    combo.connect("key-press-event", _open_on_arrow)
    forward_combo_scroll(combo)
    return combo


def _open_on_arrow(combo, event):
    """An arrow, Page or Home/End key on a closed dropdown opens its list.
    GTK would pick the next entry at once, and some entries start a setup
    or a download the moment they're picked (large-v3, CUDA, Several
    languages), each putting the old choice back: with the keyboard alone
    the entries past them couldn't be reached."""
    if event.keyval not in _PICKING_KEYS or combo.get_property("popup-shown"):
        return False
    combo.popup()
    return True


def _focus_on_press(widget, _event):
    """A clicked dropdown takes the keyboard focus, then opens as usual."""
    if not widget.has_focus():
        widget.grab_focus()
    return False


def subheading(text):
    """A small heading inside a section or fold."""
    label = Gtk.Label(xalign=0)
    label.set_markup(f'<span weight="600">{GLib.markup_escape_text(text)}</span>')
    label.set_margin_top(6)
    return label


class Tab:
    """A tab of Preferences, a section of one, or a window of settings a tab
    opens. It builds its part (build) and handles its own settings, reading
    and changing them through the window (*win*, a prefs.PreferencesWindow),
    which saves and applies them for every tab."""

    def __init__(self, win):
        self.win = win

    @property
    def config(self):
        """The settings as Preferences has them now, applied or not."""
        return self.win.config

    @property
    def window(self):
        """The Preferences window, for the dialogs a tab opens over it."""
        return self.win.window

    def update_config(self, key, value):
        """Change a setting; Apply or OK saves it."""
        self.win.update_config(key, value)

    def stop(self, closing=False):
        """Stop what this tab runs that holds the microphone (a test), as
        another tab is shown, the focus goes to another window, or the window
        closes (*closing*). Nothing, for most tabs."""
