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

"buttons" is for the mouse checks: a 3 x 3 window of buttons A to I. Each
press is a line of OUTPUT_FILE, the button's letter and where it landed in
root pixels ("E 960,540"), with "right " or "middle " before it for those
buttons; a right press also opens a small menu, as a context menu would.
OUTPUT_FILE.screen holds the root window's size ("1920 1080"). It runs on
X11 (XWayland on a Wayland desktop), whose root pixels are the ones
TuxTalk's mouse grid logs: a Wayland app is never told where it is.

Usage: sink.py OUTPUT_FILE [TITLE] [password|long|ask|buttons]
"""
import os
import signal
import sys

# GTK picks its backend when it's imported.
if sys.argv[3:4] == ["buttons"]:
    os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")   # Fedora 44 would load Gdk 4 otherwise
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


# Enough lines to scroll on any screen the VMs have.
FILLER = "".join(f"line {n}\n" for n in range(1, 401))
LETTERS = "ABCDEFGHI"
BUTTON_NAMES = {1: "", 2: "middle ", 3: "right "}


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


def buttons_view(presses, save):
    """The 3 x 3 buttons, each press appended to *presses* and saved; a right
    press pops up a menu."""
    grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True)
    menu = Gtk.Menu()
    menu.append(Gtk.MenuItem(label="Nothing here"))
    menu.show_all()

    def pressed(button, event):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False        # GDK's extra double-click event; each press has its own
        kind = BUTTON_NAMES.get(event.button, f"button{event.button} ")
        presses.append(f"{kind}{button.get_label()} {event.x_root:.0f},{event.y_root:.0f}")
        save()
        if event.button == 3:
            menu.popup_at_pointer(event)
            return True
        return False

    for n, letter in enumerate(LETTERS):
        button = Gtk.Button(label=letter)
        button.connect("button-press-event", pressed)
        grid.attach(button, n % 3, n // 3, 1, 1)
    return grid


def save_screen_size(out_path):
    """The root window's size, in the pixels the presses are logged in."""
    root = Gdk.get_default_root_window()
    with open(out_path + ".screen", "w", encoding="utf-8") as f:
        f.write(f"{root.get_width()} {root.get_height()}")


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
    elif mode == "buttons":
        presses = []
        view = buttons_view(presses, lambda: save())
        window.add(view)
        changes, text = None, lambda: "".join(f"{line}\n" for line in presses)
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
        if mode == "buttons":
            presses.clear()
            save()
        else:
            changes.set_text(FILLER if mode == "long" else "")
        return GLib.SOURCE_CONTINUE

    with open(out_path + ".pid", "w") as f:
        f.write(str(os.getpid()))
    if changes is not None:
        changes.connect("changed", save)
    else:
        save_screen_size(out_path)
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
