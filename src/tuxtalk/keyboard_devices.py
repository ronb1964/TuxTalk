"""Which input devices are keyboards, and opening and closing them: one way
for everything that reads /dev/input: the dictation service's hotkey loop
(hotkeys), the first-run hotkey test (hotkey_test_dialog), the permission
check (uinput_helper) and the keyboard grab.

Everything under /dev/input reports itself through the same evdev
interface: keyboards, but also mice, touchpads, the power button, lid
switches, audio jacks and game controllers. These checks tell the keyboards
apart by what each device reports, and TuxTalk's own virtual keyboard by its
name. The openers take evdev's list_devices and
InputDevice from the caller: the hotkey test also runs where evdev is
missing, and their tests stand in for both.
"""
import contextlib
import select

from .hotkey_helper import mask_to_keys
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

    It must report letter keys. Power buttons, lid switches, audio jacks,
    game controllers and consumer-control nodes report none, so this drops
    them immediately. TuxTalk's own virtual keyboard, which it types with,
    is never read: it is told by its name.

    One that's a mouse as well is read too, as libinput does. A Logitech
    receiver gives its keyboards a mouse's movement and buttons, and its
    mice all the letter and F-keys, so the two can't be told apart, and
    leaving them out left an MX Keys unheard (issue #26). Nor can a key
    remapper's keyboard be left out: kanata takes the real keyboards for
    itself and passes every key on through one shaped like a mouse
    (issue #23). Such a device is never grabbed (is_keyboard_and_mouse).
    """
    return _keyboard_capabilities(dev) is not None


def is_keyboard_and_mouse(dev) -> bool:
    """Whether the keyboard *dev* is a mouse too (is_keyboard_device). It
    is never grabbed or written to: held for a recording, it would freeze
    the mouse that long, and a remapper's (kanata's) would swallow the
    dictation being typed too, when the remapper has taken TuxTalk's
    virtual keyboard as well.

    The test is pointer motion (REL_X) with a mouse button, libinput's,
    not the EV_REL type: the K800 keyboard declares EV_REL with no motion
    axis, and as a mouse it would be left ungrabbed and its hotkey leak
    through."""
    caps = _keyboard_capabilities(dev)
    return caps is not None and _is_pointer(caps)


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
        if is_keyboard_and_mouse(dev):
            try:
                mask_to_keys(dev.fd)
            except OSError as e:
                logger.info(f"Reading every event of {dev.name}, its mouse movement "
                            f"included: {e}")
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
