"""When typing can't work, say why and what fixes it.

Typing permissions can be granted while the login session predates them:
TuxTalk's virtual keyboard then can't be made until a restart, and every word
is transcribed but none reaches the window. Saying a dictation didn't arrive is
not enough, so the notice names the cause and the fix.
"""

import types

import pytest

from tuxtalk import uinput_helper as U

@pytest.fixture
def host(monkeypatch):
    """A non-Flatpak host with /dev/uinput, configurable per test."""
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    real_exists = U.os.path.exists
    monkeypatch.setattr(U.os.path, "exists",
                        lambda p: True if p == "/dev/uinput" else real_exists(p))
    state = types.SimpleNamespace(writable=True, in_system=False, in_session=False)
    monkeypatch.setattr(U, "check_uinput_writable", lambda: state.writable)
    group = types.SimpleNamespace(gr_gid=4242, gr_mem=[])
    monkeypatch.setattr(U.grp, "getgrnam", lambda name: group)
    monkeypatch.setattr(U, "_username", lambda: "tester")
    monkeypatch.setattr(U.os, "getgroups",
                        lambda: [4242] if state.in_session else [1000])

    def apply():
        group.gr_mem = ["tester"] if state.in_system else []
    state.apply = apply
    return state


def test_granted_but_not_restarted_says_restart(host):
    host.writable, host.in_system, host.in_session = False, True, False
    host.apply()
    assert "restart your computer" in U.typing_blocked_reason().lower()


def test_never_granted_points_at_fix_typing(host):
    host.writable = False
    host.apply()
    reason = U.typing_blocked_reason()
    assert "Fix Typing Permissions" in reason and "restart" in reason.lower()


def test_nothing_wrong_returns_none(host):
    assert U.typing_blocked_reason() is None


def test_flatpak_has_nothing_to_diagnose(host, monkeypatch):
    host.writable = False
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    assert U.typing_blocked_reason() is None


def test_no_input_group_does_not_crash(host, monkeypatch):
    host.writable = False

    def missing(name):
        raise KeyError(name)
    monkeypatch.setattr(U.grp, "getgrnam", missing)
    assert "Fix Typing Permissions" in U.typing_blocked_reason()


def test_failed_notice_names_the_cause(monkeypatch):
    from tuxtalk import app
    shown = []
    monkeypatch.setattr(app, "_notify", lambda title, body: shown.append(body))
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(U, "typing_blocked_reason", lambda: U.TYPING_NO_PERMISSION)

    app._report_undelivered("failed", beeps_on=False)

    assert shown and shown[0].startswith(U.TYPING_NO_PERMISSION)
    assert "Recent Dictations" in shown[0]


def test_failed_notice_without_a_known_cause_is_unchanged(monkeypatch):
    from tuxtalk import app, delivery_notices
    monkeypatch.setattr(app, "_paste_last_hotkey", "")   # its hint is tested in test_paste_last
    shown = []
    monkeypatch.setattr(app, "_notify", lambda title, body: shown.append(body))
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(U, "typing_blocked_reason", lambda: None)

    app._report_undelivered("failed", beeps_on=False)
    assert shown == [delivery_notices.NOTICES["failed"]]


def test_the_fix_script_touches_only_uinput_and_matches_the_group_exactly():
    """`udevadm trigger` with no filter would replay events for every device,
    and a grep for 'input' in the group list would match 'input-foo' too.
    Members are still skipped, because usermod fails for users outside
    /etc/passwd."""
    script = U.get_fix_script_content()
    assert "udevadm trigger --sysname-match=uinput" in script
    assert ("id -nG \"$TUXTALK_USER\" | tr ' ' '\\n' | grep -qx input "
            '|| usermod -a -G input "$TUXTALK_USER"') in script


def test_the_fix_script_group_check_runs_in_bash(tmp_path):
    """Run the membership line for real with fake id/usermod commands."""
    import subprocess
    script = U.get_fix_script_content()
    line = next(l for l in script.splitlines() if l.startswith("id -nG"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "usermod").write_text(f"#!/bin/sh\ntouch {tmp_path}/ran\nexit 6\n")
    (bin_dir / "usermod").chmod(0o755)
    for groups, should_run in (("ron wheel input", False), ("ron input-foo", True)):
        (bin_dir / "id").write_text(f"#!/bin/sh\necho '{groups}'\n")
        (bin_dir / "id").chmod(0o755)
        (tmp_path / "ran").unlink(missing_ok=True)
        subprocess.run(["bash", "-c", f"TUXTALK_USER=ron; {line}"],
                       env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
        assert (tmp_path / "ran").exists() is should_run, groups


def test_the_root_script_goes_to_pkexec_on_stdin_never_through_a_file(monkeypatch):
    """A script file in /tmp could be rewritten by any program running as
    this user while the password prompt is open, and then run as root."""
    seen = {}

    def pkexec(cmd, **k):
        seen["cmd"], seen["input"] = cmd, k.get("input")
        raise U.subprocess.TimeoutExpired(cmd, k.get("timeout"))
    monkeypatch.setattr(U.subprocess, "run", pkexec)
    ok, _message = U.install_udev_rule_with_pkexec()
    assert ok is False
    assert seen["cmd"] == ["pkexec", "/bin/bash", "-s"]
    assert seen["input"] == U.get_fix_script_content()
