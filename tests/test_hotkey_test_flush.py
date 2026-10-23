"""The hotkey-test dialog flushes stale key events before it starts listening.

evdev's InputDevice.read() is a *generator function*: calling it builds a
generator and runs none of its body, so it never raises BlockingIOError until
something iterates it. A flush written as a bare `while True: dev.read()`
therefore never terminates: it spins a thread at 100% CPU and never reaches
the monitoring loop below it, so the hotkey test detects nothing.
"""


from tuxtalk.welcome_dialog import _flush_pending_events


class FakeDevice:
    """read() is a generator function, exactly like evdev's."""

    def __init__(self, queued_batches):
        self.batches = list(queued_batches)
        self.read_calls = 0

    def read(self):
        self.read_calls += 1
        if not self.batches:
            raise BlockingIOError("no events queued")
        for event in self.batches.pop(0):
            yield event


def test_flush_terminates_on_an_empty_queue():
    dev = FakeDevice([])

    _flush_pending_events([dev])   # must return, not spin

    assert dev.read_calls >= 1


def test_flush_drains_queued_events():
    dev = FakeDevice([["stale F8 press"], ["another stale event"]])

    _flush_pending_events([dev])

    assert dev.batches == [], "stale events were left in the queue"


def test_flush_continues_past_a_broken_device():
    class Broken:
        def read(self):
            raise OSError("device gone")

    good = FakeDevice([["stale"]])

    _flush_pending_events([Broken(), good])

    assert good.batches == []


def test_flush_returns_rather_than_spinning():
    """The flush must return rather than spin."""
    dev = FakeDevice([["one"], ["two"], ["three"]])

    _flush_pending_events([dev])

    assert dev.batches == []
