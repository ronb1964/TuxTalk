"""Stop the dictation hotkeys reaching the focused app on COSMIC.

Same leak as on KDE (see kwin_hotkeys): the service reads the keyboard below
the desktop, so cosmic-comp hands F8 to the focused window as well, and a
terminal types "^[[19~" before the words. cosmic-comp keeps a key from the app
when it's one of its own shortcuts, so while the service runs TuxTalk's
hotkeys are custom shortcuts that run `true`. They go in the file COSMIC
Settings -> Keyboard -> Custom shortcuts edits:
~/.config/cosmic/com.system76.CosmicSettings.Shortcuts/v1/custom, a RON map
of key binding to action.

Checked against cosmic-comp epoch-1.11 on Pop!_OS 24.04 (2026-10), with a
terminal recording every byte it got:
  * no shortcut: F8 typed "^[[19~", and again for every key repeat;
  * a shortcut on F8: the terminal got nothing, press, repeats or release,
    and the command ran once per press;
  * cosmic-comp read the changed file within a second, no logout needed.
"Disable", the action COSMIC has for turning a shortcut off, doesn't help:
cosmic-comp skips those bindings and the key goes on to the app. A binding
matches its modifiers exactly, so Shift+F8 (voice editing) is one of its own.

Unlike KWin's script or the X server's grab, this file outlives the service.
So every stop path takes the entries out again, as on KDE
(stop_dictation_service, the tray when the service dies, the exit hook,
uninstalling), and each says it's TuxTalk's in COSMIC Settings, where one a
crash left behind can be deleted by hand. cosmic-comp drops every custom
shortcut when the file doesn't parse, so it's replaced in one rename, never
written in place, and a file this module can't make sense of is left alone.
COSMIC Settings saves the whole file too, so it's read again just before the
rename and a save made meanwhile is built on rather than written over (one in
the instant between that read and the rename would still be lost).
A key the desktop or the user already has a shortcut on is left to them.

Nothing here may be fatal. Failing to hold a key costs the stray characters,
never dictation.
"""

import os
import re
import tempfile

from . import desktop_detect
from .kwin_hotkeys import QT_KEYSYMS, wanted_shortcuts
from .logger import setup_logger

logger = setup_logger(__name__)

SHORTCUTS_DIR = os.path.join("cosmic", "com.system76.CosmicSettings.Shortcuts", "v1")
# What COSMIC Settings shows for each entry, and how this module tells its
# own entries from the user's.
DESCRIPTION = "TuxTalk dictation key, kept out of apps while dictation is on"
ACTION = 'Spawn("true")'
_ACTION = r'Spawn\(\s*"true"\s*,?\s*\)'      # ACTION, however it's laid out
# cosmic-config writes through files named like this and its watcher skips
# them, so cosmic-comp only ever reads the finished file.
_TEMP_PREFIX = ".atomicwrite-tuxtalk-"

_COSMIC_MODIFIERS = {"Shift": "Shift", "Ctrl": "Ctrl", "Alt": "Alt", "Meta": "Super"}

# One binding's modifiers and key, as COSMIC writes them:
# (modifiers: [Super, Shift], key: "F8", ...). Bindings by keycode alone have
# no key and can't clash with TuxTalk's.
_BINDING = re.compile(r'\(\s*modifiers:\s*\[(?P<mods>[^\]]*)\]\s*,\s*key:\s*"(?P<key>[^"]*)"')
# One of TuxTalk's entries, however COSMIC Settings has laid it out after
# rewriting the file.
_OURS = re.compile(r'[ \t]*\(\s*modifiers:[^()]*description:\s*Some\(\s*"'
                   + re.escape(DESCRIPTION) + r'"\s*\)[^()]*\)\s*:\s*'
                   + _ACTION + r'\s*,?[ \t]*\n?')
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def should_claim() -> bool:
    """COSMIC outside a Flatpak (which gets its keys from the portal)."""
    if os.environ.get("FLATPAK_ID"):
        return False
    return desktop_detect.get_desktop_environment() == "cosmic"


def custom_path() -> str:
    return os.path.join(desktop_detect.config_home(), SHORTCUTS_DIR, "custom")


def _default_paths():
    """COSMIC's built-in shortcuts, wherever the system keeps them."""
    dirs = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    return [os.path.join(d, SHORTCUTS_DIR, "defaults") for d in dirs if d]


