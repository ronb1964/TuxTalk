"""The hotkey loop waits on the input devices instead of polling them.

Reading every /dev/input node 200 times a second, each idle read raising and
catching BlockingIOError, would mean thousands of wakeups a second, all day,
on an idle laptop.
"""

import os
import time

from tuxtalk import keyboard_devices


class PipeDevice:
    """Anything with a fileno() can be waited on, like an evdev InputDevice."""

    def __init__(self):
        self.r, self.w = os.pipe()

    def fileno(self):
        return self.r

    def close(self):
        for fd in (self.r, self.w):
            try:
                os.close(fd)
            except OSError:
                pass


def test_an_idle_device_list_sleeps_for_the_wait():
    dev = PipeDevice()
    try:
        start = time.monotonic()
        assert keyboard_devices.devices_with_events([dev], 0.05) == []
        assert time.monotonic() - start >= 0.04
    finally:
        dev.close()


def test_only_devices_with_events_are_returned():
    quiet, busy = PipeDevice(), PipeDevice()
    try:
        os.write(busy.w, b"x")
        assert keyboard_devices.devices_with_events([quiet, busy], 1.0) == [busy]
    finally:
        quiet.close()
        busy.close()


def test_a_closed_device_is_handed_back_to_be_dropped():
    """The reads then raise for it, and the loop drops the dead device."""
    good, gone = PipeDevice(), PipeDevice()
    gone.close()
    try:
        assert keyboard_devices.devices_with_events([good, gone], 0.01) == [good, gone]
    finally:
        good.close()


def test_no_devices_still_waits_instead_of_spinning():
    start = time.monotonic()
    assert keyboard_devices.devices_with_events([], 0.03) == []
    assert time.monotonic() - start >= 0.02
