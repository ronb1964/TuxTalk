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

from . import (app_focus, beeps, command_notices, desktop_detect, kwin_windows, notices,
               open_app, x11_windows)
from .beeps import READY_BEEP
from .command_phrases import WindowRequest
from .logger import setup_logger

logger = setup_logger(__name__)

TIMEOUT_S = 1.0

_UNKNOWN_METHOD = "org.freedesktop.DBus.Error.UnknownMethod"


def run(request: WindowRequest, beeps_on: bool) -> bool | None:
    """Do *request*. None: it names no app ("Close the door."), so the words
    are typed; True: done; False: not done, said with a note."""
    if request.app is None:
        logger.info(f"Voice command: {request.action} the focused window")
        return _act(request, None, False, beeps_on)
    matched = open_app.matched_app(request.app, request.action, beeps_on)
    if not matched:
        return matched
    entry, exact = matched
    logger.info(f"Voice command: {request.action} {entry.app_id}")
    if request.action == "switch":
        return _switch(entry, exact, beeps_on)
    return _act(request, entry, exact, beeps_on)


def _switch(entry, exact: bool, beeps_on: bool) -> bool:
    """Bring *entry*'s app forward. Never starts it: "switch to" said for an
    app that isn't open gets a note instead."""
    if not app_focus.can_bring_forward():
        command_notices.say("cant_switch_here", beeps_on)
        return False
    if not app_focus.focus_running(entry, timeout=TIMEOUT_S, exact=exact):
        command_notices.say("not_running", beeps_on, app=entry.name)
        return False
    beeps.beep(beeps_on, *READY_BEEP)
    return True


def _act(request: WindowRequest, entry, exact: bool, beeps_on: bool) -> bool:
    """Close, minimize or maximize *entry*'s windows, or the focused window
    with no *entry*, and say what came of it."""
    try:
        acted = _ask_desktop(request, entry, exact)
    except Exception as e:      # each desktop fails its own way
        logger.info(f"Window command {request.action} not done: {e}")
        _say_failed(e, beeps_on)
        return False
    if acted is None:
        kind = "cant_close_here" if request.action == "close" else "cant_size_here"
        command_notices.say(kind, beeps_on)
        return False
    if not acted:
        if entry is None:
            command_notices.say("no_focused_window", beeps_on)
        else:
            command_notices.say("no_window", beeps_on, app=entry.name)
        return False
    beeps.beep(beeps_on, *READY_BEEP)
    return True


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


def _say_failed(error: Exception, beeps_on: bool) -> None:
    """Tell the user the desktop couldn't be asked. The GNOME extension
    still running from before an update has no WindowAction until the user
    logs out and back in, which the note says."""
    if getattr(error, "get_dbus_name", lambda: None)() == _UNKNOWN_METHOD:
        command_notices.say("gnome_relogin", beeps_on)
    else:
        notices.edit_problem("shortcut_failed", beeps_on)
