"""Desktop voice commands (TuxTalk Labs): "snap left", "centre window",
"move window to the other screen", "show desktop", "show all windows",
"switch to desktop two", "next desktop", "move window to desktop three" and
the rest.

Each desktop does it its own way, as for the window commands
(window_actions): KDE through KWin's own shortcuts and methods
(kwin_desktop), GNOME through TuxTalk's extension inside GNOME Shell (its
DesktopCommand method), X11 through the window manager (x11_desktop).
Elsewhere (COSMIC, Sway...) a note says it can't be done here. Each answers
with the same few words ("done", "no_desktop", "edge"...), turned here into
the note that says what happened.

A snap, a centre or a move acts on whichever window has the focus when the
desktop gets it, so it is done only while that is still the window the
command was heard in. Run on the dictation worker: each desktop may take up
to TIMEOUT_S to answer a call.
"""
import os

from . import (app_focus, beeps, command_notices, desktop_detect, kwin_desktop, window_actions,
               x11_desktop)
from .beeps import READY_BEEP
from .command_phrases import FRONT_WINDOW_ACTIONS, DesktopRequest
from .logger import setup_logger

logger = setup_logger(__name__)

TIMEOUT_S = window_actions.TIMEOUT_S

# A desktop's answer -> the note saying why nothing was done.
_NOTES = {"no_window": "no_focused_window", "no_desktop": "no_such_desktop",
          "one_desktop": "one_desktop", "one_screen": "one_screen",
          "edge": "no_desktop_that_way"}


def run(request: DesktopRequest, where, beeps_on: bool) -> bool:
    """Do *request*. True: done; False: not done, said with a note, or
    stopped by Esc. *where*: focused_window.look(), taken when the command
    was heard: a snap or a move is for that window only."""
    action, number = request.action, request.number
    on_window = action in FRONT_WINDOW_ACTIONS

    def still_wanted() -> bool:
        return window_actions.still_wanted(action, where, on_window, beeps_on)
    if not still_wanted():
        return False
    try:
        answer = _ask_desktop(action, number, still_wanted if on_window else None)
    except Exception as e:      # each desktop fails its own way
        logger.info(f"Voice command {action} not done: {e}")
        # A slow or failing desktop can do it, so never "can't here", nor,
        # to a user with two screens, that there is one.
        command_notices.say(window_actions.refusal(_cant(action), e), beeps_on)
        return False
    if answer == "stopped":     # still_wanted said why
        return False
    if answer == "done":
        logger.info(f"Voice command: {action}" + (f" {number}" if number is not None else ""))
        beeps.beep(beeps_on, *READY_BEEP)
        return True
    command_notices.say(_NOTES.get(answer) or _cant(action), beeps_on, number=number)
    return False


def _ask_desktop(action: str, number: int | None, still_wanted):
    """This desktop's answer, or None where nothing can be asked. KWin is
    asked questions before some shortcuts (how many desktops, whether
    there is another screen), so it asks *still_wanted* again right before
    acting; GNOME's extension and X11 act straight away."""
    desktop = desktop_detect.get_desktop_environment()
    if desktop == "kde":
        return kwin_desktop.act(action, number, TIMEOUT_S, still_wanted)
    if desktop == "gnome":
        # D-Bus has no "none": 0 is no desktop's number.
        return str(app_focus.call_extension("DesktopCommand", "si", (action, number or 0),
                                            TIMEOUT_S))
    if desktop_detect.is_x11_session(os.environ):
        return x11_desktop.act(action, number)
    return None


def _cant(action: str) -> str:
    """The note for *action* where this desktop can't do it."""
    if action == "overview":
        return "no_overview_here"
    return "cant_place_here" if action in FRONT_WINDOW_ACTIONS else "cant_desktops_here"
