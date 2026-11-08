"""Window voice commands (TuxTalk Labs): "close Firefox", "close all
Firefox", "close window", "switch to Firefox", "minimize window",
"maximize window" and "restore window"; and by app name, "minimize Firefox",
"maximize Firefox" and "restore Firefox" (which also brings a minimized
window back), with the same app-name rules as "close Firefox" and one more:
those three act only on an app with a window open, else the words are typed.

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

from . import (app_focus, app_match, beeps, command_notices, desktop_detect, focused_window,
               keyboard_output, kwin_windows, notices, x11_windows)
from .beeps import READY_BEEP
from .command_phrases import WindowRequest
from .logger import setup_logger

logger = setup_logger(__name__)

TIMEOUT_S = 1.0
NOTHING_DONE = "Nothing was done."

_NO_REPLY = "org.freedesktop.DBus.Error.NoReply"
# A method, or the whole object, that the extension GNOME Shell runs lacks.
_NOTHING_THERE = frozenset(f"org.freedesktop.DBus.Error.{name}"
                           for name in ("UnknownMethod", "UnknownObject", "UnknownInterface"))
# The note for an action this desktop can't do; the others change a window's size.
_CANT = {"close": "cant_close_here", "switch": "cant_switch_here"}
# Words people say of other things ("Restore the files.", "Maximize the
# music."): even a full app name counts only for an app with a window open.
_ONLY_WHEN_OPEN = frozenset({"minimize", "maximize", "restore"})


def _cant(action: str) -> str:
    return _CANT.get(action, "cant_size_here")


def run(request: WindowRequest, where, beeps_on: bool) -> bool | None:
    """Do *request*. None: it names no app ("Close the door."), so the words
    are typed; True: done; False: not done, said with a note, or stopped by
    Esc. *where*: focused_window.look(), taken when the command was heard:
    "close window" is for that window only."""
    if request.app is None:
        logger.info(f"Voice command: {request.action} the focused window")
        return _act(request, None, False, where, beeps_on)
    # Whole words of a name only ("Close Chrome."): the start of one ("Close
    # the fire.") would close the wrong window, or eat the sentence with a
    # note about another app.
    found = app_match.find(request.app, strict=True)
    if found.partial:
        found = _open_ones(found.partial)
    elif found.kind == "one" and request.action in _ONLY_WHEN_OPEN:
        found = _open_ones(((found.entry, found.exact),))
    elif found.kind == "tie" and request.action in _ONLY_WHEN_OPEN:
        # Two apps of one name: only a window naming its own menu entry is
        # each one's, so one's window is never taken for the other's.
        found = _open_ones(tuple((entry, True) for entry in found.entries))
    if found.kind == "none":
        # As for "open": a sentence lost is worse than one typed. The log
        # keeps the user's words out.
        logger.info("Window command: no app matched; typed as words")
        return None
    if found.kind == "tie":
        app_match.say_tie(found, request.action, NOTHING_DONE, beeps_on)
        return False
    logger.info(f"Voice command: {request.action} {found.entry.app_id}")
    return _act(request, found.entry, found.exact, where, beeps_on)


def _open_ones(candidates: tuple) -> app_match.Match:
    """Of *candidates*, (entry, exact) for each app the words fit only in
    part ("close Chrome") or by a name that is also a word ("restore the
    files"), the ones with a window open: "Close Writer." means the
    LibreOffice Writer that's open, not Fedora Media Writer, and an app that
    isn't open wasn't meant. Where the desktop can't tell, none are."""
    try:
        open_ones = [(entry, exact) for entry, exact in candidates
                     if _ask_desktop(WindowRequest("find"), entry, exact)]
    except Exception as e:      # each desktop fails its own way: none known to be open
        # Once, with no app named: the apps the words fit hint at the words,
        # which are typed next.
        logger.info(f"Couldn't tell which apps have a window open here: {e}")
        open_ones = []
    if len(open_ones) == 1:
        return app_match.Match("one", (open_ones[0][0],), open_ones[0][1])
    kind = "tie" if open_ones else "none"
    return app_match.Match(kind, tuple(entry for entry, _exact in open_ones))


def still_wanted(action: str, where, check_focus: bool, beeps_on: bool) -> bool:
    """Whether *action* may still be done, right before the desktop is
    asked: Esc may have come while the app was being found, and a desktop
    acts on whichever window has the focus when the request gets there, so
    with *check_focus* that must still be *where*, the look taken when the
    command was heard. Says why not."""
    if keyboard_output.esc_stopped(f"done ({action})"):
        return False
    if check_focus and focused_window.changed(where, focused_window.look()):
        logger.info(f"Voice command {action} not done: another window has the focus")
        notices.edit_problem("focus_moved", beeps_on)
        return False
    return True


def _act(request: WindowRequest, entry, exact: bool, where, beeps_on: bool) -> bool:
    """Do *request* to *entry*'s windows, or the focused window *where* with
    no *entry*, and say what came of it."""
    if not still_wanted(request.action, where, entry is None, beeps_on):
        return False
    try:
        if request.action == "switch":
            # Never starts the app: "switch to" one that isn't open gets a note.
            acted = app_focus.bring_forward(entry, TIMEOUT_S, exact)
        else:
            acted = _ask_desktop(request, entry, exact, where)
    except Exception as e:      # each desktop fails its own way
        logger.info(f"Voice command {request.action} not done: {e}")
        command_notices.say(refusal(_cant(request.action), e), beeps_on)
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


def _ask_desktop(request: WindowRequest, entry, exact: bool, where=None):
    """This desktop's answer: whether it acted on a window, or None where
    nothing can be asked. False, with nobody asked, for an app with no
    identity: sent on, it would mean the focused window. *where*, the look
    the command was heard in, binds "close window" to that window on KDE,
    where the tray knows it by KWin's own id."""
    action, every = request.action, request.every
    desktop_file = app_focus.desktop_file(entry) if entry else ""
    names = app_focus.window_names(entry, exact) if entry else frozenset()
    if entry is not None and not desktop_file:
        logger.info(f"Not done: {entry.app_id!r} gives nothing to know its windows by")
        return False
    desktop = desktop_detect.get_desktop_environment()
    if desktop == "kde":
        return kwin_windows.act(action, desktop_file, names, every, TIMEOUT_S,
                                _kwin_window(where) if entry is None else "")
    if desktop == "gnome":
        app_id = entry.app_id if entry else ""
        return bool(app_focus.call_extension("WindowAction", "ssb", (action, app_id, every),
                                             TIMEOUT_S))
    if desktop_detect.is_x11_session(os.environ):
        return x11_windows.act(action, desktop_file, names, every)
    return None


def _kwin_window(where) -> str:
    """KWin's internalId of the window in the look *where*, "" when the
    tray didn't tell it."""
    window = where[1] if where else None
    return window[1] if window and window[0] == "tray" and window[1] else ""


def refusal(cant: str, error: Exception) -> str:
    """The note for a desktop that couldn't be asked: "desktop_no_answer"
    when it can do it but didn't, this time (KWin's TimeoutError, a KWin
    script that failed with RuntimeError, or the NoReply of a D-Bus call to
    KWin or GNOME Shell that timed out), else *cant*, the note that this
    desktop can't do it, unless GNOME Shell still runs the TuxTalk extension
    from before an update, which has the method only after the user logs
    out and back in. No extension at all fails the same way, so GNOME Shell
    is asked whether TuxTalk's is enabled, never the error's wording, which
    is translated."""
    dbus_name = getattr(error, "get_dbus_name", lambda: None)()
    if isinstance(error, (TimeoutError, RuntimeError)) or dbus_name == _NO_REPLY:
        return "desktop_no_answer"
    if dbus_name not in _NOTHING_THERE:
        return cant
    return "gnome_relogin" if app_focus.extension_enabled() else cant
