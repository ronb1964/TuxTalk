"""A small window that explains how a feature works, for the problems that
are really "here's the rule" rather than errors: "select that" said in the
wrong place, voice editing in a terminal, bold in a plain-text app.

The floating label is glanced at, and the notification is gone in seconds,
too quickly to read a whole explanation. So each time one of these comes
up, this window opens over the app and stays until "Got it": someone who
makes the same mistake twice should be told twice. Ticking "Don't
explain this again" leaves that one at the short floating label for good
(explained.json, next to the settings), until Preferences' "Show tips
again" brings them all back.

It runs as its own process, like Preferences, and opens centred over the
window being dictated into (window_geometry, as the floating label does),
so on a big screen or a second monitor it's where the user is looking.
Wayland lets no app place its own windows, so it runs on XWayland like the
recording indicator; it has no dropdowns, the one thing XWayland breaks
(see CLAUDE.md, GDK_BACKEND). With no XWayland it falls back to Wayland,
centred on the screen.
"""
import json
import os
import subprocess
import sys

from .logger import setup_logger

logger = setup_logger(__name__)

# kind -> (title, explanation). The kinds are edit_notices.NOTICES'.
EXPLANATIONS = {
    "nothing_said": (
        "Select works right after you dictate",
        "“Select that” and “select the last sentence” work on what you "
        "just said, right after you dictate it. TuxTalk can't see what's in your "
        "document otherwise.\n\n"
        "For other text, say “select the previous 3 words” or “select the "
        "line”, or use the mouse."),
    "undo_other_window": (
        "Delete works right after you dictate",
        "“Delete last word” and “delete last sentence” remove what you "
        "just said, right after you dictate it. TuxTalk can't see what's in your "
        "document otherwise, so it didn't delete anything here.\n\n"
        "To undo something else, press Ctrl+Z in the app."),
    "nav_terminal": (
        "Select and move don't work in a terminal",
        "In a terminal, keys like Ctrl+A and Shift+arrows do other things (Ctrl+A "
        "jumps to the start of the line), so TuxTalk doesn't press them there.\n\n"
        "Use select and move in text editors, word processors, browsers and chat boxes."),
    "terminal": (
        "Voice editing doesn't work in a terminal",
        "A terminal has no selected text to replace, and Ctrl+C there would stop "
        "the running program.\n\n"
        "Use voice editing in text editors, word processors, browsers and chat boxes."),
    "no_selection": (
        "Select the text first",
        "Voice editing changes the text you've selected. Select it first, then hold "
        "Shift with your dictation key and say how to change it, like “make this "
        "more formal”."),
    "no_shortcuts": (
        "No bold or italics here",
        "Bold, italics and underline need a word processor like LibreOffice Writer, "
        "where TuxTalk presses Ctrl+B, Ctrl+I or Ctrl+U for you. Plain-text apps like "
        "KWrite, Kate and most chat boxes have no formatting to apply."),
    "cant_format": (
        "Voice editing changes words, not looks",
        "It can reword, shorten, fix the grammar or change capitals, but it can't "
        "change the font, size or color. Use the app's own menus for those."),
    "needs_wayland": (
        "Voice editing needs Wayland",
        "Voice editing reads the selected text through the Wayland clipboard, which an "
        "X11 session doesn't have. Log in to a Wayland session to use it. Dictation "
        "itself works the same on X11."),
}

# explained.json, next to the settings: {"never": [the kinds the user ticked
# "Don't explain this again" for]}.

_window = None      # the explanation window's process, while one is open


def _never_path():
    from .config import CONFIG_PATH
    return os.path.join(os.path.dirname(CONFIG_PATH), "explained.json")


def _never_again():
    try:
        with open(_never_path()) as f:
            return set(json.load(f).get("never", []))
    except (OSError, ValueError, AttributeError, TypeError):
        return set()


def never_again(kind):
    """Remember that *kind* needs no more explaining (the tickbox)."""
    kinds = _never_again() | {kind}
    try:
        with open(_never_path(), "w") as f:
            json.dump({"never": sorted(kinds)}, f)
    except OSError as e:
        logger.debug(f"Could not save the explained kinds: {e}")


