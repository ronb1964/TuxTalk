"""TuxTalk's own virtual pointer: how "mouse by voice" puts the pointer on a
spot and clicks.

It is a uinput tablet, like the one QEMU gives a virtual machine: it reports
where the pointer should be as a position inside a fixed range, and the
desktop maps that range onto the whole desktop. A mouse reports only
movement, which the desktop speeds up and bends, so a pointer moved that way
never lands exactly; a tablet does, on KDE, GNOME, COSMIC and X11.

Like the keyboard (virtual_keyboard.py) it is made the first time it is
needed, so a computer that never uses the mouse by voice gets no extra
device, and it needs only /dev/uinput access.
"""
import threading
import time

from . import modifier_hotkeys
from .logger import setup_logger
from .virtual_keyboard import GaveUp, write_only_device

logger = setup_logger(__name__)

NAME = "TuxTalk virtual pointer"

# The tablet's range on each axis; the desktop stretches it over its width
# and height, so a fraction of the screen is a fraction of this.
MAX = 32767

BUTTONS = {"left": "BTN_LEFT", "right": "BTN_RIGHT", "middle": "BTN_MIDDLE"}

# The desktop opens a new input device a moment after it appears, and a move
# sent before that is lost. Measured in the VMs (KDE, GNOME, COSMIC,
# Cinnamon): a move 20 ms after the device appeared was still lost on two of
# them, one 50 ms after always arrived; this is four times that.
SETTLE_S = 0.2

# A button held this long is a click to every app; the second click of a
# double click comes this long after the first lets go, inside every
# desktop's double-click time.
HOLD_S = 0.05
DOUBLE_CLICK_GAP_S = 0.08

_lock = threading.Lock()
_device = None
_ready_at = 0.0
_failure = None     # why the pointer couldn't be made, logged once


def _capabilities():
    from evdev import AbsInfo, ecodes as e
    axis = AbsInfo(0, 0, MAX, 0, 0, 0)
    return {e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
            e.EV_ABS: [(e.ABS_X, axis), (e.ABS_Y, axis)]}


def _make_device():
    return write_only_device(_capabilities(), NAME, "tuxtalk/pointer")


def _open():
    """The pointer, made the first time it's needed; None if it can't be."""
    global _device, _ready_at, _failure
    if _device is not None:
        return _device
    try:
        from evdev import UInputError
        _device = _make_device()
    except (OSError, UInputError, ImportError) as e:
        reason = f"{type(e).__name__}: {e}"
        if reason != _failure:
            logger.error(f"Can't make TuxTalk's virtual pointer ({reason}); "
                         "the mouse by voice can't move the pointer")
            _failure = reason
        return None
    _ready_at = time.monotonic() + SETTLE_S
    _failure = None
    logger.info("Made TuxTalk's virtual pointer")
    return _device


def move_to(fx: float, fy: float) -> bool:
    """Put the pointer at the fraction (*fx*, *fy*) of the desktop, 0 to 1
    from the left and the top, clamped to the desktop. False when the
    pointer can't be made or failed."""
    from evdev import ecodes
    with _lock:
        device = _open()
        if device is None:
            return False
        _wait_until_ready()
        try:
            device.write(ecodes.EV_ABS, ecodes.ABS_X, _position(fx))
            device.write(ecodes.EV_ABS, ecodes.ABS_Y, _position(fy))
            device.syn()
        except OSError as e:
            logger.error(f"TuxTalk's virtual pointer failed moving: {e}")
            _drop(device)
            return False
    return True


def click(button: str = "left", count: int = 1, stopped=None, place=None) -> bool:
    """Click *button* ("left", "right" or "middle") *count* times (2 is a
    double click) where the pointer is, or where *place*(), run before each
    press once any wait for a dictation key is over, puts it (the real mouse
    may have moved it meanwhile). False when the pointer can't be made or
    failed. Raises GaveUp before a press: "key_held" when a dictation key
    held down made it wait too long, "cancelled" when *stopped*(), asked
    before placing and right before each press, says Esc came."""
    from evdev import ecodes
    code = getattr(ecodes, BUTTONS[button])
    with _lock:
        if _open() is None:
            return False
        _wait_until_ready()
    for turn in range(count):
        if turn:
            time.sleep(DOUBLE_CLICK_GAP_S)
        wait_for_dictation_keys()
        _stop_if(stopped)
        if place is not None:
            place()
            _stop_if(stopped)
        if not _press_and_release(code):
            return False
    return True


def close() -> None:
    """Take the pointer away, when the service quits. It is made again if
    it's needed after."""
    with _lock:
        if _device is not None:
            _drop(_device)


def _position(fraction: float) -> int:
    return round(min(1.0, max(0.0, fraction)) * MAX)


def _wait_until_ready() -> None:
    time.sleep(max(0.0, _ready_at - time.monotonic()))


def wait_for_dictation_keys() -> None:
    """Until no dictation key like Right Ctrl that the desktop can see is
    down, and a Compose it began is ended (modifier_hotkeys.wait_while_held):
    the app would see a Ctrl-click. Raises GaveUp("key_held") when the key
    stays down too long. Without the lock, so quitting meanwhile doesn't
    wait for the key to come up."""
    before_press = modifier_hotkeys.wait_while_held(gave_up="the click wasn't made")
    while before_press is not None and (wait := before_press()):
        wait()


def _stop_if(stopped) -> None:
    if stopped is not None and stopped():
        raise GaveUp("cancelled")


def _press_and_release(code: int) -> bool:
    """One click; False when writing failed, and the device is dropped,
    which makes the kernel let go of the button. Letting go is tried even
    when the press's SYN failed."""
    from evdev import ecodes
    with _lock:
        device = _device
        if device is None:              # taken away meanwhile, by quitting
            return False
        try:
            device.write(ecodes.EV_KEY, code, 1)
            try:
                device.syn()
                time.sleep(HOLD_S)
            finally:
                device.write(ecodes.EV_KEY, code, 0)
                device.syn()
        except OSError as e:
            logger.error(f"TuxTalk's virtual pointer failed clicking: {e}")
            _drop(device)
            return False
    return True


def _drop(device) -> None:
    """Close the device so the next move makes a new one. Closing it makes
    the kernel let go of every button it held."""
    global _device
    try:
        device.close()
    except OSError as e:
        logger.info(f"Closing TuxTalk's virtual pointer failed: {e}")
    if _device is device:
        _device = None
