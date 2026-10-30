"""TuxTalk needs TWO device permissions, granted by two different mechanisms.

Writing keystrokes needs /dev/uinput. Reading hotkeys needs /dev/input/event*.
They are not interchangeable and they are not granted the same way:

    /dev/uinput        crw-rw----+  root input   <- logind sets a per-user ACL
    /dev/input/event*  crw-rw----   root input   <- group membership only

Onboarding must check both. On Fedora the logind ACL already grants uinput, so
a uinput-only check passes, setup is skipped, and the `usermod -a -G input`
inside it never runs, leaving the user able to type but unable to read a single
hotkey. The dictation service then starts perfectly and ignores every keypress.

On Ubuntu there is no uinput ACL, so a uinput-only check fails and the group
membership gets fixed as a side effect, which hides the problem there.
"""

import pytest

from tuxtalk import uinput_helper
from tuxtalk.uinput_helper import (
    check_input_read_permission,
    check_all_device_permissions,
)


def test_reports_failure_when_no_input_device_is_readable(monkeypatch):
    monkeypatch.setattr("tuxtalk.uinput_helper._readable_input_devices", lambda: [])
    ok, reason = check_input_read_permission()

    assert ok is False
    assert "input" in reason.lower()


def test_reports_success_when_a_keyboard_is_readable(monkeypatch):
    monkeypatch.setattr(
        "tuxtalk.uinput_helper._readable_keyboards",
        lambda: ["/dev/input/event3"],
    )
    ok, _ = check_input_read_permission()
    assert ok is True


class _FakeInputDevice:
    def __init__(self, keys, path=""):
        self._keys, self.path = keys, path
        self.closed = False

    def capabilities(self):
        return {1: self._keys}

    def close(self):
        self.closed = True


def test_a_readable_game_controller_alone_is_not_hotkey_access(monkeypatch):
    """logind grants every user a game controller, which reports key events;
    the service still hears no hotkey without a readable keyboard."""
    evdev = pytest.importorskip("evdev")
    gamepad_keys = [0x130, 0x131, 0x133, 0x134]          # BTN_SOUTH ... BTN_WEST
    keyboard_keys = [30, 48, 46, 32, 66]                  # KEY_A ... KEY_D, KEY_F8
    opened = []

    def device(path):
        dev = _FakeInputDevice(gamepad_keys if path.endswith("pad") else keyboard_keys, path)
        opened.append(dev)
        return dev
    monkeypatch.setattr(evdev, "InputDevice", device)
    monkeypatch.setattr("tuxtalk.uinput_helper._readable_input_devices",
                        lambda: ["/dev/input/pad"])
    ok, _ = check_input_read_permission()
    assert ok is False
    assert all(d.closed for d in opened)

    monkeypatch.setattr("tuxtalk.uinput_helper._readable_input_devices",
                        lambda: ["/dev/input/pad", "/dev/input/kbd"])
    ok, _ = check_input_read_permission()
    assert ok is True


def test_a_keyboard_unplugged_after_it_was_listed_is_skipped(monkeypatch):
    """A node gone (or refused) between the listing and the open is no
    keyboard, and the check carries on with the next one."""
    evdev = pytest.importorskip("evdev")

    def device(path):
        if path.endswith("gone"):
            raise FileNotFoundError(path)
        return _FakeInputDevice([30, 48, 46, 32, 66], path)
    monkeypatch.setattr(evdev, "InputDevice", device)
    monkeypatch.setattr("tuxtalk.uinput_helper._readable_input_devices",
                        lambda: ["/dev/input/gone", "/dev/input/kbd"])
    assert uinput_helper._readable_keyboards() == ["/dev/input/kbd"]


def test_a_keyboard_unplugged_while_the_nodes_are_listed_does_not_stop_the_check(
        monkeypatch):
    """evdev's list_devices() checks that a node exists, then stats it: a
    keyboard unplugged in between raises there. The setup screen still gets
    an answer instead of the check failing."""
    evdev = pytest.importorskip("evdev")

    def unplugged_mid_listing():
        raise FileNotFoundError("/dev/input/event7")
    monkeypatch.setattr(evdev, "list_devices", unplugged_mid_listing)
    monkeypatch.setattr("glob.glob", lambda pattern: [])     # no readable node either
    ok, reason = check_input_read_permission()
    assert ok is False and "input" in reason


def test_setup_is_required_when_only_hotkey_reading_is_broken(monkeypatch):
    """The Fedora case: uinput fine, /dev/input unreadable → MUST still fix."""
    monkeypatch.setattr(
        "tuxtalk.uinput_helper.check_uinput_permission",
        lambda: (True, "uinput is fine"),
    )
    monkeypatch.setattr("tuxtalk.uinput_helper._readable_input_devices", lambda: [])

    ok, reason = check_all_device_permissions()

    assert ok is False, "setup must run when hotkeys cannot be read"
    assert "input" in reason.lower()


def test_setup_is_required_when_only_typing_is_broken(monkeypatch):
    monkeypatch.setattr(
        "tuxtalk.uinput_helper.check_uinput_permission",
        lambda: (False, "Cannot write to /dev/uinput."),
    )
    monkeypatch.setattr(
        "tuxtalk.uinput_helper._readable_keyboards",
        lambda: ["/dev/input/event3"],
    )

    ok, reason = check_all_device_permissions()

    assert ok is False
    assert "uinput" in reason.lower()


def test_no_setup_needed_when_both_halves_work(monkeypatch):
    monkeypatch.setattr(
        "tuxtalk.uinput_helper.check_uinput_permission",
        lambda: (True, "uinput is fine"),
    )
    monkeypatch.setattr(
        "tuxtalk.uinput_helper._readable_keyboards",
        lambda: ["/dev/input/event3"],
    )

    ok, _ = check_all_device_permissions()
    assert ok is True


def test_a_hotkey_helper_that_does_not_answer_is_not_called_working(monkeypatch):
    """Installed (its socket is there) but not answering: System check said
    "Hotkeys should work" while the dictation key did nothing."""
    import types
    closed = []
    silent = types.SimpleNamespace(sock=None, close=lambda: closed.append(True))
    monkeypatch.setattr("tuxtalk.hotkey_helper_client.connect_helper", lambda: silent)
    monkeypatch.setattr("tuxtalk.uinput_helper._readable_keyboards", lambda: [])
    ok, reason = check_input_read_permission()
    assert ok is False and "answering" in reason and closed
