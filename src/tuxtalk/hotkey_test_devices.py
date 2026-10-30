"""The keyboards the first-run hotkey test reads (opened and closed by
keyboard_devices, as the dictation service's are): drained of old presses
and read, with the hotkey helper standing in for them when it runs. Nothing
here opens a window.
"""
from .keyboard_devices import close_devices
from .logger import setup_logger

logger = setup_logger(__name__)


def flush_pending_events(devices):
    """Drain whatever is already queued on each device, then return.

    evdev's InputDevice.read() is a generator function: calling it reads
    nothing and never raises BlockingIOError, so a bare `while True:
    dev.read()` would spin forever. Iterating it performs the read and lets the
    "queue empty" BlockingIOError arrive.
    """
    for dev in devices:
        try:
            while True:
                drained = False
                for _ in dev.read():
                    drained = True
                if not drained:
                    break  # read() yielded nothing and did not raise
        except BlockingIOError:
            pass  # Queue is now empty
        except Exception as e:
            logger.info(f"Could not flush events from {getattr(dev, 'name', dev)}: {e}")


def hotkey_test_helper(ecodes, connect=None):
    """The hotkey helper as a keyboard for the hotkey test, or None when it
    isn't running. It reports only the keys it's asked to watch, so ask for
    every key the test accepts on its own: F1 to F24, Esc, Pause, Scroll Lock,
    Right Ctrl and Right Alt (helper 1.5 and newer)."""
    try:
        if connect is None:
            from .hotkey_helper_client import connect_helper as connect
        helper = connect()
        if helper is None:
            return None
        codes = [getattr(ecodes, f"KEY_F{n}") for n in range(1, 25)]
        codes += [ecodes.KEY_ESC, ecodes.KEY_PAUSE, ecodes.KEY_SCROLLLOCK,
                  ecodes.KEY_RIGHTCTRL, ecodes.KEY_RIGHTALT]
        helper.watch([str(code) for code in codes])
        logger.info("Hotkey test: reading the keys through the hotkey helper")
        return helper
    except Exception as e:
        logger.warning(f"Hotkey test: could not use the hotkey helper: {e}")
        return None


def read_ready(devices, ready, on_gone=None):
    """The events waiting on each device in *ready* (from select()). One that
    has gone (unplugged mid-test) is closed and taken out of *devices*:
    select() would report it readable forever and keep a core busy. Then
    *on_gone* runs: the keys it held down will never come up."""
    for dev in ready:
        try:
            yield from dev.read()
        except BlockingIOError:
            pass        # select() woke for nothing
        except OSError as e:
            logger.info(f"Hotkey test: stopped reading {getattr(dev, 'name', dev)}: {e}")
            devices.remove(dev)
            close_devices([dev])
            if on_gone is not None:
                on_gone()
