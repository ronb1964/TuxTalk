"""Left Ctrl kept down after a Left Ctrl+Space hold, pressed again for the desktop.

A hold hides Left Ctrl with Space (hotkeys._start_hold_recording): the
desktop is told it went up, so what TuxTalk types meanwhile isn't a row of
Ctrl shortcuts. Someone who lets go of Space first and keeps Ctrl down
expects Ctrl to still be Ctrl: Ctrl+Z after the dictation, not a "z". So
when that grab lets go (keyboard_grab.release_all) and the Ctrl is still
held, TuxTalk presses Left Ctrl on its own virtual keyboard, which the
desktop counts with the rest, and lets go of it when the real one comes up,
when a hold hides it again, or when typing gives up waiting for it.

Not on the real keyboard, and not by the hotkey helper: pressing it again
there writes the kernel's state of that key, and a release that lands in
between is dropped by the kernel, so Ctrl could stay down after it was let
go. Left as the hold left it, the real key is down in the kernel, so its
release always comes through. TuxTalk's keyboard goes away with TuxTalk, and
the kernel lets go of its keys then.

The press waits for the virtual keyboard, so it never lands between two
events of a key TuxTalk types, and typing waits while it's down
(modifier_hotkeys.wait_while_held), so nothing typed becomes a Ctrl shortcut.
"""
import threading

from evdev import ecodes

from . import virtual_keyboard
from .hotkey_keys import keys_down
from .logger import setup_logger

logger = setup_logger(__name__)

CODE = ecodes.KEY_LEFTCTRL

# Taken before virtual_keyboard's lock, never while holding it.
_lock = threading.Lock()
_pressed = False


def pressed() -> bool:
    """Whether TuxTalk holds Left Ctrl down for the desktop now. Without the
    lock: typing asks with the virtual keyboard's lock held."""
    return _pressed


def press_if_held(keyboards) -> None:
    """A grab that hid Left Ctrl has let go of *keyboards*: press it again if
    one of them still holds it."""
    global _pressed
    with _lock:
        if _pressed or CODE not in keys_down(keyboards):
            return
        _pressed = virtual_keyboard.press(CODE, 1)
        if _pressed:
            logger.info("Left Ctrl still held after the hold: pressed it again for the desktop")


def let_go(keyboards=()) -> None:
    """Let go of the Left Ctrl TuxTalk holds, unless one of *keyboards* still
    holds the real one. A failed write closes the virtual keyboard, which
    lets go of it too."""
    global _pressed
    with _lock:
        if not _pressed or CODE in keys_down(keyboards):
            return
        _pressed = False
        virtual_keyboard.press(CODE, 0)
