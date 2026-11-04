"""A service that cannot read the keyboard must say so, loudly.

evdev's list_devices() returns only the device nodes the current user can
already open, and it returns an EMPTY LIST rather than raising when it cannot
open any. So a user missing 'input' group membership gets a service that starts
cleanly, prints its configured hotkey, reports itself healthy — and then
silently ignores every keypress forever.

The onboarding hotkey test still passes in that state (it always connects GTK
key handlers, so it exercises the focused-window path, not evdev), which makes
the dictation service look like the broken part when the real cause is device
permissions.

These tests pin the diagnosis text so the silent-failure mode cannot return.
"""

import grp
import os
import pytest

from tuxtalk import secure_input, uinput_helper
from tuxtalk.uinput_helper import input_access_advice


def _input_gid():
    try:
        return grp.getgrnam("input").gr_gid
    except KeyError:
        pytest.skip("no 'input' group on this system")


def test_says_restart_when_setup_ran_but_session_is_stale(monkeypatch):
    """The confusing middle case: /etc/group updated, session predates it.

    Telling this user to re-run usermod sends them to fix something that is
    already fixed. They need one thing: a restart.
    """
    import grp
    import pwd

    me = pwd.getpwuid(os.getuid()).pw_name
    fake = type("G", (), {"gr_gid": 999, "gr_mem": [me]})()
    monkeypatch.setattr(grp, "getgrnam", lambda name: fake)
    monkeypatch.setattr("os.getgroups", lambda: [1000])  # session lacks it

    msg = input_access_advice()

    assert "restart" in msg.lower()
    assert "usermod" not in msg, "already in the group — do not re-run setup"


def test_sends_a_user_outside_the_group_to_fix_typing_and_a_restart(monkeypatch):
    """No helper here (the input group is the fix): one place to go, and
    the restart the group change needs."""
    # Empty gr_mem AND a session without the gid: a user nothing has granted.
    fake = type("G", (), {"gr_gid": 999, "gr_mem": []})()
    monkeypatch.setattr(grp, "getgrnam", lambda name: fake)
    monkeypatch.setattr("os.getgroups", lambda: [1000])
    msg = input_access_advice()

    assert "Fix Typing Permissions" in msg
    assert "usermod" not in msg, "the fix does that, and the helper where it can"
    # A logout is NOT reliable: a lingering `systemd --user` session keeps the
    # old group list alive. The advice has to be "restart", or users follow it
    # and nothing changes.
    assert "restart" in msg.lower(), "group changes need a restart to take effect"


def test_with_the_helper_possible_it_leads_with_fix_typing_and_no_restart(monkeypatch):
    """The input group lets every program read the keyboard: where the
    helper can run, that's the advice, and it needs no restart."""
    monkeypatch.setattr(secure_input, "can_install", lambda: (True, ""))
    msg = input_access_advice()
    assert "Fix Typing Permissions" in msg and "hotkey helper" in msg
    assert "with no restart" in msg and "usermod" not in msg


def test_points_at_permissions_when_user_is_already_a_member(monkeypatch):
    """Being in the group but seeing no devices is a different problem entirely."""
    gid = _input_gid()
    monkeypatch.setattr("os.getgroups", lambda: [1000, gid])
    msg = input_access_advice()

    assert "/dev/input" in msg
    # Must NOT tell someone already in the group to add themselves again.
    assert "usermod" not in msg


def test_survives_systems_with_no_input_group(monkeypatch):
    """Never crash the service while trying to explain a failure."""
    def _boom(name):
        raise KeyError(name)

    monkeypatch.setattr("grp.getgrnam", _boom)
    msg = input_access_advice()
    assert msg, "must still return something explanatory"


SYSTEM_CHECK = "Open Preferences → Advanced → System check."


def _service_start(monkeypatch, devices, hold_key="F8", plugged_in=True):
    """What the user is told when the service starts after setup with a hold
    key chosen and *devices* to read: the notifications as (title, body).
    *plugged_in*: whether a keyboard is there, readable or not."""
    from tuxtalk import first_run_marker, helper_keyboard_access, hotkeys, notify
    from tuxtalk.config import Settings

    sent = []
    monkeypatch.setattr(notify, "_send", lambda title, body: sent.append((title, body)))
    monkeypatch.setattr(first_run_marker, "is_first_run", lambda: False)
    monkeypatch.setattr(secure_input, "can_install", lambda: (True, ""))
    monkeypatch.setattr(helper_keyboard_access, "keyboard_plugged_in", lambda: plugged_in)
    hotkeys._keys_to_watch(Settings(hotkey=hold_key), devices)
    return sent


def _helper(tmp_path, answering, keyboards):
    """The hotkey helper's stand-in keyboard, as the service has it at start."""
    from tuxtalk.hotkey_helper_client import HelperKeyboard
    helper = HelperKeyboard(str(tmp_path / "no-socket"))
    helper.sock = object() if answering else None
    helper.helper_keyboards = keyboards
    return helper


@pytest.mark.parametrize("hold_key", ["F8", "Right Ctrl"])
def test_a_service_that_cant_read_the_keyboard_tells_the_user(monkeypatch, hold_key):
    """The log and the terminal were the only places: the user pressed the
    key, nothing happened and nothing said why. Said even with notifications
    off (it goes through notify.notify, like the silent microphone), naming
    the key they chose and where Fix Typing is."""
    (title, body), = _service_start(monkeypatch, [], hold_key)
    assert title == "TuxTalk can't read your keyboard"
    assert body.startswith(f"{hold_key} won't start dictation yet.")
    assert "Preferences → Advanced → System check → Fix Typing Permissions" in body
    assert "no restart" in body


def test_no_keyboard_plugged_in_yet_isnt_a_problem(monkeypatch):
    """A Bluetooth keyboard connects after login: the service finds it a few
    seconds later, so a notice at every login would be false."""
    assert _service_start(monkeypatch, [], plugged_in=False) == []


def test_a_hotkey_helper_that_isnt_answering_tells_the_user(monkeypatch, tmp_path):
    """Installed, but stopped: F8 did nothing and nothing said why. The
    words are System check's, where Update starts it again."""
    (title, body), = _service_start(monkeypatch, [_helper(tmp_path, False, None)])
    assert title == "TuxTalk can't read your keyboard"
    assert body == f"{uinput_helper.HELPER_NOT_ANSWERING}. {SYSTEM_CHECK}"


def test_a_hotkey_helper_that_sees_no_keyboard_tells_the_user(monkeypatch, tmp_path):
    (title, body), = _service_start(monkeypatch, [_helper(tmp_path, True, 0)])
    assert title == "TuxTalk can't read your keyboard"
    assert body == f"{uinput_helper.HELPER_SEES_NO_KEYBOARD}. {SYSTEM_CHECK}"


def test_a_hotkey_helper_with_no_keyboard_plugged_in_yet_says_nothing(monkeypatch, tmp_path):
    assert _service_start(monkeypatch, [_helper(tmp_path, True, 0)], plugged_in=False) == []


@pytest.mark.parametrize("keyboards", [1, None])
def test_a_hotkey_helper_reading_a_keyboard_says_nothing(monkeypatch, tmp_path, keyboards):
    """None: a helper before 1.12 doesn't say how many it reads."""
    assert _service_start(monkeypatch, [_helper(tmp_path, True, keyboards)]) == []


def test_a_service_that_reads_a_keyboard_says_nothing(monkeypatch):
    assert _service_start(monkeypatch, [object()]) == []
