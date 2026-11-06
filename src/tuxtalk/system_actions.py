"""System voice commands (TuxTalk Labs): lock the screen, "what can I say" and
screenshots (screenshot.py).

The lock goes through the desktop's own lock screen, so its password
applies and TuxTalk never handles one: through logind
(`loginctl lock-session`), or on Cinnamon, MATE and Xfce through their
screensaver, which locks those desktops and never tells logind it did.
Asking is not locking: a stopped or broken locker leaves the screen open.
So the lock is looked for a few seconds, in the session's LockedHint or the
screensaver's GetActive, and only a lock seen there gets the ready beep;
any other gets a plain note to check the screen. Programs are run with a
timeout and no shell; the wait is on the dictation worker, never GTK's
thread."""
import os
import subprocess
import time

from gi.repository import Gio, GLib

from . import beeps, command_notices, desktop_detect, screenshot, tray_link
from .command_phrases import SystemRequest
from .logger import setup_logger

logger = setup_logger(__name__)

LOCK_CHECK_S = 3.0      # how long the locker gets to say it locked
_LOCK_LOOK_EVERY_S = 0.5
_LOCK_TIMEOUT_S = 5
_QUERY_TIMEOUT_S = 2

# The screensaver each of these desktops locks with: its bus name (also its
# interface) and object path, and Lock's arguments (Cinnamon's takes a
# message for the lock screen). None of them sets logind's LockedHint;
# cinnamon-screensaver only listens to logind's lock and unlock.
_SCREENSAVERS = {
    "cinnamon": ("org.cinnamon.ScreenSaver", "/org/cinnamon/ScreenSaver",
                 GLib.Variant("(s)", ("",))),
    "mate": ("org.mate.ScreenSaver", "/org/mate/ScreenSaver", None),
    "xfce": ("org.xfce.ScreenSaver", "/org/xfce/ScreenSaver", None),
}


def session_id() -> str:
    """This login session's id, or "auto": loginctl then takes the session
    of the process asking."""
    return os.environ.get("XDG_SESSION_ID") or "auto"


def locked(session: str) -> bool | None:
    """Whether logind says *session* is locked; None when it can't tell."""
    cmd = ["loginctl", "show-session", session, "-p", "LockedHint", "--value"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                timeout=_QUERY_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"loginctl show-session failed: {e}")
        return None
    answer = result.stdout.strip()
    if result.returncode != 0 or answer not in ("yes", "no"):
        return None
    return answer == "yes"


def _ask_to_lock(session: str) -> bool:
    """Whether logind took the request to lock *session*."""
    try:
        result = subprocess.run(["loginctl", "lock-session", session],
                                capture_output=True, timeout=_LOCK_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"loginctl lock-session failed: {e}")
        return False
    if result.returncode != 0:
        logger.warning(f"loginctl lock-session failed with exit code {result.returncode}")
        return False
    return True


def _call_screensaver(saver, method: str, args, timeout_s: float):
    """Call *method* of the screensaver *saver*; raises GLib.Error."""
    name, path, _lock_args = saver
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return bus.call_sync(name, path, name, method, args, None, Gio.DBusCallFlags.NONE,
                         int(timeout_s * 1000), None)


def _screensaver_locks(saver) -> bool:
    """Ask the screensaver *saver* to lock. False when it isn't there to
    ask (an Xfce with light-locker, say): logind is asked instead. A
    request that timed out counts as asked: it may still be locking, and a
    second request through logind would be no surer."""
    try:
        _call_screensaver(saver, "Lock", saver[2], _LOCK_TIMEOUT_S)
    except GLib.Error as e:
        if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT):
            logger.warning(f"{saver[0]} didn't answer the request to lock in time")
            return True
        logger.info(f"Locking through logind: {saver[0]} can't be asked ({e.message})")
        return False
    return True


def _screensaver_active(saver) -> bool | None:
    """Whether the screensaver *saver* says its lock screen is up; None
    when it can't tell."""
    try:
        reply = _call_screensaver(saver, "GetActive", None, _QUERY_TIMEOUT_S)
    except GLib.Error as e:
        logger.warning(f"{saver[0]} GetActive failed: {e.message}")
        return None
    return bool(reply.unpack()[0])


def _confirmed(is_locked, sleep) -> bool:
    """Whether *is_locked* says the screen is locked within LOCK_CHECK_S. A
    state it can't read isn't asked again: that query could be slow each
    time."""
    for _ in range(round(LOCK_CHECK_S / _LOCK_LOOK_EVERY_S)):
        sleep(_LOCK_LOOK_EVERY_S)
        answer = is_locked()
        if answer is not False:
            return bool(answer)
    return False


def lock(beeps_on: bool, sleep=time.sleep) -> bool:
    """Lock the screen. False: logind refused, said with a note. True once
    it was asked; the ready beep only when the screensaver or the session
    then says it is locked, and otherwise a note saying the lock isn't
    confirmed."""
    saver = _SCREENSAVERS.get(desktop_detect.get_desktop_environment())
    if saver and _screensaver_locks(saver):
        who, is_locked = "the screensaver", lambda: _screensaver_active(saver)
    else:
        session = session_id()
        if not _ask_to_lock(session):
            command_notices.say("lock_failed", beeps_on)
            return False
        who, is_locked = "the session", lambda: locked(session)
    if _confirmed(is_locked, sleep):
        logger.info(f"Voice command: lock screen; {who} says it is locked")
        beeps.beep(beeps_on, *beeps.READY_BEEP)
    else:
        logger.info(f"Voice command: lock screen asked; {who} doesn't say it is locked")
        # Only the note sounds: a ready beep then a cancel beep would say both.
        command_notices.say("lock_unconfirmed", beeps_on)
    return True


def run(request: SystemRequest, beeps_on: bool) -> bool:
    """Do the system command *request* is (lock, commands or screenshot).
    True: done; False: refused with a note."""
    if request.action == "lock":
        return lock(beeps_on)
    if request.action == "commands":
        tray_link.show_voice_commands()
        beeps.beep(beeps_on, *beeps.READY_BEEP)
        logger.info("Voice command: what can I say")
        return True
    return screenshot.take(beeps_on)
