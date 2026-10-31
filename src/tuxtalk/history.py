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
separate processes. Each change to it is made holding a lock they share
(_change).
"""
import contextlib
import json
import os
import tempfile
import threading

from .logger import FailureNotes, setup_logger
from .normalize import LINE_BREAK

logger = setup_logger(__name__)
# Said once: the path is looked up on every dictation.
_no_folder = FailureNotes()

MAX_ENTRIES = 20
PREVIEW_CHARS = 50
# Behind another change to the list, a dictation goes on without its entry
# after this.
LOCK_WAIT_S = 2.0
# A removal (a refused password, Clear History) waits longer: it never runs
# while words are being typed.
REMOVAL_WAIT_S = 10.0


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


def _change(make, wait_s, removal=False):
    """Run *make*() holding the lock the processes sharing the list take
    for each change: a dictation finishing as Clear History ran wrote the
    cleared entries back. Nothing when no list is kept (history_path).

    Without the lock within *wait_s*, a stuck holder (a frozen tray): an
    addition is left out, so history is best-effort then; a *removal* (a
    refused password, Clear History) is made anyway. Privacy wins over the
    rare case of the frozen holder waking and writing its old list back
    (CODE_HEALTH row 70).
    """
    if history_path() is None:
        return
    lock = _take_lock(wait_s)
    if lock is not None:
        with lock:
            make()
    elif removal:
        logger.warning("Recent Dictations: removing without the lock, which is held elsewhere")
        make()
    else:
        logger.warning("Recent Dictations: this dictation isn't kept; the list is held elsewhere")


def _take_lock(wait_s):
    """The history lock file, held by this process, or None (logged) when
    it can't be had within *wait_s*. Every error is caught here, before the change runs: a
    full runtime folder makes writing the PID in the lock fail, and that
    must not stop the dictation being typed."""
    from . import single_instance
    try:
        lock = single_instance.open_lock("history.lock")
    except OSError as e:
        logger.warning(f"Recent Dictations: no lock to be had ({e})")
        return None
    try:
        if single_instance.claim(lock, wait_s=wait_s):
            return lock
        logger.warning("Recent Dictations: another change to them doesn't end")
    except OSError as e:
        logger.warning(f"Recent Dictations: could not lock them ({e})")
    with contextlib.suppress(OSError):    # a PID that couldn't be written fails again here
        lock.close()
    return None


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
    Never raises, and waits LOCK_WAIT_S at most: history is a convenience and
    must not get in the way of the dictation itself."""
    text = _clean(text)
    if not text:
        return
    old = _clean(replaces)
    _change(lambda: _save([text] + [e for e in get_entries() if e != text and e != old]),
            LOCK_WAIT_S)


def forget(text, put_back=""):
    """Take *text* back out: it went in first, then wasn't typed after all
    (a password field), so it may be the password itself. *put_back* is the
    entry it grew out of (Type at pause), which goes back in its place.
    A removal (_change): it waits REMOVAL_WAIT_S, then goes ahead. Never raises."""
    text, put_back = _clean(text), _clean(put_back)

    def make():
        entries = get_entries()
        if text not in entries:
            return
        entries = [e for e in entries if e != text]
        if put_back:
            entries = [put_back] + [e for e in entries if e != put_back]
        _save(entries)
    _change(make, REMOVAL_WAIT_S, removal=True)


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
    """Forget every recent dictation; False when the list couldn't be
    deleted. A removal (_change): it waits REMOVAL_WAIT_S, so never on GTK's
    thread (clear_in_background)."""
    gone = [True]

    def make():
        try:
            os.remove(history_path())
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Could not clear dictation history: {e}")
            gone[0] = False
    _change(make, REMOVAL_WAIT_S, removal=True)
    return gone[0]


def clear_in_background(then=None):
    """Clear History from a menu (the tray's, the GNOME extension's): clear()
    on a thread of its own, then *then*() on that thread. A clear that
    failed is said in a notification, since the list still shows what was
    asked to go."""
    def run():
        if not clear():
            from .notify import notify
            notify("Recent Dictations not cleared",
                   "TuxTalk couldn't delete the list. Try Clear History again.")
        if then is not None:
            then()
    # Not a daemon: quitting the tray meanwhile must not drop the clear.
    threading.Thread(target=run, name="clear-history").start()


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
