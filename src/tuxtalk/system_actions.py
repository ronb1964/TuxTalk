"""System voice commands (TuxTalk Labs): lock the screen, "what can I say" and
screenshots (screenshot.py).

The lock goes through logind (`loginctl lock-session`), so the desktop's own
lock screen and password apply and TuxTalk never handles either. Some
lockers never tell logind they locked, so a few seconds later the session's
LockedHint is read: a lock that didn't confirm is never reported as done.
Only desktops whose lockers are known to answer are trusted without the
flag; elsewhere an unconfirmed lock gets a plain note. Programs are run with
a timeout and no shell; the wait is on the dictation worker, never GTK's
thread."""
import os
import subprocess
import time

from . import beeps, command_notices, desktop_detect, screenshot, tray_link
from .command_phrases import SystemRequest
from .logger import setup_logger

logger = setup_logger(__name__)

LOCK_CHECK_S = 2.0      # how long the locker gets to tell logind before it is asked
# Desktops whose lockers set LockedHint; a lock that doesn't confirm there is
# not worth a warning.
LOCKERS_THAT_ANSWER = frozenset({"kde", "gnome", "cinnamon", "xfce", "mate"})
_LOCK_TIMEOUT_S = 5
_QUERY_TIMEOUT_S = 2


def session_id() -> str:
    """This login session's id, or "auto": loginctl then takes the session
    of the process asking."""
    return os.environ.get("XDG_SESSION_ID") or "auto"


def locked(session: str) -> bool | None:
    """Whether logind says *session* is locked; None when it can't tell."""
    cmd = ["loginctl", "show-session", session, "-p", "LockedHint", "--value"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
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


def lock(beeps_on: bool, sleep=time.sleep) -> bool:
    """Lock the screen. False: logind refused, said with a note. True once
    it was asked; if the session then doesn't say it is locked, that is
    logged, and said too on a desktop whose locker isn't known to answer."""
    session = session_id()
    if not _ask_to_lock(session):
        command_notices.say("lock_failed", beeps_on)
        return False
    beeps.beep(beeps_on, *beeps.READY_BEEP)
    sleep(LOCK_CHECK_S)
    if locked(session):
        logger.info("Voice command: lock screen; the session says it is locked")
        return True
    logger.info("Voice command: lock screen asked; the session doesn't say it is locked")
    if desktop_detect.get_desktop_environment() not in LOCKERS_THAT_ANSWER:
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
