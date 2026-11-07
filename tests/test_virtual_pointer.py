"""TuxTalk's own virtual pointer (virtual_pointer.py), on the recording
pointer from conftest.py; a real one would click on the computer running
the tests."""
import pytest
from evdev import ecodes as e

from tuxtalk import hotkey_helper, keyboard_devices, modifier_hotkeys, virtual_pointer
from tuxtalk.virtual_keyboard import GaveUp


def test_move_writes_the_fraction_of_the_range(pointer):
    assert virtual_pointer.move_to(0.5, 0.25) is True
    assert pointer.events == [(e.ABS_X, 16384), (e.ABS_Y, 8192), ("SYN", 0)]


def test_a_fraction_outside_the_screen_is_clamped(pointer):
    virtual_pointer.move_to(1.4, -0.2)
    assert pointer.events[:2] == [(e.ABS_X, 32767), (e.ABS_Y, 0)]


def test_double_right_click_presses_and_releases_twice(pointer):
    assert virtual_pointer.click("right", 2) is True
    one_click = [(e.BTN_RIGHT, 1), ("SYN", 0), (e.BTN_RIGHT, 0), ("SYN", 0)]
    assert pointer.events == one_click * 2


def test_the_second_click_of_a_double_click_follows_quickly(pointer):
    virtual_pointer.click("left", 2)
    started = pointer.clock.now
    virtual_pointer.click("left", 2)
    assert pointer.clock.now - started < 0.3


def _held_until_waited(on_wait):
    """modifier_hotkeys.wait_while_held as it really is: a check made before
    each press, handing back the wait while a dictation key is down; the
    wait runs *on_wait* and the key comes up."""
    held = [True]

    def wait():
        on_wait()
        held[0] = False

    return lambda: (lambda: wait if held[0] else None)


def test_a_click_waits_while_the_dictation_modifier_is_held(pointer, monkeypatch):
    """Right Ctrl held as the click goes would make it a Ctrl+click."""
    seen = []
    monkeypatch.setattr(modifier_hotkeys, "wait_while_held",
                        _held_until_waited(lambda: seen.append(list(pointer.events))))
    assert virtual_pointer.click() is True
    assert seen == [[]]                 # nothing was pressed while it waited
    assert pointer.events == [(e.BTN_LEFT, 1), ("SYN", 0), (e.BTN_LEFT, 0), ("SYN", 0)]


def test_waiting_for_the_key_leaves_the_pointer_free(pointer, monkeypatch):
    """The wait can last half a minute: quitting meanwhile mustn't hang on it."""
    free = []

    def try_the_lock():
        free.append(virtual_pointer._lock.acquire(blocking=False))
        if free[-1]:
            virtual_pointer._lock.release()
    monkeypatch.setattr(modifier_hotkeys, "wait_while_held", _held_until_waited(try_the_lock))
    virtual_pointer.click()
    assert free == [True]


def test_each_click_of_a_double_click_checks_the_key(pointer, monkeypatch):
    checks = []

    def wait_while_held():
        checks.append(len(pointer.events))
        return lambda: None
    monkeypatch.setattr(modifier_hotkeys, "wait_while_held", wait_while_held)
    virtual_pointer.click("left", 2)
    assert checks == [0, 4]             # before the first press and before the second


def test_a_click_that_had_to_give_up_waiting_presses_nothing(pointer, monkeypatch):
    def give_up():
        raise GaveUp("key_held")
    monkeypatch.setattr(modifier_hotkeys, "wait_while_held", lambda: lambda: give_up)
    with pytest.raises(GaveUp):
        virtual_pointer.click()
    assert pointer.events == []


def test_esc_between_the_two_clicks_stops_the_second(pointer):
    """*stopped* is asked right before each press: Esc in the gap of a double
    click keeps its second click."""
    answers = iter([False, True])
    with pytest.raises(GaveUp) as stop:
        virtual_pointer.click("left", 2, stopped=lambda: next(answers))
    assert stop.value.reason == "cancelled"
    assert pointer.events == [(e.BTN_LEFT, 1), ("SYN", 0), (e.BTN_LEFT, 0), ("SYN", 0)]


def test_esc_while_the_new_device_settles_clicks_nothing(pointer):
    """The first click waits for the desktop to take the new device; Esc
    meanwhile stops it."""
    escaped = []
    pointer.clock.sleep = lambda seconds: escaped.append(seconds)
    with pytest.raises(GaveUp):
        virtual_pointer.click(stopped=lambda: bool(escaped))
    assert escaped and pointer.events == []


def test_the_button_is_released_even_when_writing_fails_midway(pointer):
    virtual_pointer.move_to(0.5, 0.5)
    pointer.fail_after = len(pointer.events) + 2        # the press and its SYN go through
    assert virtual_pointer.click() is False
    assert pointer.attempts[-1] == (e.BTN_LEFT, 0)      # letting go was tried
    assert pointer.closed                               # the kernel lets go of what is held
    assert virtual_pointer._device is None


def test_a_failed_pointer_is_made_again_for_the_next_move(pointer):
    virtual_pointer.move_to(0.1, 0.1)
    pointer.fail_after = 0
    assert virtual_pointer.move_to(0.2, 0.2) is False
    pointer.fail_after = None
    assert virtual_pointer.move_to(0.2, 0.2) is True


def test_a_move_waits_for_the_desktop_to_take_the_new_device(pointer):
    """A move sent before the desktop has opened the new device is lost: in
    the VMs one 20 ms after it appeared was, and one 50 ms after never."""
    virtual_pointer.move_to(0.5, 0.5)
    assert pointer.clock.now >= 0.1


def test_only_the_first_move_waits_for_the_device(pointer):
    virtual_pointer.move_to(0.5, 0.5)
    started = pointer.clock.now
    virtual_pointer.move_to(0.25, 0.25)
    assert pointer.clock.now == started


def test_no_keyboard_reader_takes_the_pointer():
    caps = virtual_pointer._capabilities()
    keys = set(caps[e.EV_KEY])

    class Device:
        name = virtual_pointer.NAME

        def capabilities(self):
            return {e.EV_KEY: list(keys), e.EV_ABS: [a for a, _ in caps[e.EV_ABS]]}

    assert keyboard_devices.is_keyboard_device(Device()) is False
    with pytest.raises(hotkey_helper.Skipped):
        hotkey_helper.check_keyboard(keys, virtual_pointer.NAME)


def test_the_device_is_made_once_and_only_when_used(pointer):
    assert virtual_pointer._device is None
    virtual_pointer.click()
    first = virtual_pointer._device
    virtual_pointer.click()
    assert first is pointer and virtual_pointer._device is first
    assert virtual_pointer.close() is None
    assert pointer.closed and virtual_pointer._device is None
