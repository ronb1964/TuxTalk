"""Right Ctrl and Right Alt as dictation keys.

Both are modifiers: a key TuxTalk presses while one is down arrives as a
shortcut (Ctrl+W closes the tab), and a lone tap of Alt opens an app's menu
bar. The hotkey loop notes its keyboards and dictation keys here, and typing
asks before each key whether it must wait.
"""
import time

from evdev import ecodes

from .hotkey_keys import MODIFIER_CODES, keys_down
from .uinput_helper import is_keyboard_device

# The keyboards the hotkey loop reads and its dictation keys that are
# modifiers; set by note().
_noted = ([], frozenset())


def note(devices, *keys) -> None:
    """Remember the hotkey loop's *devices* and which of its dictation *keys*
    are modifiers, for wait_while_held."""
    global _noted
    _noted = (devices, frozenset(k for k in keys if k in MODIFIER_CODES))


def wait_while_held():
    """A function for virtual_keyboard.send to call before each key: it waits
    while a Right Ctrl or Right Alt dictation key is down, however long.
    None when neither is a dictation key."""
    devices, modifiers = _noted
    if not modifiers:
        return None
    keyboards = [d for d in list(devices) if is_keyboard_device(d)]

    def before_press():
        while keys_down(keyboards) & modifiers:
            time.sleep(0.02)
    return before_press


def right_alt_alone(event, dictation_keys, held_modifiers) -> bool:
    """Whether *event* is a Right Alt dictation key going down with no other
    modifier held, which app._guard_alt_tap keeps from opening a menu. Not
    with another modifier: the key isn't dictating then, and if Left Ctrl is
    down, the guard letting go of it could let it go there too."""
    return (event.value == 1 and event.code == ecodes.KEY_RIGHTALT
            and event.code in dictation_keys
            and not held_modifiers - {ecodes.KEY_RIGHTALT})
