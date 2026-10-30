"""evdev's InputDevice has no set_nonblocking(), and doesn't need one.

python-evdev opens every device with O_NONBLOCK already. Calling
set_nonblocking() raises AttributeError, and inside the loop that collects
keyboards for the welcome screen's hotkey test that would skip every device
("No keyboards found via evdev"). The fake devices that test_hotkey_test_
unreadable_keyboard.py and test_input_grabs.py open through the hotkey test
and the service have no such method either, so a call to it fails there.
"""
import os

import pytest


def test_evdev_opens_devices_non_blocking():
    """The flush before the hotkey test and the service's read loop both rely
    on non-blocking reads."""
    evdev = pytest.importorskip("evdev")
    paths = evdev.list_devices()
    if not paths:
        pytest.skip("no readable input devices here")
    dev = evdev.InputDevice(paths[0])
    try:
        assert not hasattr(dev, "set_nonblocking")
        assert os.get_blocking(dev.fd) is False
    finally:
        dev.close()
