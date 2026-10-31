"""The hotkey-test dialog flushes stale key events before it starts listening.

evdev's InputDevice.read() is a *generator function*: calling it builds a
generator and runs none of its body, so it never raises BlockingIOError until
something iterates it. A flush written as a bare `while True: dev.read()`
therefore never terminates: it spins a thread at 100% CPU and never reaches
the monitoring loop below it, so the hotkey test detects nothing.
"""

from tuxtalk.hotkey_test_devices import flush_pending_events, read_ready


class Spinning(BaseException):
    """Raised past the flush's own `except Exception`: it read for ever."""


class FakeDevice:
    """read() hands back a generator that raises only once iterated, exactly
    like evdev's. Asked to read far more often than it has batches, it stops
    the test instead of letting it spin."""

    def __init__(self, queued_batches):
        self.batches = list(queued_batches)
        self.read_calls = 0

    def read(self):
        self.read_calls += 1
        if self.read_calls > 100:
            raise Spinning
        return self._events()

    def _events(self):
        if not self.batches:
            raise BlockingIOError("no events queued")
        yield from self.batches.pop(0)


def test_flush_terminates_on_an_empty_queue():
    dev = FakeDevice([])

    flush_pending_events([dev])   # must return, not spin

    assert dev.read_calls == 1


def test_flush_drains_queued_events():
    dev = FakeDevice([["stale F8 press"], ["another stale event"]])

    flush_pending_events([dev])

    assert dev.batches == [], "stale events were left in the queue"


def test_flush_continues_past_a_broken_device():
    class Broken:
        def read(self):
            raise OSError("device gone")

    good = FakeDevice([["stale"]])

    flush_pending_events([Broken(), good])

    assert good.batches == []


def test_an_unplugged_keyboard_is_dropped_rather_than_read_forever():
    """A keyboard unplugged mid-test reads as ready for ever after; kept in
    the list, the test kept a core at 100 % until it closed."""
    class Unplugged:
        name = "USB keyboard"
        closed = False

        def read(self):
            raise OSError(19, "No such device")
            yield

        def close(self):
            self.closed = True

    gone, good = Unplugged(), FakeDevice([["F8 press"]])
    devices = [gone, good]

    held = {"Left Ctrl"}

    events = list(read_ready(devices, [gone, good], on_gone=held.clear))

    assert events == ["F8 press"]
    assert devices == [good] and gone.closed
    assert held == set()             # its Ctrl will never come up