def binding(qt_sequence):
    """(modifiers, key) for a key from wanted_shortcuts ("Shift+F8") as COSMIC
    names them: (("Shift",), "F8"). Keys are keysyms: letters in lower case,
    Scroll Lock as "Scroll_Lock".
    cosmic-comp matches a binding by keysym alone (it ignores a binding's
    keycode), so a letter is held on the key that types it in the active
    layout, which is the key hotkey_keys.keycode_from_name listens to."""
    *mods, key = qt_sequence.split("+")
    return (tuple(_COSMIC_MODIFIERS[m] for m in mods),
            key.lower() if len(key) == 1 else QT_KEYSYMS.get(key, key))


def _taken(text):
    """{(frozenset of modifiers, key)} for the bindings in a shortcuts file."""
    return {(frozenset(m.strip() for m in found["mods"].split(",") if m.strip()),
             found["key"].lower())
            for found in _BINDING.finditer(text)}


def _entry(mods, key):
    return (f'    (modifiers: [{", ".join(mods)}], key: "{key}", '
            f'description: Some("{DESCRIPTION}")): {ACTION},\n')


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _write(path, text, expected):
    """Replace *path* with *text* in one rename, keeping its permissions.
    False, and nothing written, if it no longer holds *expected*."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    try:
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        mode = 0o644
    fd, temp = tempfile.mkstemp(prefix=_TEMP_PREFIX, dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(temp, mode)
        if _read(path) != expected:
            os.unlink(temp)
            return False
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    return True


def _edit(path, change):
    """Rewrite *path* as change(its text, or None for no file), which gives
    the new text (None: leave it). COSMIC Settings saves the whole file when
    the user edits a shortcut; if that happened meanwhile, start again from
    what it saved rather than writing over it."""
    for _attempt in range(3):
        current = _read(path)
        new = change(current)
        if new is None or new == current or _write(path, new, current):
            return
    raise OSError(f"{path} kept changing while TuxTalk was editing it")


def _hard_to_edit(text):
    """Comments, where a comma added after the last entry could land inside
    one, or raw strings (r"..."), which can hide one from _STRING. COSMIC
    Settings writes neither; only a file edited by hand has them."""
    return re.search(r'//|/\*|\br#*"', _STRING.sub('""', text)) is not None


def _with_entries(text, entries):
    """*text* (a RON map, or None for no file) with *entries* added before
    its closing brace; None when it doesn't look like a map or is
    _hard_to_edit."""
    if not (text or "").strip():
        return "{\n" + "".join(entries) + "}\n"
    end = text.rfind("}")
    if (not text.lstrip().startswith("{") or text[end + 1:].strip()
            or _hard_to_edit(text)):
        return None
    head = text[:end].rstrip()
    if not head.endswith(("{", ",")):
        head += ","           # RON needs the comma after the last entry
    return head + "\n" + "".join(entries) + text[end:]


class _Unreadable(Exception):
    pass


def claim(cfg) -> bool:
    """Make *cfg*'s hotkeys COSMIC shortcuts. True if any are held.

    Safe to call again after a hotkey change: the old entries go first.
    """
    if not should_claim():
        return False
    path = custom_path()
    held = []

    def change(current):
        theirs = _OURS.sub("", current) if current else current
        taken = _taken(theirs or "")
        for default in _default_paths():
            taken |= _taken(_read(default) or "")
        entries = []
        held.clear()
        for _name, _text, key in wanted_shortcuts(cfg):
            mods, sym = binding(key)
            if (frozenset(mods), sym.lower()) in taken:
                logger.warning(f"COSMIC already has a shortcut on {key}, so it will still "
                               "do that as well as starting dictation")
                continue
            entries.append(_entry(mods, sym))
            held.append(key)
        if not entries:
            return theirs
        new = _with_entries(theirs, entries)
        if new is None:
            raise _Unreadable
        return new

    try:
        _edit(path, change)
    except _Unreadable:
        logger.warning(f"Could not make sense of COSMIC's custom shortcuts in {path}, so "
                       "the hotkeys aren't held back from the focused app")
        return False
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Could not add the hotkeys to COSMIC's shortcuts: {e}")
        return False
    if held:
        logger.info("COSMIC now holds the hotkeys back from the focused app: "
                    + ", ".join(held))
    return bool(held)


def release() -> bool:
    """Take TuxTalk's entries out of COSMIC's shortcuts. Never raises: quit
    path. False when one may be left. Whatever the desktop: uninstalling
    from another one must still clean up after a crash on COSMIC."""
    try:
        _edit(custom_path(), lambda current: _OURS.sub("", current) if current else None)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Could not take the hotkeys out of COSMIC's shortcuts: {e}")
        return False
    return True
