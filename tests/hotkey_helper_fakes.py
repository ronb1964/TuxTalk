"""What the hotkey helper's tests share (test_hotkey_helper.py,
test_hotkey_helper_devices.py and test_hotkey_helper_client.py): a keyboard
that is a pipe its events are written into, so no real device or privilege
is needed, and a helper to connect to."""
import os
import socket
import time

import pytest

from tuxtalk import hotkey_helper as hh


class FakeKeyboard(hh.Keyboard):
    """A keyboard made of a pipe. Which keys the user holds (*physical*) is
    kept apart from which the kernel takes to be down (*kernel*), and an
    event, the user's or one injected, comes through only if it changes the
    kernel's, as with a real evdev device: a release while the kernel already
    takes the key to be up is dropped, seen by nobody."""

    made = []                       # every one, so the fixture closes their pipes

    def __init__(self, path="/dev/input/event-fake"):
        r, w = os.pipe()
        FakeKeyboard.made.append(self)
        os.set_blocking(r, False)
        super().__init__(path, r, "Fake keyboard", set(), False, f"own:{path}")
        self.w = w
        self.physical, self.kernel = set(), set()
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
        """The user presses (1) or lets go of (0) a key, or it repeats (2)."""
        if value != 2:
            (self.physical.add if value else self.physical.discard)(code)
            if not self._changes_kernel(code, value):
                return
        self._event(code, value)

    def _changes_kernel(self, code, value):
        if (value == 1) == (code in self.kernel):
            return False
        (self.kernel.add if value == 1 else self.kernel.discard)(code)
        return True

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
        if self._changes_kernel(code, value):
            self._event(code, value)
        return True


@pytest.fixture
def setup(tmp_path):
    """A helper listening in *tmp_path* with one FakeKeyboard, the user at
    the screen being this one: (helper, keyboard, connect, say, heard, the
    seat file)."""
    seat = tmp_path / "seat0"
    seat.write_text(f"ACTIVE=c1\nACTIVE_UID={os.getuid()}\n")
    (tmp_path / "input").mkdir()
    helper = hh.Helper(socket_dir=str(tmp_path), seat_file=str(seat),
                       input_dir=str(tmp_path / "input"))
    helper.listen()
    keyboard = FakeKeyboard()
    helper.keyboards[keyboard.path] = keyboard
    clients = []

    def connect():
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(helper.socket_path)
        sock.settimeout(0)          # the helper answers before recv: no wait
        clients.append(sock)
        spin()
        return sock

    def spin(times=4):
        for _ in range(times):
            helper.run_once(timeout=0)

    def say(sock, line):
        sock.sendall(line.encode() + b"\n")
        spin()

    def heard(sock):
        spin()
        data = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except (socket.timeout, BlockingIOError):
                break
            if not chunk:
                break
            data += chunk
        return data.decode().splitlines()

    yield helper, keyboard, connect, say, heard, seat
    for sock in clients:
        sock.close()
    helper.listener.close()
    for kb in FakeKeyboard.made:
        kb.close()
        os.close(kb.w)
    FakeKeyboard.made.clear()


def ready_client(connect, say, heard, specs="66 67 1 CA:25"):
    """A client of the setup's helper that said hello and watches *specs*."""
    sock = connect()
    say(sock, "HELLO 1")
    say(sock, f"WATCH {specs}")
    hello, *rest = heard(sock)
    assert hello.startswith(f"HELLO 1 {hh.VERSION} keyboards=") and rest == ["OK WATCH"]
    return sock
