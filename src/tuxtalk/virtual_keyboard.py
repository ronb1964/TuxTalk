"""TuxTalk's own virtual keyboard: how dictated text reaches the focused app.

The dictation service makes one keyboard through /dev/uinput when it starts
and keeps it while it runs. Every key press goes to the kernel, which hands
it to the desktop like a press on a real keyboard, and the desktop reads it
with the user's own layout (keyboard_layout.py works out which keys to press).

Until 0.18 the keys went through ydotool: a daemon, ydotoold, that TuxTalk had
to start, watch and restart ("TuxTalk's typing helper had stopped"), and a
ydotool program started for every piece of text and every shortcut. Writing
to our own device needs the same /dev/uinput access ydotoold needed, and
nothing else.

Who may open /dev/uinput is set up outside this module: the hotkey helper's
udev rule gives it to the user at the screen (secure_input.py), the older
setup gives it to the "input" group (uinput_helper.py).
"""
import threading
import time

from .logger import setup_logger

logger = setup_logger(__name__)

NAME = "TuxTalk virtual keyboard"

# Per key event. Measured in the test VMs (2026-10-20): at 1 ms GTK 3 and 4,
# Qt, Xwayland, gnome-terminal, Konsole, Firefox and Brave (Chromium, the
# engine of Electron apps) all got every character of a 200-character text
# right, also with every processor busy; with no wait the kernel's buffer
# overflowed and whole words were lost. That buffer holds about 64 key events
# while the desktop isn't reading, so the wait also sets how long a freeze of
# the desktop typing survives: 64 times it. Frozen on purpose mid-text, KWin
# lost letters after 100 ms at 1 ms and after 200 ms at 2 ms, none at 4 ms.
# ydotool's 12 ms (24 ms a letter) came from letters arriving out of order at
# 5 ms, in December 2025, with ydotoold in between. The key_delay setting
# raises it for a computer that loses letters.
KEY_DELAY_MS = 2

# The desktop opens a new keyboard a moment after it appears, and presses sent
# before that are lost: the first letters were missing at once, never after
# 20 ms on GNOME or 50 ms on KDE (VMs, 2026-10-20). The service makes the
# keyboard when it starts, so by the first dictation this has long passed.
SETTLE_S = 0.2


class StoppedPartway(Exception):
    """The keyboard failed after some of the keys had reached the app."""


