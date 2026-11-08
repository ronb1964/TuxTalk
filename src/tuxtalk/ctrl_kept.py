"""Left Ctrl kept down after a Left Ctrl+Space hold, pressed again for the desktop.

A hold hides Left Ctrl with Space (hotkeys._start_hold_recording): the
desktop is told it went up, so what TuxTalk types meanwhile isn't a row of
Ctrl shortcuts. Someone who lets go of Space first and keeps Ctrl down
expects Ctrl to still be Ctrl: Ctrl+Z after the dictation, not a "z". So
when that grab lets go (keyboard_grab.release_all) and the Ctrl is still
held, TuxTalk presses Left Ctrl on its own virtual keyboard, which the
desktop counts with the rest, and lets go of it when the real one comes up,
when a hold hides it again, when typing gives up waiting for it, or after
HOLD_LIMIT_S.

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
import time

from evdev import ecodes

from . import virtual_keyboard
from .hotkey_keys import keys_down
from .logger import setup_logger

logger = setup_logger(__name__)

CODE = ecodes.KEY_LEFTCTRL
# How long it's held at most. A release the kernel dropped (one inside the
# hold's own hide writes) never comes, and until then every key the user
# types would be a Ctrl shortcut; a Ctrl+Z after a dictation comes sooner.
HOLD_LIMIT_S = 10.0

# Taken before virtual_keyboard's lock, never while holding it.
_lock = threading.Lock()
# True from just before the press until just after the release, so typing,
# which reads it with the virtual keyboard's lock held, never sends a key
# while the Ctrl is down and this says it isn't.
_pressed = False
_pressed_at = 0.0


def pressed() -> bool:
    """Whether TuxTalk holds Left Ctrl down for the desktop now. Without the
    lock: typing asks with the virtual keyboard's lock held."""
    return _pressed


def press_if_held(keyboards) -> None:
    """A grab that hid Left Ctrl has let go of *keyboards*: press it again if
    one of them still holds it, as read once the virtual keyboard is free."""
    global _pressed, _pressed_at
    with _lock:
        if _pressed or CODE not in keys_down(keyboards):
            return
        _pressed = True
        if not virtual_keyboard.press(CODE, 1, wanted=lambda: CODE in keys_down(keyboards)):
            _pressed = False        # let go meanwhile, or the keyboard failed
            return
        _pressed_at = time.monotonic()
        logger.info("Left Ctrl still held after the hold: pressed it again for the desktop")


def let_go(keyboards=()) -> None:
    """Let go of the Left Ctrl TuxTalk holds, unless one of *keyboards* still
    holds the real one. A failed write closes the virtual keyboard, which
    lets go of it too."""
    global _pressed
    with _lock:
        if not _pressed or CODE in keys_down(keyboards):
            return
        virtual_keyboard.press(CODE, 0)
        _pressed = False


def let_go_if_held_too_long() -> None:
    """Let go of the Left Ctrl TuxTalk has held for HOLD_LIMIT_S, whether or
    not the real one reads as down: its release may have been lost."""
    if _pressed and time.monotonic() - _pressed_at >= HOLD_LIMIT_S:
        logger.warning(f"Left Ctrl held for the desktop {HOLD_LIMIT_S:g} s: let go of it")
        let_go()
