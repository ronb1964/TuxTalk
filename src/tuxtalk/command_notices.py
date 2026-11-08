"""The notes for a voice command that wasn't done (everyday keys, window,
desktop, sound and music commands, addresses and searches, the mouse
grid): a short label floated over the window and a sentence in a
notification, each saying what happened and what to do; and for one that
was, the log line and the ready beep, with a short label where the result
isn't seen on the screen."""
from . import app_focus, beeps, desktop_detect, notices, screen_message
from .logger import setup_logger
from .modifier_hotkeys import HELD_LIMIT_S

logger = setup_logger(__name__)

# kind -> (label, sentence). {app}, {number} and {players} are filled from
# say()'s keyword names.
NOTES = {
    "terminal": (
        "Doesn't apply in a terminal",
        "This key doesn't apply in a terminal. Use the terminal's own keys."),
    "no_window": (
        "{app} has no open window",
        "There's no {app} window open to do that to. Open one, then try again."),
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
    # The desktop was asked and didn't answer in time, or its script failed:
    # it can do this, so "can't here" would be wrong, and so would any claim
    # about the screens.
    "desktop_no_answer": (
        "The desktop didn't answer",
        "The desktop didn't answer in time. It may still have done it: look before you "
        "say it again."),
    "no_sound_control": (
        "Couldn't change the volume",
        "TuxTalk couldn't reach the sound system (PipeWire or PulseAudio). Use your "
        "desktop's volume control."),
    "no_player": (
        "No music player to control",
        "TuxTalk found no music or video player it can control. Start one, then try "
        "again."),
    "nothing_playing": (
        "No music is playing",
        "Nothing is playing, so there was nothing to pause."),
    "which_player": (
        "Several players paused: {players}",
        "More than one player is paused, so TuxTalk didn't pick one. Start the one you "
        "want yourself."),
    "which_playing": (
        "Several players playing: {players}",
        "More than one player is playing, so TuxTalk didn't pick one. Pause the others, "
        "then try again."),
    "player_no_answer": (
        "The music player didn't answer",
        "TuxTalk asked the player, but it didn't answer in time. Try again in a moment."),
    "lock_failed": (
        "Couldn't lock the screen",
        "The computer didn't take the request to lock. Lock it from your desktop's menu."),
    "lock_unconfirmed": (
        "Lock not confirmed",
        "TuxTalk asked the computer to lock but couldn't confirm it did. Check the screen "
        "before you walk away."),
    "no_screenshot_tool": (
        "Couldn't take a screenshot",
        "TuxTalk found no way to take a screenshot on this desktop. Use your desktop's "
        "screenshot tool, often the Print Screen key."),
    "screenshot_unconfirmed": (
        "Screenshot not confirmed",
        "TuxTalk asked for a screenshot, but didn't hear back. It may still be saved "
        "where your desktop keeps screenshots."),
    "screenshot_cancelled": (
        "Screenshot cancelled",
        "The screenshot was cancelled, so nothing was saved."),
    "screenshot_picker_open": (
        "Screenshot tool already open",
        "Choose the window in the screenshot tool that's open, or press Escape there."),
    "cant_window_screenshot_here": (
        "Can't take just the window here",
        "This desktop can't take a screenshot of one window. Say \"take a screenshot\" "
        "for the whole screen."),
    "grid_needs_x": (
        "The mouse grid needs X (XWayland) here",
        "This desktop has no X display for TuxTalk to draw the grid on. \"Left click\" and "
        "\"right click\" still click where the pointer is."),
    "grid_needs_compositing": (
        "The mouse grid needs see-through windows here",
        "This desktop isn't drawing windows see-through (compositing is off), so the grid "
        "would hide the screen. Turn compositing on in the desktop's settings. \"Left "
        "click\" and \"right click\" still click where the pointer is."),
    "no_cell": (
        "No cell {number} here",
        "The grid has no cell {number}. Say one of the numbers on the screen."),
    "say_a_number": (
        "Say a number first",
        "There's more than one screen, so say the number of a cell, or \"click\" and "
        "the number."),
    "nudge_needs_a_number": (
        "Say a number first",
        "Say the number of a part first, then \"left\", \"right\", \"up\" or \"down\" "
        "to move the dot."),
    "smallest": (
        "That's as small as it goes",
        "The cells can't get any smaller. Say \"click\" to click the middle, or "
        "\"click\" and a number."),
    "cant_place": (
        "Can't place the pointer exactly on this screen setup",
        "TuxTalk couldn't put the pointer exactly on that spot, so it didn't click. Use "
        "the mouse for this one."),
    "zoom_not_back": (
        "Not clicked: the desktop is still zoomed in",
        "TuxTalk couldn't put the desktop's zoom back, and a click would land where you "
        "can't see it. Zoom out with your desktop's own zoom keys, then say it again."),
    "click_key_held": (
        "Not clicked: a dictation key stayed down",
        f"Your dictation key read as held down for {HELD_LIMIT_S:g} seconds, and the click "
        "would have been a Ctrl or Alt click. Let go of the key and say it again."),
    "pointer_failed": (
        "The mouse didn't work this time",
        "TuxTalk's virtual pointer didn't move or click. Say it again; if it keeps "
        "happening, open Preferences, Advanced, Show system check."),
}

# Slips while finding a way around the mouse grid, which stays up: the beep
# and the label over the window say it, and a notification each time would
# cover the zoomed view and fill the history.
_GRID_SLIPS = frozenset({"no_cell", "say_a_number", "nudge_needs_a_number", "smallest"})

# kind -> label, for a command that was done but whose result the user may
# not see (the volume level, the player paused), filled as say()'s are.
DONE: dict[str, str] = {
    "volume": "Volume {percent}%",
    "muted": "Sound muted",
    "unmuted": "Sound on",
    "screenshot_saved": "Screenshot saved in {folder}",
    "screenshot_saved_unseen": "Screenshot saved",
}


def say(kind: str, beeps_on: bool, **names) -> None:
    """Tell the user the command of *kind* (a NOTES key) wasn't done: the
    cancel beep, the label over the window and the sentence in a
    notification, but for a slip in the mouse grid (_GRID_SLIPS). *names*
    fill the {app}, {number} or {players} in the texts."""
    label, sentence = NOTES[kind]
    logger.info(f"Voice command not done: {kind}")
    notices.command_refused(label.format(**names), sentence.format(**names), beeps_on,
                            notify=kind not in _GRID_SLIPS)


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
