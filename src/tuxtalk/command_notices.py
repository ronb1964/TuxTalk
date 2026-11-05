"""The notes for a voice command that wasn't done (everyday keys, window
commands, addresses and searches): a short label floated over the window and
a sentence in a notification, each saying what happened and what to do; and
for one that was, the log line and the ready beep."""
from . import beeps, notices
from .logger import setup_logger

logger = setup_logger(__name__)

# kind -> (label, sentence). {app} is filled from say()'s keyword names.
NOTES = {
    "terminal": (
        "Doesn't apply in a terminal",
        "A terminal has no tabs, saving or finding of its own that TuxTalk can press "
        "for you. Use the terminal's own keys."),
    "no_window": (
        "{app} has no open window",
        "{app} is running but has no window to use. Open one, then try again."),
    "not_running": (
        "{app} isn't open",
        "There is no {app} running to do that to. Say \"open {app}\" first."),
    "cant_close_here": (
        "Can't close windows on this desktop",
        "This desktop doesn't let TuxTalk close a window. Close it yourself."),
    "cant_switch_here": (
        "Can't switch windows on this desktop",
        "This desktop doesn't let TuxTalk bring another window forward. Click the "
        "window you want."),
    "cant_size_here": (
        "Can't minimize or maximize here",
        "This desktop doesn't let TuxTalk change a window's size. Use the window's "
        "own buttons."),
    "no_focused_window": (
        "Couldn't tell which window",
        "TuxTalk couldn't tell which window has the focus, so it did nothing. Click "
        "the window and try again."),
    "gnome_relogin": (
        "Log out and back in for this",
        "This needs the newer TuxTalk GNOME extension. Log out and back in once, "
        "then try again."),
    "no_browser": (
        "No web browser is set up",
        "TuxTalk found no web browser to open the address in. Set a default browser "
        "in your desktop's settings."),
    "browser_not_forward": (
        "The browser didn't come forward",
        "The page may have opened behind your other windows. Look for your browser "
        "in the taskbar."),
    "address_not_opened": (
        "Couldn't open that address",
        "TuxTalk couldn't hand the address to your browser. Try typing it there."),
}


def say(kind: str, beeps_on: bool, **names) -> None:
    """Tell the user the command of *kind* (a NOTES key) wasn't done: the
    cancel beep, the label over the window and the sentence in a
    notification. *names* fill the {app} in the texts."""
    label, sentence = NOTES[kind]
    logger.info(f"Voice command not done: {kind}")
    notices.command_refused(label.format(**names), sentence.format(**names), beeps_on)


def outcome(sent, what: str, beeps_on: bool) -> bool:
    """After the voice command *what* sent its keys (keyboard_output.
    send_only_into's *sent*): "Voice command: *what*" and the ready beep
    when they went in, else the note for why not, where Esc ("cancelled")
    has said so already. True when they went in."""
    if sent is True:
        logger.info(f"Voice command: {what}")
        beeps.beep(beeps_on, *beeps.READY_BEEP)
    elif sent != "cancelled":
        notices.edit_problem("focus_moved" if sent == "moved" else "shortcut_failed", beeps_on)
    return sent is True
