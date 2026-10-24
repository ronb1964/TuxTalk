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

from tuxtalk.secure_input import input_access_diagnosis


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

    msg = input_access_diagnosis()

    assert "restart" in msg.lower()
    assert "usermod" not in msg, "already in the group — do not re-run setup"


def test_names_the_group_when_user_is_not_a_member(monkeypatch):
    """The actionable case: not in the group, so say which group and what to run."""
    # Empty gr_mem AND a session without the gid — a user nothing has granted.
    fake = type("G", (), {"gr_gid": 999, "gr_mem": []})()
    monkeypatch.setattr(grp, "getgrnam", lambda name: fake)
    monkeypatch.setattr("os.getgroups", lambda: [1000])
    msg = input_access_diagnosis()

    assert "input" in msg
    assert "usermod" in msg, "must give the user a command they can actually run"
    # A logout is NOT reliable — a lingering `systemd --user` session keeps the
    # old group list alive. The advice has to be "restart", or users follow it
    # and nothing changes.
    assert "restart" in msg.lower(), "group changes need a restart to take effect"


def test_points_at_permissions_when_user_is_already_a_member(monkeypatch):
    """Being in the group but seeing no devices is a different problem entirely."""
    gid = _input_gid()
    monkeypatch.setattr("os.getgroups", lambda: [1000, gid])
    msg = input_access_diagnosis()

    assert "/dev/input" in msg
    # Must NOT tell someone already in the group to add themselves again.
    assert "usermod" not in msg


def test_survives_systems_with_no_input_group(monkeypatch):
    """Never crash the service while trying to explain a failure."""
    def _boom(name):
        raise KeyError(name)

    monkeypatch.setattr("grp.getgrnam", _boom)
    msg = input_access_diagnosis()
    assert msg, "must still return something explanatory"
