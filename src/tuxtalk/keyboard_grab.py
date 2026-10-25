"""Exclusive grabs on the keyboards while the dictation service records.

While recording, the service holds an exclusive grab on the keyboard so the
hotkey doesn't leak through to whatever app has the focus. A grab taken and
never released leaves the keyboard dead system-wide until TuxTalk exits, so
the grabbed devices are kept here (grabbed_devices) rather than threaded
through return values: every way a recording ends (a normal stop, cancel,
the toggle key, Esc, or an exception) can release them with one call.

Keeping the desktop's key state honest across a grab: the desktop (libinput,
in KWin or GNOME Shell) sees a hotkey go down before the grab, but the grab
hides it going up. libinput counts presses minus releases per key, so the
key stays "held" for the rest of the session and each dictation adds
another. Most apps never notice; anything that asks which keys are held (VM
viewers, remote desktop, games) sees F8 stuck and auto-repeats it
(freedesktop bug 101796). So before grabbing, the release the desktop is
owed is sent through the same keyboard, and after grabbing the press is
sent back, which only TuxTalk sees, so the real release still arrives when
the key comes up. The service's own read loop hears both injected events
and skips them (is_own_injected_event), or the injected release would end
the recording it just started.

Everything here runs on the input thread.
"""
import time

from evdev import ecodes

from . import modifier_hotkeys
from . import recording_state as rs
from . import uinput_helper
from .hotkey_keys import keys_down
from .logger import setup_logger

logger = setup_logger(__name__)

# Devices we currently hold an exclusive grab on.
grabbed_devices = []

_INJECTED_TTL_S = 2.0

_injected_pending: list = []   # [(device path, code, value, expiry time)]


def inject_key(dev, code: int, value: int) -> bool:
    """Send a key event through *dev* and remember to skip it when it comes back."""
    try:
        dev.write(ecodes.EV_KEY, code, value)
        dev.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
    except Exception as e:
        logger.warning(f"Could not inject key {code}={value} on {getattr(dev, 'name', dev)}: {e}")
        return False
    expires = time.time() + _INJECTED_TTL_S
    _injected_pending.append((getattr(dev, "path", None), code, value, expires))
    return True


def is_own_injected_event(dev, event) -> bool:
    """True (once) if *event* is one we injected. Stale entries expire, so a
    lost injection can never swallow a real key event later."""
    now = time.time()
    _injected_pending[:] = [p for p in _injected_pending if p[3] > now]
    path = getattr(dev, "path", None)
    for i, (p, code, value, _) in enumerate(_injected_pending):
        if p == path and code == event.code and value == event.value:
            del _injected_pending[i]
            return True
    return False


def grab_keyboards_hiding(devices, codes) -> None:
    """Grab the keyboards without leaving the desktop believing *codes* are held.

    Only keys the kernel still reports as down are balanced: if one was already
    released (a very fast tap), the desktop saw that release and nothing is owed.
    """
    keyboards = [d for d in devices if uinput_helper.is_keyboard_device(d)]
    held, shown = [], set()     # shown: keys the desktop may still see down
    for dev in keyboards:
        try:
            down = set(dev.active_keys())
        except Exception as e:
            logger.info(f"Could not read which keys {getattr(dev, 'name', dev)} holds: {e}")
            continue
        for code in codes & down:
            if inject_key(dev, code, 0):
                held.append((dev, code))
            else:
                shown.add(code)
    grab_all_devices(keyboards)
    # Mark the keys down again. With the grab in place only we see this, and it
    # lets the kernel pass us the real release when the key comes up. On a
    # device the grab failed on, the desktop sees it too, but then it also sees
    # the real release, so it stays balanced either way.
    for dev, code in held:
        inject_key(dev, code, 1)
        if dev not in grabbed_devices:
            shown.add(code)
    modifier_hotkeys.note_hidden(set(codes) - shown, [d for d in devices if d in grabbed_devices])


def grab_all_devices(devices) -> None:
    """Take an exclusive grab on each device, tracking the ones that succeed.

    Devices that refuse the grab (permissions, already grabbed) are skipped and
    left untracked so we never try to release something we don't hold.
    """
    for dev in devices:
        try:
            dev.grab()
            grabbed_devices.append(dev)
            logger.info(f"Grabbed device: {dev.name}")
        except Exception as e:
            logger.warning(f"Could not grab {getattr(dev, 'name', dev)}: {e}")


def release_all() -> None:
    """Release every grab we hold. Idempotent, and safe to call from any path.

    One unplugged device must not strand the grabs on every other keyboard, so
    each release is attempted independently and the registry is always cleared.
    """
    global grabbed_devices
    for dev in grabbed_devices:
        try:
            dev.ungrab()
            logger.info(f"Ungrabbed device: {getattr(dev, 'name', dev)}")
        except Exception as e:
            logger.warning(f"Could not ungrab {getattr(dev, 'name', dev)}: {e}")
    grabbed_devices = []
    modifier_hotkeys.note_hidden(())


def release_if_not_recording() -> bool:
    """Safety net: holding an exclusive grab while not recording is always a bug.

    Called once per event-loop pass so that any path which fails to release —
    including one added later — self-heals within milliseconds instead of
    leaving the user with a dead keyboard. Returns True if it had to recover.
    """
    if grabbed_devices and not rs.state.is_recording:
        logger.warning(f"Releasing {len(grabbed_devices)} stranded input grab(s)")
        release_all()
        return True
    return False


def wait_for_keys_up(devices, codes, timeout) -> bool:
    """Wait until none of *codes* is down on any keyboard. False on timeout."""
    keyboards = [d for d in devices if uinput_helper.is_keyboard_device(d)]
    deadline = time.time() + timeout
    while True:
        if not keys_down(keyboards) & set(codes):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.02)
