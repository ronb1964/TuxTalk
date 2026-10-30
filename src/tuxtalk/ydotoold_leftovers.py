"""ydotoold, which TuxTalk typed through until 0.18, taken away.

0.18 types on its own virtual keyboard. Until then the tray started ydotoold
itself, and Fix Typing could set up a systemd user unit that starts it at
every login. retire_ydotoold() takes both away at every start, so nothing
runs ydotoold for TuxTalk any more; the uninstaller looks for the unit too.
"""
import os
import signal
import subprocess

from .logger import setup_logger

logger = setup_logger(__name__)

YDOTOOLD_UNIT = os.path.join(".config", "systemd", "user", "ydotoold.service")


def our_ydotoold_unit(home):
    """The ydotoold systemd user unit under *home* if TuxTalk wrote it, else
    None. The name is generic, so its description has to say it's ours."""
    path = os.path.join(home, YDOTOOLD_UNIT)
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None
    if "(TuxTalk keystroke injection)" in text or "(TalkType keystroke injection)" in text:
        return path
    return None


def retire_ydotoold():
    """Stop the ydotoold an older TuxTalk started and remove its systemd user
    unit. Never raises: a leftover daemon only wastes a little memory."""
    stop_ydotoold_we_started()
    unit = our_ydotoold_unit(os.path.expanduser("~"))
    if unit is None:
        return
    try:
        result = subprocess.run(["systemctl", "--user", "disable", "--now", "ydotoold.service"],
                                capture_output=True, text=True, errors="replace", timeout=15)
        if result.returncode != 0:
            # Kept, so the next start tries again: removed while systemd still
            # runs it, the daemon would go on until logout.
            logger.warning(f"Could not stop the old ydotoold service: {result.stderr.strip()}")
            return
        os.remove(unit)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=15)
        logger.info("Removed the ydotoold service TuxTalk used to type with")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Could not remove the old ydotoold service: {e}")


def _our_ydotoold_pidfile():
    """Where an older TuxTalk noted the ydotoold it started itself."""
    from .single_instance import session_runtime_dir
    return os.path.join(session_runtime_dir(), "tuxtalk-ydotoold.pid")


def stop_ydotoold_we_started():
    """Stop the ydotoold an older TuxTalk started. Never raises.

    Only that one: a ydotoold run by another program may still be needed.
    The noted process must still be a ydotoold, as the number may have been
    handed to another program since."""
    path = _our_ydotoold_pidfile()
    try:
        with open(path) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return
    try:
        with open(f"/proc/{pid}/comm") as f:
            if f.read().strip() == "ydotoold":
                os.kill(pid, signal.SIGTERM)
                logger.info("Stopped the ydotoold an older TuxTalk started")
    except (OSError, ValueError) as e:
        logger.debug(f"The ydotoold TuxTalk started is already gone: {e}")
    try:
        os.remove(path)
    except OSError:
        pass
