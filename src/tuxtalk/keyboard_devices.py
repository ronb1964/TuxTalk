"""Which input devices are keyboards, and opening and closing them: one way
for everything that reads /dev/input: the dictation service's hotkey loop
(hotkeys), the first-run hotkey test (hotkey_test_dialog), the permission
check (uinput_helper) and the keyboard grab.

Everything under /dev/input reports itself through the same evdev
interface: keyboards, but also mice, touchpads, the power button, lid
switches, audio jacks and game controllers. These checks tell the keyboards
apart by what each device reports, and tell a device a program made
(through uinput) from hardware. The openers take evdev's list_devices and
InputDevice from the caller: the hotkey test also runs where evdev is
missing, and their tests stand in for both.
"""
import contextlib
import os
import select

from .logger import setup_logger
from .virtual_keyboard import NAME as VIRTUAL_KEYBOARD_NAME

logger = setup_logger(__name__)

try:
    from evdev import ecodes as _ecodes
except ImportError:     # no evdev: nothing can read /dev/input anyway
    _ecodes = None

# Letter keys, used to tell a real keyboard from a device that merely reports
# key events (power buttons, lid switches, mouse buttons, game controllers,
# consumer-control nodes). Built by name, not as a numeric range: evdev codes
# follow the physical scancode order of a PC keyboard, so KEY_A..KEY_Z spans
# the home row and several punctuation keys while leaving KEY_M outside it.
LETTER_KEYS = frozenset(
    getattr(_ecodes, f"KEY_{letter}") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
) if _ecodes else frozenset()


def is_keyboard_device(dev) -> bool:
    """True if *dev* can deliver the hotkey and is therefore worth reading.

    Reading everything under /dev/input would also take in mice, touchpads,
    audio jacks and the power button. And logind lets any user open a game
    controller, which reports key events too, so "some device with keys
    opened" does not mean the keyboard did.

    Two capability checks, in this order:

    1. It must report letter keys. Power buttons, lid switches, audio jacks,
       game controllers and consumer-control nodes report none, so this drops
       them immediately.
    2. A real device must not be a pointing device. Logitech Unifying
       receivers advertise a superset HID descriptor, so a wireless mouse
       claims all 26 letter keys and all 12 F-keys just like a keyboard does.
       What separates them is real pointer motion (REL_X) combined with mouse
       buttons, the same test libinput uses. A uinput keyboard of that shape
       is read all the same (is_remapper_keyboard): kanata takes the real
       keyboards for itself and passes every key on through one, so skipping
       it left TuxTalk hearing no key at all (issue #23).

    TuxTalk's own virtual keyboard, which it types with, is never read: it is
    told by its name.

    Note the check is REL_X specifically, not the EV_REL event type: the K800
    keyboard declares EV_REL while exposing no motion axis, so testing the type
    alone would throw out a real keyboard and let the hotkey leak through.
    """
    caps = _keyboard_capabilities(dev)
    if caps is None:
        return False
    return not _is_pointer(caps) or is_uinput_device(getattr(dev, "path", ""))


def is_remapper_keyboard(dev) -> bool:
    """Whether *dev* is a keyboard read only because a program made it,
    shaped like a mouse too: a key remapper's (kanata's), which carries
    the real keyboards' keys and the mouse's motion, and the text TuxTalk
    types when the remapper has taken its virtual keyboard as well. Never
    grabbed or written to: held, it would freeze the mouse and swallow the
    dictation being typed."""
    caps = _keyboard_capabilities(dev)
    return (caps is not None and _is_pointer(caps)
            and is_uinput_device(getattr(dev, "path", "")))


def _keyboard_capabilities(dev):
    """*dev*'s capabilities when it reports letter keys and isn't TuxTalk's
    own keyboard, else None."""
    if _ecodes is None or getattr(dev, "name", None) == VIRTUAL_KEYBOARD_NAME:
        return None
    try:
        caps = dev.capabilities()
    except Exception as e:      # unplugged, or a node evdev can't describe
        logger.info(f"Could not read what {getattr(dev, 'name', dev)} reports: {e}")
        return None
    keys = set(caps.get(_ecodes.EV_KEY) or [])
    return caps if len(keys & LETTER_KEYS) >= 3 else None


def _is_pointer(caps) -> bool:
    return (_ecodes.REL_X in (caps.get(_ecodes.EV_REL) or [])
            and _ecodes.BTN_LEFT in (caps.get(_ecodes.EV_KEY) or []))


def is_uinput_device(path) -> bool:
    """Whether the input device at *path* (/dev/input/eventN) is one a program
    made through uinput: its sysfs node is under /sys/devices/virtual/input.
    A Bluetooth LE keyboard or mouse (uhid) is elsewhere under
    /sys/devices/virtual and counts as hardware. False when it can't be told."""
    node = os.path.realpath(f"/sys/class/input/{os.path.basename(path)}/device")
    return node.startswith("/sys/devices/virtual/input/")


# How often to look for keyboards plugged in (or back) meanwhile: often enough
# that one starts working on its own. Each look opens and closes every node
# under /dev/input once, a few dozen system calls, at most once every few
# seconds and only while no recording runs.
RESCAN_SECONDS = 3.0

# Nodes logged as having a name evdev can't decode, so the normal log says
# once per plug why that device is left out, not at every scan. Only nodes
# still listed are kept.
_undecodable = set()


def open_new_keyboards(devices, list_devices, input_device) -> list:
    """Open the keyboards that aren't in *devices* yet, told apart by their
    node path, add them to *devices* and return them. evdev opens them
    non-blocking, which every reader relies on.

    Anything else (a mouse, the power button, a game controller, which any
    user may open and which reports keys too) is closed at once rather than
    whenever it's garbage-collected, since every scan opens each node again.
    A node that can't be opened (no permission, unplugged mid-scan) is
    skipped and logged at debug level: every scan meets the same ones. One
    whose name isn't UTF-8, which evdev can't decode, is skipped too, and
    said once in the normal log, since that device then never works. The
    caller says what it opened, in its own words."""
    known = {getattr(d, "path", None) for d in devices}
    try:
        paths = list_devices()
    except OSError as e:
        # evdev checks that a node exists, then stats it: an unplug in between.
        logger.info(f"A device went while listing the input devices; next scan: {e}")
        return []
    _undecodable.intersection_update(paths)
    added = []
    for path in paths:
        if path in known:
            continue
        try:
            dev = input_device(path)
        except OSError as e:
            logger.debug(f"Could not open {path}: {e}")
            continue
        except UnicodeDecodeError as e:
            if path not in _undecodable:
                _undecodable.add(path)
                logger.info(f"Leaving out the device at {path}: its name isn't UTF-8 ({e})")
            continue
        if not is_keyboard_device(dev):
            close_devices([dev])
            continue
        devices.append(dev)
        added.append(dev)
    return added


def devices_with_events(devices, timeout):
    """The *devices* with events waiting, after sleeping up to *timeout* for one."""
    # The hotkey helper may already hold events it took in while answering
    # another call (active_keys), or have a reconnect due: ready now.
    due = [d for d in devices if getattr(d, "needs_service", None) and d.needs_service()]
    if due:
        return due
    try:
        ready, _, _ = select.select(devices, [], [], timeout)
    except (OSError, ValueError):
        # A device closed under us. Reading every device finds it, and the
        # reader's error handling drops it.
        return list(devices)
    return ready


def close_devices(devices):
    """Close *devices*; one unplugged meanwhile is closed already."""
    for dev in devices:
        with contextlib.suppress(OSError):
            dev.close()
