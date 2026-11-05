"""Window voice commands (TuxTalk Labs): "close Firefox", "close all
Firefox", "close window", "switch to Firefox", "minimize window",
"maximize window" and "restore window".

Each desktop does it its own way, as for bringing a window forward
(app_focus): KDE through a KWin script (kwin_windows), GNOME through
TuxTalk's extension inside GNOME Shell (its WindowAction method), X11
through the window manager (x11_windows). Elsewhere (COSMIC, Sway...) a note
says it can't be done here. Closing is always the polite close, the same as
the window's X button, so an app with unsaved work still asks to save.

An app's window is told apart by its identity (menu entry, window classes);
an empty identity means "the focused window" to every desktop, so a named
app that has none is refused rather than sent on. Run on the dictation
worker: each desktop may take up to TIMEOUT_S to answer.
"""
import os

from . import (app_focus, app_match, beeps, command_notices, desktop_detect, kwin_windows,
               x11_windows)
from .beeps import READY_BEEP
from .command_phrases import WindowRequest
from .logger import setup_logger

logger = setup_logger(__name__)

TIMEOUT_S = 1.0
NOTHING_DONE = "Nothing was done."

_UNKNOWN_METHOD = "org.freedesktop.DBus.Error.UnknownMethod"
# The note for an action this desktop can't do; the others change a window's size.
_CANT = {"close": "cant_close_here", "switch": "cant_switch_here"}


def _cant(action: str) -> str:
    return _CANT.get(action, "cant_size_here")


def run(request: WindowRequest, beeps_on: bool) -> bool | None:
    """Do *request*. None: it names no app ("Close the door."), so the words
    are typed; True: done; False: not done, said with a note."""
    if request.app is None:
        logger.info(f"Voice command: {request.action} the focused window")
        return _act(request, None, False, beeps_on)
    # The whole name only: the start of one ("Close the fire.") would close
    # the wrong window, or eat the sentence with a note about another app.
    found = app_match.find(request.app, strict=True)
    if found.kind == "none":
        # As for "open": a sentence lost is worse than one typed. The log
        # keeps the user's words out.
        logger.info("Window command: no app matched; typed as words")
        return None
    if found.kind == "tie":
        app_match.say_tie(found, request.action, NOTHING_DONE, beeps_on)
        return False
    logger.info(f"Voice command: {request.action} {found.entry.app_id}")
    return _act(request, found.entry, found.exact, beeps_on)


def _act(request: WindowRequest, entry, exact: bool, beeps_on: bool) -> bool:
    """Do *request* to *entry*'s windows, or the focused window with no
    *entry*, and say what came of it."""
    try:
        if request.action == "switch":
            # Never starts the app: "switch to" one that isn't open gets a note.
            acted = app_focus.bring_forward(entry, TIMEOUT_S, exact)
        else:
            acted = _ask_desktop(request, entry, exact)
    except Exception as e:      # each desktop fails its own way
        logger.info(f"Window command {request.action} not done: {e}")
        command_notices.say(_refusal(request.action, e), beeps_on)
        return False
    if acted is None:
        command_notices.say(_cant(request.action), beeps_on)
    elif not acted:
        _say_nothing_to_act_on(request.action, entry, beeps_on)
    else:
        beeps.beep(beeps_on, *READY_BEEP)
    return bool(acted)


def _say_nothing_to_act_on(action: str, entry, beeps_on: bool) -> None:
    """The desktop found no window to do *action* to."""
    if entry is None:
        command_notices.say("no_focused_window", beeps_on)
    elif action == "switch":
        command_notices.say("not_running", beeps_on, app=entry.name)
    else:
        command_notices.say("no_window", beeps_on, app=entry.name)


def _ask_desktop(request: WindowRequest, entry, exact: bool):
    """This desktop's answer: whether it acted on a window, or None where
    nothing can be asked. False, with nobody asked, for an app with no
    identity: sent on, it would mean the focused window."""
    action, every = request.action, request.every
    desktop_file = app_focus.desktop_file(entry) if entry else ""
    names = app_focus.window_names(entry, exact) if entry else frozenset()
    if entry is not None and not desktop_file:
        logger.info(f"Not done: {entry.app_id!r} gives nothing to know its windows by")
        return False
    desktop = desktop_detect.get_desktop_environment()
    if desktop == "kde":
        return kwin_windows.act(action, desktop_file, names, every, TIMEOUT_S)
    if desktop == "gnome":
        app_id = entry.app_id if entry else ""
        return bool(app_focus.call_extension("WindowAction", "ssb", (action, app_id, every),
                                             TIMEOUT_S))
    if desktop_detect.is_x11_session(os.environ):
        return x11_windows.act(action, desktop_file, names, every)
    return None


def _refusal(action: str, error: Exception) -> str:
    """The note for a desktop that couldn't be asked to do *action*: this
    desktop can't do it, unless GNOME Shell still runs the TuxTalk extension
    from before an update, which has the method only after the user logs
    out and back in. No extension at all fails the same way (UnknownMethod),
    so the extension's object is looked for, never the error's wording,
    which is translated."""
    cant = _cant(action)
    if getattr(error, "get_dbus_name", lambda: None)() != _UNKNOWN_METHOD:
        return cant
    try:
        old = app_focus.extension_running(TIMEOUT_S)
    except Exception as e:      # GNOME Shell gone meanwhile: no extension to log out for
        logger.info(f"Couldn't look for the GNOME extension: {e}")
        return cant
    return "gnome_relogin" if old else cant
