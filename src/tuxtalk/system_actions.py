"""System voice commands (TuxTalk Labs): lock the screen, "what can I say" and
screenshots (screenshot.py).

The lock goes through logind (`loginctl lock-session`), so the desktop's own
lock screen and password apply and TuxTalk never handles either. Asking is
not locking: a stopped or broken locker leaves the screen open, and some
lockers never tell logind they locked. So the session's LockedHint is read
for a few seconds, on every desktop, and only a lock it confirms gets the
ready beep; any other gets a plain note to check the screen. Programs are
run with a timeout and no shell; the wait is on the dictation worker, never
GTK's thread."""
import os
import subprocess
import time

from . import beeps, command_notices, screenshot, tray_link
from .command_phrases import SystemRequest
from .logger import setup_logger

logger = setup_logger(__name__)

LOCK_CHECK_S = 3.0      # how long the locker gets to tell logind it locked
_LOCK_LOOK_EVERY_S = 0.5
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


def _confirmed(session: str, sleep) -> bool:
    """Whether logind says *session* is locked within LOCK_CHECK_S. A state
    it can't read isn't asked again: that query could be slow each time."""
    for _ in range(round(LOCK_CHECK_S / _LOCK_LOOK_EVERY_S)):
        sleep(_LOCK_LOOK_EVERY_S)
        answer = locked(session)
        if answer is not False:
            return bool(answer)
    return False


def lock(beeps_on: bool, sleep=time.sleep) -> bool:
    """Lock the screen. False: logind refused, said with a note. True once
    it was asked; the ready beep only when the session then says it is
    locked, and otherwise a note saying the lock isn't confirmed."""
    session = session_id()
    if not _ask_to_lock(session):
        command_notices.say("lock_failed", beeps_on)
        return False
    if _confirmed(session, sleep):
        logger.info("Voice command: lock screen; the session says it is locked")
        beeps.beep(beeps_on, *beeps.READY_BEEP)
    else:
        logger.info("Voice command: lock screen asked; the session doesn't say it is locked")
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
