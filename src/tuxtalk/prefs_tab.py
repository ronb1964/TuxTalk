"""What the tabs of Preferences share: the window they belong to, the amber
of the warnings, whether the GNOME extension applies here, and the window a
download or install shows its progress in."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

# The amber of TuxTalk's warnings (an unsafe setting, a backup never saved).
WARNING_AMBER = "#e6aa3c"


def _on_gnome():
    """Whether the GNOME Shell extension can apply on this desktop at all."""
    try:
        from .desktop_detect import is_gnome
        return is_gnome()
    except Exception:
        return True   # unsure: show the GNOME options rather than hide them


def progress_window(parent, title, first_line):
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


class Tab:
    """One tab of Preferences, or a window of settings a tab opens. It builds
    its page (build) and handles its own settings, reading and changing them
    through the window (*win*, a prefs.PreferencesWindow), which saves and
    applies them for every tab."""

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
