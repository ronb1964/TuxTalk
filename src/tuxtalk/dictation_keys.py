"""Which keys may be a dictation key, as Preferences and config.toml name them.

A dictation key must never be one that types: held for a dictation, it would
start one with every press, and its press would be lost from what the user
types. So a key on its own is one of the keys that type nothing (F1-F24,
Right Ctrl, Right Alt, Scroll Lock), and any other key needs Ctrl, Alt or
Super with it ("Alt+D", "Ctrl+Shift+K"); Shift alone types capitals, so it
never counts. Left Ctrl+Space is the one combo with a key that types, kept
for those who asked for it (ctrl_space.py).

Names are checked as text, so the settings can be checked anywhere; the
hotkey loop turns them into keyboard codes (hotkey_keys).
"""
CTRL_SPACE = "Left Ctrl+Space"

# The keys that may be a dictation key on their own: they type nothing.
SINGLE_KEYS = frozenset({*(f"F{n}" for n in range(1, 25)), "Right Ctrl", "Right Alt",
                         "Scroll Lock"})
# The modifiers a combo may name. Shift only with one of the others.
MODIFIERS = ("Ctrl", "Alt", "Super", "Shift")
_UNLOCKING = frozenset({"Ctrl", "Alt", "Super"})
# The keys a combo may end in: a letter (the key that types it on the
# user's layout, hotkey_keys.keycode_from_name) or an F-key.
_COMBO_KEYS = frozenset({*(chr(c) for c in range(ord("A"), ord("Z") + 1)),
                         *(f"F{n}" for n in range(1, 25))})


def canonical(name: str) -> str:
    """*name* spelled as Preferences shows it ("alt+d" -> "Alt+D"), or
    unchanged when it isn't a key TuxTalk knows."""
    if not name:
        return ""
    by_upper = {key.upper(): key for key in (*SINGLE_KEYS, CTRL_SPACE)}
    if name.strip().upper() in by_upper:
        return by_upper[name.strip().upper()]
    *mods, key = [part.strip() for part in name.split("+")]
    mods_by_upper = {mod.upper(): mod for mod in MODIFIERS}
    if not mods or any(mod.upper() not in mods_by_upper for mod in mods):
        return name
    ordered = sorted({mods_by_upper[mod.upper()] for mod in mods}, key=MODIFIERS.index)
    return "+".join([*ordered, key.upper()])


def problem(name: str) -> str | None:
    """Why *name* can't be a dictation key, in words for the user, or None
    when it can ("" means none chosen, which is allowed)."""
    if not name or not name.strip():
        return None
    name = canonical(name)
    if name in SINGLE_KEYS or name == CTRL_SPACE:
        return None
    parts = name.split("+")
    if len(parts) == 1:
        return (f"{name} types, so it can't be a dictation key on its own. Hold Ctrl, Alt "
                "or the Windows key with it (Alt+D), or pick a key that doesn't type.")
    *mods, key = parts
    if any(mod not in MODIFIERS for mod in mods) or len(set(mods)) != len(mods):
        return f"{name} isn't a key combination TuxTalk knows."
    if not set(mods) & _UNLOCKING:
        return (f"{name} types, so it can't be a dictation key. Hold Ctrl, Alt or the "
                "Windows key with it.")
    if key not in _COMBO_KEYS:
        return f"{name}: a combination ends in a letter or an F-key."
    return None