def reset():
    """Preferences' "Show tips again": every explanation comes back."""
    try:
        os.remove(_never_path())
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.debug(f"Could not reset the explained kinds: {e}")


def should_explain(kind) -> bool:
    """Whether *kind* gets the window: it has one, and the user didn't tick
    "Don't explain this again" for it."""
    return kind in EXPLANATIONS and kind not in _never_again()


def window_open() -> bool:
    """Whether an explanation window is on screen already (no stacking)."""
    return _window is not None and _window.poll() is None


def start_helper_window(module, *args):
    """Start TuxTalk's *module* as its own process for a small window that
    opens over the app being dictated into: on XWayland first, where it may
    be placed (plain Wayland if there's none, centred on the screen). Shared
    by these explanations and "fix that word". Returns the Popen."""
    from .service_launcher import SERVICE_GDK_BACKEND
    env = os.environ.copy()
    env["GDK_BACKEND"] = SERVICE_GDK_BACKEND
    return subprocess.Popen([sys.executable, "-m", module, *args], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def open_window(kind) -> bool:
    """Open the explanation for *kind* in its own process. True if it was
    started; never raises."""
    global _window
    try:
        _window = start_helper_window("tuxtalk.explain", kind)
        logger.info(f"Explained: {kind}")
        return True
    except Exception as e:
        logger.warning(f"Could not open the explanation: {e}")
        return False


def build_dialog(kind):
    """The window for *kind*: (dialog, the "don't explain again" tickbox)."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from .ui_style import message_dialog

    title, text = EXPLANATIONS[kind]
    dialog = message_dialog(message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.NONE,
                            text=title)
    dialog.format_secondary_text(text)
    check = Gtk.CheckButton(label="Don't explain this again")
    check.set_margin_start(12)
    dialog.get_message_area().pack_start(check, False, False, 6)
    dialog.add_button("Got it", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.set_keep_above(True)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    return dialog, check


def position_over(size, center, monitor):
    """Top-left corner that centres a window of *size* (w, h) on *center*,
    kept on *monitor* (a window_geometry.Rect)."""
    from .screen_message import place
    return place(size[0], size[1], center, monitor)


def place_over_active_window(dialog):
    """Centre *dialog* (any Gtk.Window) over the window being dictated into,
    before it shows. Leaves it centred on the screen when that window can't
    be found."""
    try:
        from gi.repository import Gdk, Gtk
        from . import window_geometry
        center = window_geometry.active_window_center()
        if center is None:
            return
        display = Gdk.Display.get_default()
        g = (display.get_monitor_at_point(*center) or display.get_monitor(0)).get_geometry()
        _min, natural = dialog.get_preferred_size()
        dialog.set_position(Gtk.WindowPosition.NONE)
        dialog.move(*position_over((natural.width, natural.height), center,
                                   window_geometry.Rect(g.x, g.y, g.width, g.height)))
    except Exception as e:      # it opens centred on the screen instead
        logger.info(f"Could not place the window over the one dictated into: {e}")


def main(argv=None):
    """The explanation window's own process: show the window for the kind
    named on the command line and remember a ticked "Don't explain this
    again". Returns 1 for a kind it doesn't know."""
    argv = sys.argv[1:] if argv is None else argv
    kind = argv[0] if argv else ""
    if kind not in EXPLANATIONS:
        return 1
    try:
        from . import desktop_identity, ui_style
        desktop_identity.apply()        # TuxTalk's icon on the window, not a generic one
        ui_style.prepare_windows()   # emoji that show, buttons clear of panels and docks
    except Exception as e:
        logger.warning(f"Couldn't set up the window's icon, emoji or size: {e}")
    dialog, check = build_dialog(kind)
    # Asked before the window shows, while the app still has the focus.
    place_over_active_window(dialog)
    dialog.show_all()
    dialog.present()
    dialog.run()
    if check.get_active():
        never_again(kind)
    dialog.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
