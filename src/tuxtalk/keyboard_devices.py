"""Opening and closing the keyboards under /dev/input, one way for everything
that reads them: the dictation service's hotkey loop (hotkeys) and the
first-run hotkey test (hotkey_test_dialog). Both callers hand in evdev's
list_devices and InputDevice: the hotkey test also runs where evdev is
missing, and their tests stand in for both."""
import contextlib

from . import uinput_helper
from .logger import setup_logger

logger = setup_logger(__name__)

# How often to look for keyboards plugged in (or back) meanwhile: often enough
# that one starts working on its own, rare enough that opening every node under
# /dev/input each time costs nothing.
RESCAN_SECONDS = 3.0


def open_new_keyboards(devices, list_devices, input_device) -> list:
    """Open the keyboards that aren't in *devices* yet, told apart by their
    node path, add them to *devices* and return them. evdev opens them
    non-blocking, which every reader relies on.

    Anything else (a mouse, the power button, a game controller, which any
    user may open and which reports keys too) is closed at once rather than
    whenever it's garbage-collected, since every scan opens each node again.
    A node that can't be opened (no permission, unplugged mid-scan) is
    skipped and logged at debug level: every scan meets the same ones."""
    known = {getattr(d, "path", None) for d in devices}
    try:
        paths = list_devices()
    except OSError as e:
        # evdev checks that a node exists, then stats it: an unplug in between.
        logger.info(f"A device went while listing the input devices; next scan: {e}")
        return []
    added = []
    for path in paths:
        if path in known:
            continue
        try:
            dev = input_device(path)
        except OSError as e:
            logger.debug(f"Could not open {path}: {e}")
            continue
        if not uinput_helper.is_keyboard_device(dev):
            close_devices([dev])
            continue
        devices.append(dev)
        added.append(dev)
        logger.info(f"Reading keyboard {dev.name!r} at {path}")
    return added


def close_devices(devices):
    """Close *devices*; one unplugged meanwhile is closed already."""
    for dev in devices:
        with contextlib.suppress(OSError):
            dev.close()
