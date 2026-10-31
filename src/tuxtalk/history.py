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
separate processes. Each change to it is made holding a lock they share;
one that can't have the lock in time waits in memory, never on disk, and
is tried again (_change).
"""
import contextlib
import json
import os
import tempfile
import threading
import time

from .logger import FailureNotes, setup_logger
from .normalize import LINE_BREAK

logger = setup_logger(__name__)
# Said once: the path is looked up on every dictation.
_no_folder = FailureNotes()

MAX_ENTRIES = 20
PREVIEW_CHARS = 50
# Behind another change to the list, a dictation goes on after this, and
# its entry goes in once the list is free.
LOCK_WAIT_S = 2.0
# A removal (a refused password, Clear History) waits longer: it never runs
# while words are being typed.
REMOVAL_WAIT_S = 10.0
# Changes still waiting for the lock are tried again this often, this long.
RETRY_EVERY_S = 2.0
RETRY_FOR_S = 600.0

# Changes not made yet for want of the lock, oldest first, each with the
# time it was asked for (time.monotonic, the same clock in every process).
_waiting = []
_waiting_guard = threading.Lock()
_retrying = False       # a thread tries the waiting changes again (_retry)


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


def _cleared_marker():
    """The file Clear History writes the time it was asked for into, next to
    the list, so a dictation asked for before it can't come back after it."""
    return os.path.join(os.path.dirname(history_path()), "tuxtalk-history-cleared")


def _change(change, wait_s):
    """Make *change* (see _apply) after every change still waiting, in the
    order they were asked for, holding the cross-process lock: a dictation
    finishing as Clear History ran wrote the cleared entries back. True when
    it's made. Without the lock within *wait_s* nothing is written, since a
    change made beside another can bring cleared entries back: it waits in
    memory, and a thread tries again (_retry). A Clear drops the changes
    waiting before it, which it would undo anyway. Nothing is kept, or
    waits, when no list is kept (history_path)."""
    if history_path() is None:
        return True
    with _waiting_guard:
        if change[0] == "clear":
            _waiting.clear()
        _waiting.append((change, time.monotonic()))
        adds = [i for i, (waiting, _) in enumerate(_waiting) if waiting[0] == "add"]
        for i in reversed(adds[:-MAX_ENTRIES]):     # never more than the list holds
            del _waiting[i]
    if _make_waiting(wait_s):
        return True
    _retry_in_background()
    return False


def _make_waiting(wait_s):
    """Every change waiting, in order, under the lock; False when the lock
    wasn't had within *wait_s* (they wait on)."""
    lock = _take_lock(wait_s)
    if lock is None:
        return False
    with lock:
        with _waiting_guard:
            batch = _waiting[:]
            _waiting.clear()
        for change, asked_at in batch:
            _apply(change, asked_at)
    return True


def _apply(change, asked_at):
    """One change to the list, with the lock held: ("add", text, the entry
    it replaces), ("forget", text, the entry to put back in its place) or
    ("clear",). An entry asked for before the last Clear History, which
    another process made while this one waited, isn't put in."""
    kind, *args = change
    if kind == "clear":
        _remove_list(asked_at)
        return
    text, other = args
    entries = get_entries()
    if kind == "forget":
        if text not in entries:
            return
        entries = [e for e in entries if e != text]
        new, old = other, ""            # what goes back in its place, if anything
    else:
        new, old = text, other
    if new and asked_at >= _cleared_at():
        entries = [new] + [e for e in entries if e != new and e != old]
    elif kind == "add":
        return          # dictated before a Clear another process made meanwhile
    _save(entries)


def _cleared_at():
    """When the last Clear History was asked for, as time.monotonic; 0 when
    it can't be read (never cleared this session)."""
    try:
        with open(_cleared_marker(), encoding="utf-8") as f:
            return float(f.read())
    except (OSError, ValueError):
        return 0.0


def _remove_list(asked_at):
    """Clear History, with the lock held: the list goes, and the time it was
    asked for is kept for _apply."""
    try:
        with open(_cleared_marker(), "w", encoding="utf-8") as f:
            f.write(repr(asked_at))
    except OSError as e:
        logger.warning(f"Could not note when dictation history was cleared: {e}")
    try:
        os.remove(history_path())
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"Could not clear dictation history: {e}")


def _retry_in_background():
    """Start _retry, unless it runs already."""
    global _retrying
    with _waiting_guard:
        if _retrying:
            return
        _retrying = True
    threading.Thread(target=_retry, name="history-retry", daemon=True).start()


def _retry():
    """Try the waiting changes again every RETRY_EVERY_S until they're all
    made, for RETRY_FOR_S at most: a refused password or a Clear must not
    be lost because something held the list for a while. Any still waiting
    after that go in with the next change."""
    global _retrying
    deadline = time.monotonic() + RETRY_FOR_S
    while True:
        time.sleep(RETRY_EVERY_S)
        made = _make_waiting(LOCK_WAIT_S)
        with _waiting_guard:
            left = len(_waiting)
            if (made and not left) or time.monotonic() >= deadline:
                _retrying = False
                break
    if left:
        logger.warning(f"Recent Dictations: {left} change(s) still not made; "
                       "they go in with the next one")


def _take_lock(wait_s):
    """The history lock file, held by this process, or None (logged) when
    it can't be had within *wait_s*. Every error is caught here, before the change runs: a
    full runtime folder makes writing the PID in the lock fail, and that
    must not stop the dictation being typed."""
    from . import single_instance
    try:
        lock = single_instance.open_lock("history.lock")
    except OSError as e:
        logger.warning(f"Recent Dictations not changed yet: no lock to be had ({e})")
        return None
    try:
        if single_instance.claim(lock, wait_s=wait_s):
            return lock
        logger.warning("Recent Dictations not changed yet: another change to them doesn't end")
    except OSError as e:
        logger.warning(f"Recent Dictations not changed yet: could not lock them ({e})")
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
    must not get in the way of the dictation itself. Behind a change that
    doesn't end, the entry goes in once it has (_change)."""
    text = _clean(text)
    if text:
        _change(("add", text, _clean(replaces)), LOCK_WAIT_S)


def forget(text, put_back=""):
    """Take *text* back out: it went in first, then wasn't typed after all
    (a password field), so it may be the password itself. *put_back* is the
    entry it grew out of (Type at pause), which goes back in its place.
    Waits REMOVAL_WAIT_S, then keeps trying in the background. Never raises."""
    _change(("forget", _clean(text), _clean(put_back)), REMOVAL_WAIT_S)


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
    """Forget every recent dictation. True when they're gone; False when
    another change held the list past REMOVAL_WAIT_S, and the clear is made
    in the background once it lets go. It waits: never on GTK's thread
    (clear_in_background)."""
    return _change(("clear",), REMOVAL_WAIT_S)


def clear_in_background(then=None):
    """Clear History from a menu (the tray's, the GNOME extension's): clear()
    on a thread of its own, then *then*() on that thread. A clear that has
    to wait for a stuck change is said in a notification, since the list
    still shows what was asked to go."""
    def run():
        if not clear():
            from .notify import notify
            notify("Recent Dictations not cleared yet",
                   "Another change to them hasn't finished. They're cleared as soon as it has.")
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
