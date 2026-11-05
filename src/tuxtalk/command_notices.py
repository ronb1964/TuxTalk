"""The notes for a voice command that wasn't done (everyday keys, window
and desktop commands, addresses and searches): a short label floated over the
window and a sentence in a notification, each saying what happened and what
to do; and for one that was, the log line and the ready beep, with a short
label where the result isn't seen on the screen."""
from . import app_focus, beeps, desktop_detect, notices, screen_message
from .logger import setup_logger

logger = setup_logger(__name__)

# kind -> (label, sentence). {app} and {number} are filled from say()'s keyword
# names.
NOTES = {
    "terminal": (
        "Doesn't apply in a terminal",
        "This key doesn't apply in a terminal. Use the terminal's own keys."),
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
    # GNOME tells which window has the focus only through TuxTalk's extension,
    # which is off.
    "no_focused_window_gnome": (
        "Couldn't tell which window",
        "TuxTalk couldn't tell which window has the focus, so it did nothing. Turn on "
        "the TuxTalk GNOME extension, or click the window and try again."),
    "gnome_relogin": (
        "Log out and back in for this",
        "This needs the newer TuxTalk GNOME extension. Log out and back in once, "
        "then try again."),
    "no_browser": (
        "No web browser is set up",
        "TuxTalk found no web browser to search with. Set a default browser in your "
        "desktop's settings."),
    "browser_copies_alike": (
        "Two copies of your browser",
        "Two copies of your default browser are installed, and TuxTalk can't tell their "
        "windows apart, so nothing was searched. Bring the one you want to the front, "
        "then say it again."),
    "browser_not_started": (
        "Your browser didn't start",
        "Your browser didn't start, so nothing was searched. Try opening it yourself."),
    "browser_not_forward": (
        "The browser didn't come forward",
        "Nothing was searched. Bring your browser to the front, then say it again."),
    "address_not_opened": (
        "Couldn't open that address",
        "TuxTalk couldn't hand the address to your browser. Try typing it there."),
    "cant_place_here": (
        "Can't move windows on this desktop",
        "This desktop doesn't let TuxTalk snap or move a window. Drag it, or use the "
        "desktop's own keys."),
    "cant_desktops_here": (
        "Can't switch desktops here",
        "This desktop doesn't let TuxTalk show the desktop or switch desktops. Use the "
        "desktop's own keys."),
    "no_overview_here": (
        "No window overview here",
        "This desktop has no overview of all windows that TuxTalk can open. Try Alt+Tab."),
    "no_such_desktop": (
        "There's no desktop {number}",
        "This computer has fewer desktops than that. Say a smaller number, or add "
        "desktops in your desktop's settings."),
    "one_desktop": (
        "There's only one desktop",
        "This computer has one desktop, so there's nowhere to go. Add desktops in your "
        "desktop's settings."),
    "no_desktop_that_way": (
        "No desktop that way",
        "You're on the last desktop that way. Say \"next desktop\" or \"previous "
        "desktop\" for the other way."),
    "one_screen": (
        "There's only one screen",
        "TuxTalk sees one screen here, so the window stays where it is."),
    # KWin was asked and gave no answer in time: it can do this, so "can't
    # here" would be wrong, and so would any claim about the screens.
    "desktop_no_answer": (
        "The desktop didn't answer",
        "The desktop didn't answer in time, so nothing was done. Say it again in a "
        "moment."),
}

# kind -> label, for a command that was done but whose result the user may
# not see (the volume level, the player paused), filled as say()'s are.
DONE: dict[str, str] = {}


def say(kind: str, beeps_on: bool, **names) -> None:
    """Tell the user the command of *kind* (a NOTES key) wasn't done: the
    cancel beep, the label over the window and the sentence in a
    notification. *names* fill the {app} or {number} in the texts."""
    label, sentence = NOTES[kind]
    logger.info(f"Voice command not done: {kind}")
    notices.command_refused(label.format(**names), sentence.format(**names), beeps_on)


def tell(kind: str, beeps_on: bool, **names) -> None:
    """Tell the user the command of *kind* (a DONE key) was done: the ready
    beep and the label over the window, as news rather than a warning. No
    notification: there is nothing to do about it."""
    logger.info(f"Voice command done: {kind}")
    beeps.beep(beeps_on, *beeps.READY_BEEP)
    screen_message.show_over_active_window(DONE[kind].format(**names), warning=False)


def say_focus_unknown(beeps_on: bool) -> None:
    """The desktop didn't say which app has the focus, so a key or a search
    wasn't done. On GNOME that is usually the TuxTalk extension being off;
    with it on (the tray slow to answer), clicking the window is the advice."""
    off = (desktop_detect.get_desktop_environment() == "gnome"
           and not app_focus.extension_enabled())
    say("no_focused_window_gnome" if off else "no_focused_window", beeps_on)


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
