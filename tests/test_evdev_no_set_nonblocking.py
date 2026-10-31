"""evdev's InputDevice has no set_nonblocking(), and doesn't need one.

python-evdev opens every device with O_NONBLOCK already. Calling
set_nonblocking() raises AttributeError, and inside the loop that collects
keyboards for the welcome screen's hotkey test that would skip every device
("No keyboards found via evdev"). The fake devices that test_hotkey_test_
unreadable_keyboard.py and test_input_grabs.py open through the hotkey test
and the service have no such method either, so a call to it fails there.

No real device is opened: evdev's os.open is replaced by one that records
the flags it was asked for and refuses, as an unreadable device would.
"""
import errno
import os

import pytest

evdev = pytest.importorskip("evdev")


class _OsThatOpensNothing:
    """os for evdev's device module, with an open() that records and refuses."""

    def __init__(self):
        self.flags = []

    def __getattr__(self, name):
        return getattr(os, name)

    def open(self, path, flags, *_args):
        self.flags.append(flags)
        raise OSError(errno.EACCES, "no real input devices in tests", path)


def test_evdev_opens_devices_non_blocking(monkeypatch):
    """The flush before the hotkey test and the service's read loop both rely
    on non-blocking reads."""
    fake_os = _OsThatOpensNothing()
    monkeypatch.setattr(evdev.device, "os", fake_os)
    with pytest.raises(OSError):
        evdev.InputDevice("/dev/input/event0")
    assert fake_os.flags, "evdev opened the device some other way"
    assert all(flags & os.O_NONBLOCK for flags in fake_os.flags)
    assert not hasattr(evdev.InputDevice, "set_nonblocking")
