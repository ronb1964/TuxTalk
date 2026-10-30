"""GNOME's Alt+F8 (resize) and Alt+F7 (move) bindings during the hotkey test.

The first-run hotkey test clears them while it is open, because GNOME answers
Alt+F8 by sending the focused window an F8 without the Alt modifier, which
looks exactly like a real F8 press. gsettings keeps whatever is written across
reboots, so the original values are first saved to a file. Restoring deletes
the file; if the process dies before that (killed, crashed, logged out), the
next start finds the file and puts the bindings back.
"""
import json
import os
import subprocess

from .logger import setup_logger

logger = setup_logger(__name__)

SCHEMA = "org.gnome.desktop.wm.keybindings"
KEYS = ("begin-resize", "begin-move")
BACKUP_NAME = "gnome-keybindings-backup.json"


def _backup_path():
    from .config import get_data_dir
    return os.path.join(get_data_dir(), BACKUP_NAME)


def _on_gnome():
    return "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()


def _set(key, value):
    """Write one binding; True if gsettings accepted it."""
    try:
        result = subprocess.run(["gsettings", "set", SCHEMA, key, value],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Couldn't set GNOME's {key}: {e}")
        return False
    if result.returncode != 0:
        logger.warning(f"GNOME didn't take {key}: {result.stderr.strip()}")
    return result.returncode == 0


def restore(saved):
    """Put the saved bindings back; never raises (it runs from finally blocks).
    True once they all are.

    One failed key never skips the other. The backup file is deleted only
    once every key is back, so a failure is retried at the next start.
    """
    if not saved:
        return True
    all_back = True
    for key, value in saved.items():
        if key not in KEYS or not isinstance(value, str):
            continue
        if _set(key, value):
            logger.info(f"Restored {key} to {value}")
        else:
            all_back = False
    if all_back:
        try:
            os.remove(_backup_path())
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.info(f"Could not remove the keybinding backup: {e}")
    return all_back


def restore_left_over():
    """Restore bindings a previous run cleared and never put back. False
    when some are still cleared (the backup then stays, for another try)."""
    try:
        with open(_backup_path(), encoding="utf-8") as f:
            saved = json.load(f)
    except FileNotFoundError:
        return True
    except Exception as e:
        logger.warning(f"Unreadable GNOME keybinding backup: {e}")
        return False
    if not isinstance(saved, dict):
        return True
    logger.warning("Putting back GNOME's Alt+F8/Alt+F7 bindings left cleared by a "
                   "hotkey test that didn't finish")
    return restore(saved)


def disable_for_hotkey_test():
    """Clear the bindings on GNOME. Returns what to pass to restore(), or None."""
    if not _on_gnome():
        return None
    # A backup left by a run that died holds the real values; reading the
    # bindings now would only find the cleared ones, and saving those over
    # the backup would lose the real ones for good.
    if not restore_left_over():
        logger.warning("Not clearing GNOME keybindings: earlier ones aren't back yet")
        return None
    try:
        saved = {key: subprocess.check_output(["gsettings", "get", SCHEMA, key],
                                              text=True, timeout=5).strip()
                 for key in KEYS}
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(f"Could not read GNOME keybindings: {e}")
        return None
    try:
        from .config import write_text_atomic
        path = _backup_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Whole or not at all: a half-written backup can't be read back, and
        # until it is, no hotkey test clears the bindings.
        write_text_atomic(path, json.dumps(saved), keep_backup=False)
    except Exception as e:
        # Without the backup a crash would lose them for good; leave them alone.
        logger.warning(f"Not clearing GNOME keybindings: could not save them ({e})")
        return None
    for key in KEYS:
        _set(key, "[]")
    logger.info("Temporarily disabled GNOME begin-resize/begin-move keybindings")
    return saved
