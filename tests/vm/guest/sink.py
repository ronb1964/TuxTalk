#!/usr/bin/python3
"""A plain text window for TuxTalk to type into during the VM release tests.

Every change to its text is written straight to a file, so the harness can
read exactly what was typed, Enter presses included, without screenshots.
SIGUSR1 empties it between checks (its pid is in OUTPUT_FILE.pid). Runs
with the guest's own python3 and GTK 3. With "password" it is one password
box instead, which TuxTalk must leave empty.

Two more modes for the voice-command checks. "long" is a tall text, scrolled
to the top, whose vertical position is written to OUTPUT_FILE.scroll on every
change (the file holds the typed text only: empty while nothing was added to
the filler). "ask" answers a close with a "Save changes?" question and stays
open, as an editor with unsaved work does.

Usage: sink.py OUTPUT_FILE [TITLE] [password|long|ask]
"""
import os
import signal
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")   # Fedora 44 would load Gdk 4 otherwise
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


# Enough lines to scroll on any screen the VMs have.
FILLER = "".join(f"line {n}\n" for n in range(1, 401))


def ask_to_save(window, _event):
    """Answer a close with a question and keep the window, like an editor
    holding unsaved work. True: the close is not carried out."""
    dialog = Gtk.MessageDialog(transient_for=window, modal=True,
                               message_type=Gtk.MessageType.QUESTION,
                               text="Save changes?")
    dialog.set_title("Save changes?")
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.connect("response", lambda d, _r: d.destroy())
    dialog.show()
    return True


def main():
    out_path = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) > 2 else "TT Sink"
    mode = sys.argv[3] if len(sys.argv) > 3 else None
    password = mode == "password"
    # Its own app id per file: TuxTalk tells windows apart by app, so two
    # test windows must look like two apps for a window switch to count. On
    # X11 that's the WM_CLASS class, which GTK takes from the script's name
    # ("Sink.py") unless it's set too.
    app_id = "tt-" + os.path.splitext(os.path.basename(out_path))[0]
    GLib.set_prgname(app_id)
    Gdk.set_program_class(app_id)

    def save_scroll(adjustment):
        with open(out_path + ".scroll", "w", encoding="utf-8") as f:
            f.write(str(adjustment.get_value()))

    window = Gtk.Window(title=title)
    window.set_default_size(1000, 700)
    if password:
        # Filling the window, so the harness's click in the middle lands in it.
        view = Gtk.Entry(visibility=False)
        window.add(view)
        changes, text = view, view.get_text
    else:
        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD)
        scroller = Gtk.ScrolledWindow()
        scroller.add(view)
        window.add(scroller)
        changes = view.get_buffer()
        if mode == "long":
            changes.set_text(FILLER)
            scroller.get_vadjustment().connect("value-changed", save_scroll)
            save_scroll(scroller.get_vadjustment())
            text = lambda: ("" if (t := changes.get_text(  # noqa: E731
                *changes.get_bounds(), True)) == FILLER else t)
        else:
            text = lambda: changes.get_text(*changes.get_bounds(), True)  # noqa: E731

    def save(*_):
        tmp = out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text())
        os.replace(tmp, out_path)

    def clear():
        changes.set_text(FILLER if mode == "long" else "")
        return GLib.SOURCE_CONTINUE

    with open(out_path + ".pid", "w") as f:
        f.write(str(os.getpid()))
    changes.connect("changed", save)
    save()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, clear)
    if mode == "ask":
        window.connect("delete-event", ask_to_save)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    window.maximize()
    view.grab_focus()
    Gtk.main()


if __name__ == "__main__":
    main()