class GaveUp(Exception):
    """Raised by the wait a send's *before_press* asked for, to stop the
    typing: the keys held are let go, and send passes it on, or raises
    StoppedPartway when some keys had already gone out. Its *reason* is
    the caller's, for what it tells the user."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


_lock = threading.Lock()
_device = None
_ready_at = 0.0
_failure = None     # why the keyboard couldn't be made, logged once


def _capabilities():
    """Every key a layout can put a character on, and a mouse's motion, wheel
    and three buttons. TuxTalk's hotkey readers (the service's own, and the hotkey
    helper already installed on users' computers) skip any device that is a
    pointer too, which is how they always ignored ydotoold's keyboard and
    mouse; shaped like it, ours is never grabbed or listened to for hotkeys.
    No joystick buttons: those would make it a game controller to udev."""
    from evdev import ecodes as e
    keys = [code for code in range(1, e.KEY_MAX + 1)
            if not (e.BTN_MISC <= code < e.KEY_OK or e.BTN_TRIGGER_HAPPY <= code)]
    keys += [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE]
    return {e.EV_KEY: keys, e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL]}


def _keyboard_class():
    from evdev import UInput

    class Keyboard(UInput):
        def _find_device(self, *_fd):
            """Skip opening the new device to read it back: TuxTalk only
            writes to it. With the hotkey helper installed only the helper
            may read /dev/input, and python-evdev retried for two seconds
            before giving up, on every keyboard made.

            _find_device is python-evdev's own, not part of its API. 1.9
            (poetry.lock) passes the device's fd, and Ubuntu 22.04's 1.4
            nothing, so it takes either. Check it when updating evdev, since a
            changed signature stops the keyboard from being made."""
            return None

    return Keyboard


def _make_device():
    from evdev import ecodes
    return _keyboard_class()(_capabilities(), name=NAME, bustype=ecodes.BUS_VIRTUAL,
                             phys="tuxtalk/keyboard")


def _open():
    """The keyboard, made the first time it's needed; None if it can't be."""
    global _device, _ready_at, _failure
    if _device is not None:
        return _device
    try:
        from evdev import UInputError
        _device = _make_device()
    except (OSError, UInputError, ImportError) as e:
        reason = f"{type(e).__name__}: {e}"
        if reason != _failure:
            logger.error(f"Can't make TuxTalk's virtual keyboard ({reason}); nothing can be typed")
            _failure = reason
        return None
    _ready_at = time.monotonic() + SETTLE_S
    _failure = None
    logger.info("Made TuxTalk's virtual keyboard")
    return _device


def start() -> bool:
    """Make the keyboard now, so the desktop has it before the first dictation."""
    with _lock:
        return _open() is not None


def send(events, delay_ms: float = KEY_DELAY_MS, before_press=None) -> bool:
    """Press and release keys: *events* is [(evdev key code, 1 down / 0 up)].
    *before_press*, if given, is called before each key goes down, and
    returns None or a function that waits (app._send_keys: until a Right
    Ctrl dictation key is let go) and may raise GaveUp; that runs with the
    keyboard free for others (_wait_unlocked).

    Waits *delay_ms* after each event, on a schedule kept from the start, so
    sleeping a little long doesn't add up over a long text. When this process
    was held up (a busy computer), the schedule starts again from then rather
    than catching up in a burst, which would overflow the kernel's buffer and
    lose whole words. True when every
    event was sent, False when none could be. Raises StoppedPartway when the
    keyboard failed after some had reached the app: the caller can't know
    how much of the text landed.

    Keys still down at the end, or when it fails, are let go: a Shift left
    down on any keyboard capitalises what the user types next.
    """
    events = list(events)
    if not events:
        return True
    with _lock:
        device = _open()
        if device is None:
            return False
        from evdev import ecodes
        wait = _ready_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        step = delay_ms / 1000
        due = time.monotonic()
        down = {}           # keys held, in the order they went down
        sent = 0
        try:
            for code, value in events:
                wait = before_press() if value and before_press is not None else None
                if wait is not None:
                    _wait_unlocked(device, down, wait)
                device.write(ecodes.EV_KEY, code, value)
                # Counted before the SYN: the event may reach the app even
                # if the SYN after it fails.
                sent += 1
                if value:
                    down[code] = True
                else:
                    down.pop(code, None)
                device.syn()
                due = max(due + step, time.monotonic())
                left = due - time.monotonic()
                if left > 0:
                    time.sleep(left)
        except OSError as e:
            logger.error(f"TuxTalk's virtual keyboard failed after {sent} of {len(events)} key events: {e}")
            _close_broken(device, down)
            if sent:
                raise StoppedPartway(f"{sent} of {len(events)} key events")
            return False
        except GaveUp:
            _release(device, down)
            if sent:
                raise StoppedPartway(f"{sent} of {len(events)} key events") from None
            raise
        if down:
            logger.warning(f"Letting go of keys left down: {list(down)}")
            _release(device, down)
        return True


def _wait_unlocked(device, down, wait) -> None:
    """Run *wait* with the lock let go. Taking it meanwhile, the hotkey
    loop's own keys go out at once: its Ctrl that keeps a Right Alt tap from
    opening the menu bar (modifier_hotkeys.guard_alt_tap) came after the tap otherwise.
    The keys this send holds (Shift for a capital) are let go first and
    pressed again after, so nothing sent meanwhile gets them."""
    from evdev import ecodes
    held = list(down)
    _release(device, held)
    down.clear()
    _lock.release()
    try:
        wait()
    finally:
        _lock.acquire()
    for code in held:
        device.write(ecodes.EV_KEY, code, 1)
        down[code] = True
    device.syn()


def _release(device, codes) -> None:
    """Let go of *codes* (in the order they went down), the last one first."""
    from evdev import ecodes
    for code in reversed(list(codes)):
        device.write(ecodes.EV_KEY, code, 0)
    device.syn()


def _close_broken(device, down) -> None:
    """Let go of what's held and drop the device, so the next send makes a new
    one. Closing it also makes the kernel let go of every key it held."""
    global _device
    try:
        _release(device, down)
    except OSError:
        pass
    try:
        device.close()
    except OSError:
        pass
    if _device is device:   # not one another thread made while this one waited
        _device = None

