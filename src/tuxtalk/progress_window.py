"""The window a download or install runs in, for Preferences and the tray:
the work runs on a thread of its own, a progress bar shows how far it got,
and the window can't be closed while it works."""
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .logger import setup_logger  # noqa: E402

logger = setup_logger(__name__)


def _progress_window(parent, title, first_line):
    """A window for a download or install, which can't be closed while it
    works: (window, status line, progress bar), shown."""
    dialog = Gtk.Dialog(title=title, transient_for=parent, flags=Gtk.DialogFlags.MODAL)
    dialog.set_default_size(400, 120)
    dialog.set_deletable(False)
    if parent is None:              # from the tray: over the other windows, centred
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.set_keep_above(True)
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
    *parent* (None: from the tray), then close the window and call
    *finish(what work returned)* on the GTK thread (None if work raised).
    work may call progress(message, percent) as often as it likes."""
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

    threading.Thread(target=run, daemon=True, name=title).start()
