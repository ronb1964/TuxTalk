"""A keyboard for the hotkey helper's tests (test_hotkey_helper.py and
test_hotkey_helper_client.py): a pipe its events are written into, so no real
device or privilege is needed."""
import os
import time

from tuxtalk import hotkey_helper as hh


class FakeKeyboard(hh.Keyboard):
    """A keyboard made of a pipe. The kernel's view of which keys are down is
    kept too: an injected event comes back only if it changes that, as with a
    real evdev device."""

    made = []                       # every one, so the fixture closes their pipes

    def __init__(self, path="/dev/input/event-fake"):
        r, w = os.pipe()
        FakeKeyboard.made.append(self)
        os.set_blocking(r, False)
        super().__init__(path, r, "Fake keyboard", set(), False, f"own:{path}")
        self.w = w
        self.kernel = set()
        self.calls = []
        # What its node's permissions and udev say now (current_group):
        # None while they say what they said when it was opened.
        self.udev_says = None

    def current_group(self):
        return self.udev_says or self.group

    def _event(self, code, value):
        # Stamped like the kernel does after EVIOCSCLOCKID(CLOCK_MONOTONIC).
        sec, usec = divmod(int(time.monotonic() * 1e6), 1_000_000)
        os.write(self.w, hh.EVENT.pack(sec, usec, hh.EV_KEY, code, value))

    def press(self, code, value=1):
        """The user presses (1) or lets go of (0) a key."""
        if value == 1:
            self.kernel.add(code)
        elif value == 0:
            self.kernel.discard(code)
        self._event(code, value)

    def kernel_down(self):
        return set(self.kernel)

    def close(self):
        """Once only: the fixture closes what the helper hasn't, and the
        number of a closed descriptor may already belong to another file."""
        if self.fd >= 0:
            super().close()
            self.fd = -1

    def grab(self, on):
        self.calls.append(("grab", on))
        self.grabbed = on
        return True

    def inject(self, code, value):
        self.calls.append(("inject", code, value))
        if (value == 1) != (code in self.kernel):
            (self.kernel.add if value == 1 else self.kernel.discard)(code)
            self._event(code, value)
        return True
