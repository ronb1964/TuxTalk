"""Stop the dictation hotkeys reaching the focused app on other X11 desktops:
Cinnamon, Xfce, MATE and the rest.

Same leak as on KDE and GNOME (see kwin_hotkeys): the service reads the
keyboard below the desktop, so the X server hands F8 to the focused window as
well, and a terminal types "^[[19~" before the words. KDE has its KWin script
and GNOME the extension; here the X server itself can do it. A passive grab
(GrabKey) on the root window makes the server deliver that key to us instead
of the focused window. evdev and the hotkey helper read below X, so they still
see the press and release, and recording is unaffected.

The grab is on a connection the service keeps open for its whole life, made
through XCB for the reason xcb_ctypes gives. The X server drops a
client's grabs when its connection closes, so a stop, the auto-timeout, a
crash or a SIGKILL all hand the keys back by themselves; unlike KDE, nothing
in service_launcher or the tray has to release them. A hotkey change ungrabs
everything on the connection and grabs the new keys.

When a grabbed key goes down, X hands us the whole keyboard until it comes
up. F9 stops a dictation and types it on the press, so with F9 still held
the text would come to us instead of the app. A thread reading the
connection ends that at once, as kglobalaccel does on KDE's X11 session; the
key's release then reaches the app, which ignores it.

The X server's keycodes are evdev's plus 8 (both the evdev and libinput
drivers), so the grab is on the physical key the service listens to: an
F-key or Scroll Lock, or for a letter the key that types it on the active
layout. Which keys are held is decided in hotkey_claims, as for KDE and
GNOME: F-keys, Scroll Lock and modifier combos, never a bare letter.

Nothing here may be fatal: failing to hold a key costs the stray characters,
never dictation.
"""

import ctypes
import os
import threading

from . import desktop_detect
from .hotkey_claims import keys_to_claim
from .logger import setup_logger
from .xcb_ctypes import free, x11_root, xcb_lib

logger = setup_logger(__name__)

_EVDEV_TO_X_KEYCODE = 8
_MODIFIER_MASKS = {"Shift": 1, "Ctrl": 4, "Alt": 8, "Meta": 64}   # Shift, Control, Mod1, Mod4
# A grab matches its modifiers exactly, so it is taken again with Caps Lock
# (Lock) and Num Lock (Mod2) on, or F8 would leak whenever either is.
_LOCK_VARIANTS = (0, 2, 16, 2 | 16)
_GRAB_MODE_ASYNC = 1
_ANY_KEY = 0
_ANY_MODIFIER = 0x8000
_BAD_ACCESS = 10
_KEY_PRESS = 2
_CURRENT_TIME = 0

# (connection, root window) once claim() has connected; kept until exit.
_x = None


def should_claim() -> bool:
    """An X11 session that is neither KDE nor GNOME, outside a Flatpak (which
    gets its keys from the GlobalShortcuts portal)."""
    if os.environ.get("FLATPAK_ID") or not desktop_detect.is_x11_session(os.environ):
        return False
    return desktop_detect.get_desktop_environment() not in ("kde", "gnome")


def x_grab(qt_sequence):
    """(X keycode, modifier mask) for a key from keys_to_claim ("Shift+F8")."""
    from evdev import ecodes

    from .keyboard_layout import shortcut_keycode

    *mods, key = qt_sequence.split("+")
    mask = 0
    for mod in mods:
        mask |= _MODIFIER_MASKS[mod]
    # A letter is the key that types it, as hotkey_keys.keycode_from_name listens for.
    code = shortcut_keycode(key.lower()) if len(key) == 1 else ecodes.ecodes[f"KEY_{key.upper()}"]
    return code + _EVDEV_TO_X_KEYCODE, mask


def _connection():
    """Our X connection and its root window, connecting the first time; None
    when the X server can't be reached. A connection that breaks isn't
    replaced: the X server went away, and the desktop session with it."""
    global _x
    lib = xcb_lib()
    if _x is not None:
        return None if lib.xcb_connection_has_error(_x[0]) else _x
    screen_number = ctypes.c_int(0)
    conn = lib.xcb_connect(None, ctypes.byref(screen_number))   # $DISPLAY
    root = (x11_root(lib, conn, screen_number.value)
            if conn and not lib.xcb_connection_has_error(conn) else None)
    if not root:
        if conn:
            lib.xcb_disconnect(conn)
        return None
    _x = (conn, root)
    threading.Thread(target=_end_keyboard_grabs, args=(conn,), daemon=True,
                     name="tt-x11-hotkeys").start()
    return _x


def _end_keyboard_grabs(conn):
    """Let go of the whole keyboard each time a grabbed key brings it to us
    (see the module docstring). The key stays ours. Returns when the
    connection breaks. XCB connections are thread-safe."""
    lib = xcb_lib()
    while event := lib.xcb_wait_for_event(conn):
        if ctypes.c_uint8.from_address(event).value & 0x7f == _KEY_PRESS:
            lib.xcb_ungrab_keyboard(conn, _CURRENT_TIME)
            lib.xcb_flush(conn)
        free(event)


def claim(cfg) -> bool:
    """Grab *cfg*'s hotkeys, after letting go of any grabbed before. True if
    the X server holds at least one for us."""
    if not should_claim():
        return False
    try:
        x = _connection()
        if x is None:
            logger.warning("Could not reach the X server to hold the hotkeys back")
            return False
        conn, root = x
        lib = xcb_lib()
        lib.xcb_ungrab_key(conn, _ANY_KEY, root, _ANY_MODIFIER)
        # Every request goes out before the first answer is read, so this
        # costs one round trip however many keys there are.
        cookies = []
        for _role, _what, key in keys_to_claim(cfg):
            keycode, mask = x_grab(key)
            cookies.append((key, [lib.xcb_grab_key_checked(
                conn, 0, root, mask | lock, keycode, _GRAB_MODE_ASYNC, _GRAB_MODE_ASYNC)
                for lock in _LOCK_VARIANTS]))
        held = []
        for key, key_cookies in cookies:
            errors = [lib.xcb_request_check(conn, c) for c in key_cookies]
            codes = {e.contents.error_code for e in errors if e}
            for e in errors:
                if e:
                    free(e)
            if not codes:
                held.append(key)
            elif codes == {_BAD_ACCESS}:
                logger.warning(f"Another program already takes {key} (a desktop shortcut, "
                               f"most likely), so pressing {key} will do that as well as "
                               f"dictating")
            else:
                logger.warning(f"The X server would not hold {key} back: error {sorted(codes)}")
        lib.xcb_flush(conn)
        if lib.xcb_connection_has_error(conn):
            # A broken connection answers every check with "no error".
            logger.warning("Lost the X server while holding the hotkeys back")
            return False
    except Exception as e:
        logger.warning(f"Could not hold the hotkeys back from the focused app: {e}")
        return False
    if held:
        logger.info("The X server now holds the hotkeys back from the focused app: "
                    + ", ".join(held))
    return bool(held)
