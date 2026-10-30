"""Which input devices are keyboards: the ones the hotkeys are read from and
the keyboard grab holds.

Everything under /dev/input reports itself through the same evdev
interface: keyboards, but also mice, touchpads, the power button, lid
switches, audio jacks and game controllers. These checks tell the keyboards
apart by what each device reports, and tell a device a program made
(through uinput) from hardware.
"""
import os

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
