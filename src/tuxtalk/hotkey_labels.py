"""How Preferences and the first-run hotkey test name the dictation keys:
the hotkey dropdowns' entries, each saying what else its key does, and the
hands-free tickbox's words."""

from .config import HOTKEY_CHOICES

# What else a key does, named in the hotkey dropdowns so nobody picks one
# blind: dictating with F5 also reloads the page in a browser. Right Ctrl or
# Right Alt held down for a shortcut (Right Ctrl+C) starts or stops a
# dictation instead, so it can't be both.
HOTKEY_CLASHES = {
    "F1": "opens Help in many apps",
    "F5": "reloads the page in browsers",
    "F11": "full screen in browsers",
    "F12": "developer tools in browsers",
    "Right Ctrl": "stops working as Ctrl",
    "Right Alt": "stops working as Alt",
}


def hands_free_label(toggle_key):
    """The hands-free tickbox's words, naming the toggle key: "Double-tap F9
    for hands-free: stop after" (its seconds box and "seconds of silence"
    follow it)."""
    key = toggle_key or "the toggle key"
    return f"Double-tap {key} for hands-free: stop after"


def hotkey_label(key, altgr_types=None, compose=False):
    """'F5 (reloads the page in browsers)', or just the key. *altgr_types*:
    when the keyboard layout's Right Alt is AltGr, the characters it types
    that picking it takes away ("@", "€"; keyboard_layout.altgr_characters).
    *compose*: the keyboard options make *key* the Compose key."""
    clash = HOTKEY_CLASHES.get(key)
    if key == "Right Alt" and altgr_types is not None:
        clash = "your AltGr key"
        if altgr_types:
            clash += f": stops typing {' and '.join(altgr_types)}"
    elif compose:
        clash = "your Compose key: stops working as Compose"
    return f"{key} ({clash})" if clash else key


def _compose_names():
    """The dictation keys the keyboard options make Compose, by name."""
    from .keyboard_layout import compose_keys
    from evdev import ecodes
    codes = compose_keys()
    return {name for name, code in HOTKEY_CHOICES.items() if ecodes.ecodes[code] in codes}


def layout_label(key):
    """hotkey_label for *key* on the active keyboard layout, for the
    first-run hotkey test: picking Right Alt there takes AltGr away too,
    and a Compose key stops composing."""
    from .keyboard_layout import altgr_characters
    return hotkey_label(key, altgr_characters() if key == "Right Alt" else None,
                        key in _compose_names())


def dropdown_entries():
    """[(key, label)] for the hold and toggle dropdowns, in the order
    config.HOTKEY_CHOICES gives, with Right Alt and Right Ctrl saying what
    the active keyboard layout loses with them."""
    from .keyboard_layout import altgr_characters
    altgr_types = altgr_characters()
    compose = _compose_names()
    return [(key, hotkey_label(key, altgr_types, key in compose)) for key in HOTKEY_CHOICES]
