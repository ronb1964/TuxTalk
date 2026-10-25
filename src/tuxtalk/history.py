"""
Recent dictations: the last few things the user dictated, so text that went
to the wrong window (focus stolen by a notification, a form that reloaded,
nothing focused at all) can be copied again instead of being spoken twice.

Privacy: the list lives in the user's runtime directory (XDG_RUNTIME_DIR,
normally /run/user/<uid>). That is RAM-backed tmpfs, readable only by the
user, and wiped at logout, so dictated text is never written to disk. This
matches TuxTalk's promise that dictated text stays out of the log.

The dictation service writes the list; the tray and the GNOME extension (via
D-Bus) read it. A small file is the simplest way to share it between those
separate processes.
"""
import json
import os
import tempfile

from .logger import setup_logger
from .normalize import LINE_BREAK

logger = setup_logger(__name__)

MAX_ENTRIES = 20
PREVIEW_CHARS = 50



def _history_path():
    """The history file, in the user's (or the Flatpak's) runtime directory."""
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    # Inside a Flatpak, use its private runtime dir (same rule as app/tray
    # _runtime_dir) so a host install and the Flatpak keep separate lists.
    fid = os.environ.get("FLATPAK_ID")
    if fid:
        app_dir = os.path.join(base, "app", fid)
        if os.path.isdir(app_dir):
            base = app_dir
    return os.path.join(base, "talktype-history.json")


def get_entries():
    """Recent dictations, newest first. Never raises."""
    try:
        with open(_history_path(), encoding="utf-8") as f:
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
    _save([text] + [e for e in get_entries() if e != text and e != old])


def forget(text):
    """Take *text* back out: it went in first, then wasn't typed after all
    (a password field), so it may be the password itself. Never raises."""
    text = _clean(text)
    entries = get_entries()
    if text in entries:
        _save([e for e in entries if e != text])


def _save(entries):
    try:
        path = _history_path()
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
    try:
        os.remove(_history_path())
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
