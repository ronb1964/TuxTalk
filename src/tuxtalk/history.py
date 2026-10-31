"""
Recent dictations: the last few things the user dictated, so text that went
to the wrong window (focus stolen by a notification, a form that reloaded,
nothing focused at all) can be copied again instead of being spoken twice.

Privacy: the list lives in the user's private runtime directory
(single_instance.runtime_dir: XDG_RUNTIME_DIR, normally /run/user/<uid>).
That is RAM-backed tmpfs, readable only by the user, and wiped at logout,
so dictated text is never written to disk. A session without one keeps no
list at all, rather than use /tmp, which may be on disk. This matches
TuxTalk's promise that dictated text stays out of the log.

The dictation service writes the list; the tray and the GNOME extension (via
D-Bus) read it. A small file is the simplest way to share it between those
separate processes.
"""
import contextlib
import json
import os
import tempfile

from .logger import FailureNotes, setup_logger
from .normalize import LINE_BREAK

logger = setup_logger(__name__)
# Said once: the path is looked up on every dictation.
_no_folder = FailureNotes()

MAX_ENTRIES = 20
PREVIEW_CHARS = 50
# Behind another change to the list, then go ahead: a dictation waits for this.
LOCK_WAIT_S = 2.0


def history_path():
    """The history file, in the user's (or the Flatpak's) private runtime
    folder, so a host install and the Flatpak keep separate lists. None when
    the session has no private runtime folder: the locks' /tmp fallback may
    be on disk, where dictated text would outlive the session, so then
    nothing is kept."""
    from .single_instance import private_session_dir
    base = private_session_dir()
    if base is None:
        if _no_folder.failed("history", "no private runtime folder"):
            logger.info("Recent Dictations keeps nothing: this session has no private "
                        "runtime folder")
        return None
    # The pre-rename name, kept: the list is for getting lost text back, and
    # an update's restart within the session mustn't empty it.
    return os.path.join(base, "talktype-history.json")


@contextlib.contextmanager
def _one_change_at_a_time():
    """Held around each change to the list, across processes: a dictation
    finishing as Clear History ran wrote the cleared entries back. Behind a
    change that doesn't end in LOCK_WAIT_S, or with no lock to be had, it
    goes ahead anyway: Recent Dictations must not hold up a dictation.
    Nothing is locked when no list is kept (history_path)."""
    from . import single_instance
    if history_path() is None:
        yield
        return
    try:
        lock = single_instance.open_lock("history.lock")
    except OSError as e:
        logger.warning(f"Recent Dictations: no lock, changing them anyway ({e})")
        yield
        return
    with lock:
        if not single_instance.claim(lock, wait_s=LOCK_WAIT_S):
            logger.warning("Recent Dictations: another change doesn't end; going ahead")
        yield


def get_entries():
    """Recent dictations, newest first. Never raises."""
    path = history_path()
    if path is None:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
        return [e for e in entries if isinstance(e, str)][:MAX_ENTRIES]
    except (OSError, ValueError):
        return []


def _clean(text):
    """*text* as kept: a spoken "new line" as a real newline, so the copied
    text pastes the way it was meant to look."""
    return (text or "").replace(LINE_BREAK, "\n").strip()


def add_entry(text, replaces=None):
    """Remember *text* as the newest dictation. *replaces* is an entry it grew
    out of (Type at pause: the same recording, a phrase shorter), which goes.
    Never raises: history is a convenience and must not get in the way of
    the dictation itself."""
    text = _clean(text)
    if not text:
        return
    old = _clean(replaces)
    with _one_change_at_a_time():
        _save([text] + [e for e in get_entries() if e != text and e != old])


def forget(text):
    """Take *text* back out: it went in first, then wasn't typed after all
    (a password field), so it may be the password itself. Never raises."""
    text = _clean(text)
    with _one_change_at_a_time():
        entries = get_entries()
        if text in entries:
            _save([e for e in entries if e != text])


def _save(entries):
    """Write *entries* (newest first, cut to MAX_ENTRIES) as the list, whole
    or not at all; nothing without a private runtime folder. Never raises."""
    path = history_path()
    if path is None:
        return
    try:
        # Write to a temp file and rename, so a reader never sees half a file.
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".talktype-history-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:  # mkstemp makes it 0600
                json.dump(entries[:MAX_ENTRIES], f)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise
    except Exception as e:
        logger.warning(f"Could not save dictation history: {e}")


def clear():
    """Forget every recent dictation."""
    path = history_path()
    if path is None:
        return
    with _one_change_at_a_time():
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Could not clear dictation history: {e}")


def paste_last_hint(hotkey):
    """The Recent Dictations menu's line about the Paste Last Dictation
    hotkey (config paste_last_hotkey), or None when it is turned off."""
    return f"{hotkey} types the newest one again" if hotkey else None


def full_text_lines(text, width=60, max_lines=10):
    """*text* wrapped into menu-sized lines, for the hover view in the tray.

    Keeps the dictation's own line breaks, drops blank lines (an empty menu
    item renders as a stray gap), and stops at *max_lines* with an ellipsis.
    Only the display is trimmed; Copy always copies the whole dictation.
    """
    import textwrap
    lines = []
    for paragraph in text.splitlines():
        lines.extend(textwrap.wrap(paragraph, width=width))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + " \u2026"
    return lines


def preview(text, limit=PREVIEW_CHARS):
    """One-line menu label for *text*: line breaks flattened, long text cut."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"
