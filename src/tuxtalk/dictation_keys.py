"""Which keys may be a dictation key, as Preferences and config.toml name them.

A dictation key must never be one that types: held for a dictation, it would
start one with every press, and its press would be lost from what the user
types. So a key on its own is one of the keys that type nothing (F1-F24,
Right Ctrl, Right Alt, Scroll Lock), and any other key needs Ctrl, Alt or
Super with it ("Alt+D", "Ctrl+Shift+K"); Shift alone types capitals, so it
never counts. Left Ctrl+Space is the one combo with a key that types, kept
for those who asked for it (combo_keys).

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
# The keys of a combo's modifier that keyboard options can make third-level
# keys (AltGr), typing characters (combo_keys.typing_modifiers). Right Alt
# is AltGr on most layouts already, so Alt is only Left Alt.
TYPING_MODIFIER_KEYS = {"Alt": "Left Alt", "Super": "the Windows key", "Ctrl": "Ctrl"}


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
    if key not in _COMBO_KEYS:
        return f"{name}: a combination ends in a letter or an F-key."
    if not set(mods) & _UNLOCKING and key in SINGLE_KEYS:
        return (f"Shift alone doesn't make a combination. Hold Ctrl, Alt or the Windows key "
                f"with {key}, or use {key} on its own.")
    if not set(mods) & _UNLOCKING:
        return (f"{name} types, so it can't be a dictation key. Hold Ctrl, Alt or the "
                "Windows key with it.")
    return None


def same_last_key(first: str, second: str) -> bool:
    """Whether dictation keys *first* and *second* end in the same key (F8
    and Ctrl+F8, Alt+D and Ctrl+D), which the hotkey loop can't tell apart."""
    def last(name):
        return canonical(name).rpartition("+")[2].upper() if name else ""
    return bool(first and second) and last(first) == last(second)


def types_here(name: str, typing_mods) -> str | None:
    """Why combo *name* can't be a dictation key on this keyboard layout,
    where the modifiers in *typing_mods* (combo_keys.typing_modifiers) type
    characters with a letter, or None."""
    mods = canonical(name).split("+")[:-1]
    typing = [mod for mod in mods if mod in typing_mods]
    if not typing:
        return None
    key = TYPING_MODIFIER_KEYS[typing[0]]
    return (f"On your keyboard layout {key} types characters, so {canonical(name)} would "
            "start a dictation with each one typed. Pick a combination without "
            f"{typing[0]}, or a key on its own.")


def refusal(name: str, other: str, voice_editing: bool, typing_mods=frozenset(),
            shortcuts=()) -> str | None:
    """Why *name* can't be picked as a dictation key in Preferences, beside
    *other* (the other dictation key): problem's reasons, the other key's
    own last key, Right Ctrl beside a Ctrl combo (its Ctrl would be the
    combo's), Shift while voice editing (Shift with the dictation key is the
    edit), a modifier that types on this layout (*typing_mods*, types_here),
    or one of TuxTalk's *shortcuts* ((what, name): the cheat sheet, Paste
    Last), which would answer to the press first."""
    why = problem(name)
    if why:
        return why
    name = canonical(name)
    mods = name.split("+")[:-1]
    if same_last_key(name, other):
        return f"Your other dictation key, {other}, ends in the same key."
    other_mods = canonical(other).split("+")[:-1] if other else []
    if "Right Ctrl" in (name, canonical(other)) and "Ctrl" in mods + other_mods:
        return "Right Ctrl and a combination with Ctrl can't be your two dictation keys."
    if voice_editing and "Shift" in mods:
        return ("Voice editing uses Shift with your dictation key, so the key can't have "
                "Shift in it.")
    for what, shortcut in shortcuts:
        their_mods = canonical(shortcut).split("+")[:-1] if shortcut else []
        if same_last_key(name, shortcut) and set(their_mods) <= set(mods):
            return f"{canonical(shortcut)} is your {what} shortcut, and it would answer first."
    return types_here(name, typing_mods)


def warning(name: str, held_back: bool = True) -> str | None:
    """What to know before using combo *name*, which works but may take a
    shortcut over; None for a key on its own. *held_back*: whether the
    desktop keeps the key from the app (hotkey_claims); where it can't, the
    app gets every press too."""
    mods = canonical(name).split("+")[:-1]
    if not mods or name == CTRL_SPACE:
        return None
    if not held_back:
        return (f"On this desktop TuxTalk can't keep {canonical(name)} from your apps, so the "
                "app you're in gets it as well each time (Alt+D jumps to a browser's address "
                "bar). F8 or Scroll Lock does much less there, at most a ~ in a "
                "terminal.")
    if mods == ["Alt"]:
        return ("Many apps open a menu with Alt and a letter (Alt+F is often File). TuxTalk "
                "takes this one over, and can't check which apps use it.")
    return ("Apps may use this shortcut for something of their own. TuxTalk takes it over, "
            "and can't check which apps use it.")
