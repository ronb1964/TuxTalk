"""Which dictation keys the desktop is asked to hold back from the focused
app, in Qt's spelling.

The service reads the keyboard below the desktop, so the desktop passes the
hotkey on to the focused app too, and a terminal prints a "~". Each desktop
has its own way to hold a key back: a KWin script on KDE (kwin_hotkeys), the
GNOME extension (gnome_hotkeys), COSMIC's custom shortcuts (cosmic_hotkeys)
and an X grab elsewhere on X11 (x11_hotkeys). Which keys are claimed is
decided once, here, so the desktops can't drift apart: F-keys, Scroll Lock
and modifier combos, never a bare letter. Keys are named the way Qt names
them ("Shift+F8", "ScrollLock", "Meta+F9"), which KDE reads as they are and
the others turn into their own syntax; qt_key_code gives the number KDE
reports a key in, for kwin_hotkeys alone.
"""

# TuxTalk's modifier names -> Qt's.
_QT_MODIFIERS = {"CTRL": "Ctrl", "SHIFT": "Shift", "ALT": "Alt", "SUPER": "Meta"}
_FKEYS = {f"F{i}" for i in range(1, 13)}
# The other dictation keys a desktop can hold back: TuxTalk's name (upper
# case) -> Qt's name and code. Right Ctrl and Right Alt aren't among them: a
# modifier on its own can't be a shortcut, and it types nothing into the app.
_QT_KEYS = {"SCROLL LOCK": ("ScrollLock", 0x01000026)}
_QT_KEY_CODES = dict(_QT_KEYS.values())
# Qt's names for those keys where the X keysym (GNOME, COSMIC) differs.
QT_KEYSYMS = {"ScrollLock": "Scroll_Lock"}


def qt_key_sequence(name):
    """TuxTalk's hotkey name ("F8", "Ctrl+Alt+V") as a Qt key sequence, or None.

    A bare key is only claimed if it's an F-key or Scroll Lock. Claiming a bare
    letter would stop the user typing that letter anywhere while the service runs.
    """
    if not name or not name.strip():
        return None
    parts = [p.strip().upper() for p in name.split("+")]
    key = parts[-1]
    bare = key in _FKEYS or key in _QT_KEYS
    if not (bare or (len(key) == 1 and "A" <= key <= "Z")):
        return None
    mods = []
    for part in parts[:-1]:
        if part not in _QT_MODIFIERS:
            return None
        mods.append(_QT_MODIFIERS[part])
    if not mods and not bare:
        return None
    return "+".join(mods + [_QT_KEYS[key][0] if key in _QT_KEYS else key])


# Qt's codes for a modifier and a key, as kglobalaccel reports shortcuts.
_QT_MODIFIER_CODES = {"Shift": 0x02000000, "Ctrl": 0x04000000, "Alt": 0x08000000,
                      "Meta": 0x10000000}
_QT_F1 = 0x01000030


def qt_key_code(sequence):
    """The number KDE's kglobalaccel reports a key in, for a key in Qt's
    spelling as qt_key_sequence gives it ("Shift+F8", "ScrollLock",
    "Meta+F9"), or None. It takes Qt's names, not TuxTalk's: "Meta", not
    "Super"."""
    if not sequence:
        return None
    *mods, key = sequence.split("+")
    if key in _FKEYS:
        code = _QT_F1 + int(key[1:]) - 1
    elif key in _QT_KEY_CODES or len(key) == 1:
        code = _QT_KEY_CODES.get(key) or ord(key)
    else:
        return None
    for mod in mods:
        code |= _QT_MODIFIER_CODES[mod]
    return code


def keys_to_claim(cfg):
    """[(role, what it does, Qt key)] for the hotkeys in *cfg* a desktop can
    hold back, each key once. With voice editing on, Shift and a dictation
    key starts a voice edit (hotkeys._held_shift), so that is claimed too."""
    roles = (
        ("hold", "hold to talk", getattr(cfg, "hotkey", "")),
        ("toggle", "tap to start and stop", getattr(cfg, "toggle_hotkey", "")),
        ("voice-commands", "voice commands", getattr(cfg, "voice_commands_hotkey", "")),
        ("paste-last", "paste last dictation", getattr(cfg, "paste_last_hotkey", "")),
    )
    if getattr(cfg, "voice_editing", False):
        roles += tuple((f"edit-{role}", "voice edit", f"Shift+{name}")
                       for role, _what, name in roles[:2] if name)
    out, seen = [], set()
    for role, what, name in roles:
        key = qt_key_sequence(name)
        if key and key not in seen:   # one key can only be registered once
            seen.add(key)
            out.append((role, what, key))
    return out
