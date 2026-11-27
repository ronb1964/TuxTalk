"""Whether a program is running, for the checks that change what TuxTalk does
while one is: an input method's Ctrl+Space, a clipboard history."""
import os

from .logger import setup_logger

logger = setup_logger(__name__)


def is_running(name: str, proc: str = "/proc") -> bool | None:
    """Whether this user has a process called *name* (its /proc/<pid>/comm,
    which keeps 15 characters: names must stay short); None when the
    running programs can't be listed, for a caller to decide."""
    uid = os.getuid()
    try:
        pids = os.listdir(proc)
    except OSError as e:
        logger.info(f"Could not list the running programs: {e}")
        return None
    for pid in pids:
        if not pid.isdigit():
            continue
        try:
            if os.stat(os.path.join(proc, pid)).st_uid != uid:
                continue
            # errors="replace": comm's 15 bytes can end inside a character.
            with open(os.path.join(proc, pid, "comm"), encoding="utf-8",
                      errors="replace") as f:
                if f.read().strip() == name:
                    return True
        except OSError:     # the process ended while we looked
            continue
    return False
