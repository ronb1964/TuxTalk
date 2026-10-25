"""What the tabs of Preferences share: the window they belong to, the amber
of the warnings, the window a download or install runs in
(run_with_progress), and the pieces every tab is built from (section
headings, folds, dropdowns)."""
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from .logger import setup_logger
from .ui_style import Fold, forward_combo_scroll

logger = setup_logger(__name__)

# How far a Labs feature's options sit in under its heading.
LABS_INDENT = 20


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
    gives it the keyboard focus, and the mouse wheel over it scrolls the page
    instead of changing the choice (ui_style.forward_combo_scroll)."""
    combo.set_can_focus(True)
    combo.connect("button-press-event", _focus_on_press)
    forward_combo_scroll(combo)
    return combo


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


def _progress_window(parent, title, first_line):
    """A window for a download or install, which can't be closed while it
    works: (window, status line, progress bar), shown."""
    dialog = Gtk.Dialog(title=title, transient_for=parent, flags=Gtk.DialogFlags.MODAL)
    dialog.set_default_size(400, 120)
    dialog.set_deletable(False)
    content = dialog.get_content_area()
    content.set_spacing(10)
    content.set_margin_start(20)
    content.set_margin_end(20)
    content.set_margin_top(20)
    content.set_margin_bottom(10)
    status_label = Gtk.Label(label=first_line)
    status_label.set_halign(Gtk.Align.START)
    content.pack_start(status_label, False, False, 0)
    progress_bar = Gtk.ProgressBar()
    progress_bar.set_show_text(True)
    content.pack_start(progress_bar, False, False, 10)
    dialog.show_all()
    return dialog, status_label, progress_bar


def run_with_progress(parent, title, first_line, work, finish):
    """Run *work(progress)* on its own thread with a progress window over
    *parent*, then close the window and call *finish(what work returned)*
    on the GTK thread (None if work raised). work may call
    progress(message, percent) as often as it likes."""
    dialog, status_label, progress_bar = _progress_window(parent, title, first_line)

    def progress(message, percent):
        def update_ui():
            status_label.set_text(message)
            progress_bar.set_fraction(percent / 100.0)
            progress_bar.set_text(f"{percent}%")
            return False
        GLib.idle_add(update_ui)

    def done(result):
        dialog.destroy()
        finish(result)
        return False

    def run():
        try:
            result = work(progress)
        except Exception:
            logger.exception(f"{title} failed")
            result = None
        GLib.idle_add(done, result)

    threading.Thread(target=run, daemon=True).start()


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
