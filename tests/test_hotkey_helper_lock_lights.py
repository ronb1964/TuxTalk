"""The hotkey helper (hotkey_helper.py) gives a keyboard back with its lock
lights as they were. Letting go of a grab makes Linux restart the text
console's keyboard code on the keyboard, which sets the console's own lock
lights there; the desktop followed, and Num Lock went off with every
dictation (issue #34).

The kernel here is a stand-in that does what Linux does at those moments,
and the keyboard writes into a pipe, so nothing reaches a real device."""
import os

import pytest

from tuxtalk import hotkey_helper as hh

NUM_LOCK, CAPS_LOCK, SCROLL_LOCK = 0, 1, 2


class ConsoleKernel:
    """A keyboard's lock lights in the kernel. Letting go of a grab sets the
    text console's own (*console*) on it, as kbd_start does in Linux."""

    def __init__(self, lit, console=frozenset()):
        self.lit, self.console = set(lit), set(console)

    def ioctl(self, fd, request, arg=0, mutate=False):
        if request == hh.EVIOCGRAB and arg == 0:
            self.lit = set(self.console)
        elif request == hh.EVIOCGLED:
            for code in self.lit:
                arg[code // 8] |= 1 << code % 8
        return 0


@pytest.fixture
def keyboard(monkeypatch):
    """(a Keyboard writing into a pipe, the pipe's other end, a function that
    puts a ConsoleKernel under it)."""
    r, w = os.pipe()
    os.set_blocking(r, False)
    kb = hh.Keyboard("/dev/input/event-fake", w, "Fake keyboard", set(), False, "own:fake")

    def under(kernel):
        monkeypatch.setattr(hh, "fcntl", kernel)
        return kernel

    yield kb, r, under
    os.close(r)
    os.close(w)


def written(r):
    """The events written to the keyboard so far, as (type, code, value)."""
    try:
        data = os.read(r, 4096)
    except BlockingIOError:
        return []
    return [hh.EVENT.unpack_from(data, at)[2:] for at in range(0, len(data), hh.EVENT.size)]


def lights_after(kernel, events):
    """The kernel's lock lights once *events* written to the keyboard arrive."""
    for event_type, code, value in events:
        if event_type == hh.EV_LED:
            (kernel.lit.add if value else kernel.lit.discard)(code)
    return kernel.lit


@pytest.mark.parametrize("lit", [
    {NUM_LOCK},
    {NUM_LOCK, CAPS_LOCK},
    {CAPS_LOCK, SCROLL_LOCK},
])
def test_lock_lights_on_before_a_dictation_are_on_after_it(keyboard, lit):
    kb, r, under = keyboard
    kernel = under(ConsoleKernel(lit))
    assert kb.grab(True)
    assert written(r) == []                 # holding it changes nothing
    assert kb.grab(False)
    assert lights_after(kernel, written(r)) == lit


def test_a_lock_light_the_console_has_on_is_put_back_off(keyboard):
    kb, r, under = keyboard
    kernel = under(ConsoleKernel(lit=set(), console={NUM_LOCK}))
    kb.grab(True)
    kb.grab(False)
    assert lights_after(kernel, written(r)) == set()


def test_nothing_is_written_when_letting_go_changed_no_light(keyboard):
    """A keyboard whose lights already match the console's (or that has
    none) gets no write at all."""
    kb, r, under = keyboard
    under(ConsoleKernel(lit={NUM_LOCK}, console={NUM_LOCK}))
    kb.grab(True)
    kb.grab(False)
    assert written(r) == []


def test_a_keyboard_that_cant_be_let_go_gets_nothing_written(keyboard):
    kb, r, under = keyboard
    kernel = under(ConsoleKernel({NUM_LOCK}))
    kb.grab(True)

    def refuse_release(fd, request, arg=0, mutate=False):
        if request == hh.EVIOCGRAB:
            raise OSError(19, "No such device")
        return ConsoleKernel.ioctl(kernel, fd, request, arg, mutate)

    under(type("Refusing", (), {"ioctl": staticmethod(refuse_release)}))
    assert not kb.grab(False)
    assert kb.grabbed and written(r) == []
