"""The dictation keys and shortcuts as the keyboard sends them.

The settings name keys the way Preferences shows them ("F8", "Right Ctrl",
"Ctrl+Alt+P"); the hotkey loop reads evdev key codes. This turns one into the
other, follows which modifiers are held, and asks the keyboards which keys
are down.
"""
from typing import NamedTuple

from evdev import ecodes

from .config import HOTKEY_CHOICES

# The dictation keys by upper-case name ("RIGHT CTRL"), as evdev codes.
_HOTKEY_CODES = {name.upper(): ecodes.ecodes[key] for name, key in HOTKEY_CHOICES.items()}

# Modifier key evdev codes for tracking combo hotkeys
MODIFIER_CODES = {
    ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL,
    ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT,
    ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT,
    ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA,
}

# Map modifier names to their evdev code pairs (left + right)
_MODIFIER_NAMES = {
    "CTRL":  (ecodes.KEY_LEFTCTRL,  ecodes.KEY_RIGHTCTRL),
    "SHIFT": (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT),
    "ALT":   (ecodes.KEY_LEFTALT,   ecodes.KEY_RIGHTALT),
    "SUPER": (ecodes.KEY_LEFTMETA,  ecodes.KEY_RIGHTMETA),
}


def keycode_from_name(name: str) -> int | None:
    """Convert key name to evdev keycode. Returns None if name is empty (no hotkey configured)."""
    if not name or not name.strip():
        return None  # No hotkey configured - don't activate any key
    n = name.strip().upper()
    if n in _HOTKEY_CODES:
        return _HOTKEY_CODES[n]
    if len(n) == 1 and "A" <= n <= "Z":
        # The key that types the letter, which is the one KDE, GNOME and COSMIC
        # hold back for us (they match shortcuts by letter): on a German
        # keyboard Ctrl+Alt+Y is the key US keyboards have Z on.
        from .keyboard_layout import shortcut_keycode
        return shortcut_keycode(n.lower())
    return None  # Unknown key name - don't activate


def parse_hotkey_combo(combo_str: str) -> tuple[set[str], int | None] | None:
    """Parse a hotkey combo string like 'Ctrl+Shift+H' into (modifier_names, keycode).

    Returns None if the string is empty or invalid.
    modifier_names is a set of uppercase modifier names (e.g. {'CTRL', 'SHIFT'}).
    keycode is the evdev keycode for the non-modifier key.
    """
    if not combo_str or not combo_str.strip():
        return None

    parts = [p.strip().upper() for p in combo_str.split("+")]
    if len(parts) < 2:
        return None  # Need at least one modifier + one key

    # Last part is the main key, everything before is a modifier
    modifiers = set()
    for part in parts[:-1]:
        if part in _MODIFIER_NAMES:
            modifiers.add(part)
        else:
            return None  # Unknown modifier

    main_key = keycode_from_name(parts[-1])
    if main_key is None:
        return None  # Unknown key

    return (modifiers, main_key)


def check_modifiers_held(required_mods: set[str], held_keys: set[int]) -> bool:
    """Check if all required modifiers are currently held down."""
    for mod_name in required_mods:
        left, right = _MODIFIER_NAMES[mod_name]
        if left not in held_keys and right not in held_keys:
            return False
    return True


def track_modifier(held_modifiers: set, event) -> None:
    """Keep *held_modifiers* up to date with one key *event*: a modifier's
    press or auto-repeat adds it, its release takes it out."""
    if event.code in MODIFIER_CODES:
        if event.value in (1, 2):
            held_modifiers.add(event.code)
        elif event.value == 0:
            held_modifiers.discard(event.code)


def keys_down(keyboards) -> set:
    """The keys down on *keyboards*; one that's gone has none."""
    down = set()
    for dev in keyboards:
        try:
            down |= set(dev.active_keys())
        except OSError:
            pass
    return down


class Hotkeys(NamedTuple):
    """The hotkeys as keycodes, which the input loop keeps in locals and
    rebinds after a live settings change."""
    hold_key: int | None
    toggle_key: int | None
    vc_hotkey_str: str
    voice_cmds_combo: tuple | None
    voice_cmds_main_key: int | None
    paste_last_combo: tuple | None = None

    @classmethod
    def from_settings(cls, cfg) -> "Hotkeys":
        """The hotkeys *cfg* names."""
        vc_hotkey_str = getattr(cfg, "voice_commands_hotkey", "")
        voice_cmds_combo = parse_hotkey_combo(vc_hotkey_str)
        return cls(
            hold_key=keycode_from_name(cfg.hotkey),
            toggle_key=keycode_from_name(cfg.toggle_hotkey) if cfg.toggle_hotkey else None,
            vc_hotkey_str=vc_hotkey_str,
            voice_cmds_combo=voice_cmds_combo,
            voice_cmds_main_key=voice_cmds_combo[1] if voice_cmds_combo else None,
            paste_last_combo=parse_hotkey_combo(getattr(cfg, "paste_last_hotkey", "")),
        )

    def watched(self) -> tuple:
        """The keys whose presses count as using TuxTalk (for Power Saving)."""
        return (self.hold_key, self.toggle_key, ecodes.KEY_ESC, self.voice_cmds_main_key,
                self.paste_last_combo[1] if self.paste_last_combo else None)
