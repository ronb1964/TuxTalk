"""Setting up the hotkey helper (secure_input.py) instead of the input group."""
import subprocess
from types import SimpleNamespace

import pytest

from shell_stubs import recorders, stub
from tuxtalk import secure_input as si
from tuxtalk import hotkey_helper


def test_the_install_script_is_valid_bash_and_installs_root_owned():
    script = si.install_script()
    assert subprocess.run(["bash", "-n"], input=script, text=True).returncode == 0
    assert f"mv -fT \"$tmp\" {si.HELPER_PATH}" in script
    assert script.startswith("set -euo pipefail\numask 022\n")
    assert 'chown root:root "$tmp"' in script
    assert "systemctl restart tuxtalk-hotkeys.service" in script
    # The helper is copied exactly, so the service runs this TuxTalk's helper.
    assert si.helper_source() in script


def test_setup_loads_uinput_before_giving_it_to_the_user(tmp_path):
    """Without the module, /dev/uinput is a bare static node the udev
    trigger can't reach: no access for the user until a reboot (Fedora
    LXQt, 2026-10-22). Run with stand-ins: a modprobe that fails mustn't
    stop the rest of the setup under set -e."""
    script = si.install_script()
    part = script[script.index("modprobe uinput"):script.index("for i in $(seq 50)")]
    calls = tmp_path / "calls"
    recorders(tmp_path, ("modprobe", "udevadm"), calls, {"modprobe": 1})
    run = subprocess.run(["/bin/bash", "-c", "set -euo pipefail\n" + part],
                         env={"PATH": str(tmp_path)}, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert calls.read_text().splitlines() == [
        "modprobe uinput",
        "udevadm control --reload-rules",
        "udevadm trigger --action=change --sysname-match=uinput",
    ]


def test_the_uninstall_lines_take_everything_away(tmp_path):
    """The helper, its service and both typing rules are what rm is given:
    each path also appears in the checks after it, so finding the names in
    the script proved nothing."""
    from tuxtalk.uinput_helper import UDEV_RULE_PATH
    calls = tmp_path / "calls.txt"
    recorders(tmp_path, ("systemctl", "rm", "rmdir", "udevadm"), calls, status=1)
    run = subprocess.run(["/bin/bash", "-c", si.uninstall_lines()],
                         env={"PATH": str(tmp_path)}, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert (f"rm -f {si.UNIT_PATH} {si.HELPER_PATH} {si.UINPUT_RULE_PATH} {UDEV_RULE_PATH}"
            in calls.read_text().splitlines())


@pytest.mark.parametrize("line", [
    "DynamicUser=yes", "SupplementaryGroups=input", "DevicePolicy=closed",
    "DeviceAllow=char-input rw", "PrivateNetwork=yes", "ProtectHome=yes",
    "ProtectSystem=strict", "NoNewPrivileges=yes", "CapabilityBoundingSet=",
    "RestrictAddressFamilies=AF_UNIX", "SystemCallFilter=@system-service",
])
def test_the_service_is_locked_down(line):
    assert line in si.UNIT.splitlines()


def test_the_service_runs_the_root_owned_copy_in_isolated_mode():
    assert f"ExecStart={si.PYTHON} -I -S {si.HELPER_PATH}" in si.UNIT
    assert si.HELPER_PATH.startswith("/usr/local/libexec/")


def test_the_uinput_rule_uses_uaccess_before_logind_applies_it():
    assert 'TAG+="uaccess"' in si.UINPUT_RULE and 'KERNEL=="uinput"' in si.UINPUT_RULE
    number = int(si.UINPUT_RULE_PATH.rsplit("/", 1)[1].split("-")[0])
    assert number < 73            # 73-seat-late.rules turns the tag into access


def test_the_script_goes_to_pkexec_on_stdin_not_through_a_file(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(si.subprocess, "run", fake_run)
    assert si._pkexec("echo hi", "tested") == (True, "")
    assert seen["cmd"] == ["pkexec", "/bin/bash", "-s"]
    assert seen["input"] == "echo hi"


def test_fix_uses_the_helper_where_it_can_and_needs_no_restart(monkeypatch):
    monkeypatch.setattr(si, "can_install", lambda: (True, ""))
    monkeypatch.setattr(si, "install_with_pkexec", lambda: (True, ""))
    assert si.fix_permissions() == (True, "", False)
    lines, restarts = si.fix_description()
    assert not restarts and "hotkey helper" in lines[0]


def test_fix_falls_back_to_the_input_group(monkeypatch):
    from tuxtalk import uinput_helper
    monkeypatch.setattr(uinput_helper, "install_udev_rule_with_pkexec", lambda: (True, "ok"))
    assert si.fix_permissions() == (True, "ok", True)
    lines, restarts = si.fix_description()
    assert restarts and "'input' group" in lines[1]


def test_the_input_group_path_says_why_and_what_it_means(monkeypatch):
    """Without the helper, setup says why, and that programs can then read
    the keyboard: calmly, in one line."""
    monkeypatch.setattr(si, "can_install", lambda: (False, "this system doesn't run systemd"))
    note = si.fallback_note()
    assert "(this system doesn't run systemd)" in note
    assert "'input' group" in note and "read the keyboard" in note


def test_the_helper_path_needs_no_note(monkeypatch):
    monkeypatch.setattr(si, "can_install", lambda: (True, ""))
    assert si.fallback_note() is None


def test_the_flatpak_needs_no_note(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    assert si.fallback_note() is None


def test_a_running_helper_counts_as_hotkeys_working(monkeypatch):
    from tuxtalk import uinput_helper, hotkey_helper_client

    class Connected:
        sock = object()             # it answered

        def close(self):
            pass
    monkeypatch.setattr(hotkey_helper_client, "connect_helper", lambda: Connected())
    monkeypatch.setattr(uinput_helper, "_readable_keyboards", lambda: [])
    ok, reason = uinput_helper.check_input_read_permission()
    assert ok and "hotkey helper" in reason


def test_first_run_fix_with_the_helper_asks_for_no_restart(monkeypatch):
    from tuxtalk import welcome_dialog as wd
    monkeypatch.setattr(si, "can_install", lambda: (True, ""))
    monkeypatch.setattr(si, "install_with_pkexec", lambda: (True, ""))
    result = wd._fix_typing_steps()
    assert result["uinput_fixed"] and result["needs_restart"] is False


def test_preferences_offers_the_helper_to_input_group_users(monkeypatch):
    from tuxtalk import prefs_system_check as P, uinput_helper

    class Label:
        markup = ""

        def set_markup(self, m):
            self.markup = m

    class Button:
        label, sensitive = "", None

        def set_label(self, v):
            self.label = v

        def set_sensitive(self, v):
            self.sensitive = v

    monkeypatch.setattr(uinput_helper, "check_all_device_permissions", lambda: (True, ""))
    monkeypatch.setattr(uinput_helper, "check_user_in_input_group", lambda: False)
    monkeypatch.setattr(si, "can_install", lambda: (True, ""))
    prefs = P.SystemCheck(SimpleNamespace(config={}))
    prefs.typing_status_label, prefs.fix_typing_button = Label(), Button()
    prefs._show_typing_status()
    assert prefs.fix_typing_button.label == P.SWITCH_TO_HELPER_LABEL
    assert prefs.fix_typing_button.sensitive
    assert "'input' group" in prefs.typing_status_label.markup

    monkeypatch.setattr(si, "running_version", lambda: hotkey_helper.VERSION)
    prefs._show_typing_status()
    assert prefs.fix_typing_button.label == "✓ Ready" and not prefs.fix_typing_button.sensitive


def _typing_prefs(monkeypatch, *, running, installed=True, in_group=False):
    from tuxtalk import prefs_system_check as P, uinput_helper

    class Widget:
        markup = label = ""
        sensitive = visible = None

        def set_markup(self, m):
            self.markup = m

        def set_label(self, v):
            self.label = v

        def set_sensitive(self, v):
            self.sensitive = v

        def set_visible(self, v):
            self.visible = v

    monkeypatch.setattr(uinput_helper, "check_all_device_permissions", lambda: (True, ""))
    monkeypatch.setattr(uinput_helper, "check_user_in_input_group", lambda: in_group)
    monkeypatch.setattr(si, "running_version", lambda: running)
    monkeypatch.setattr(si, "is_installed", lambda: installed)
    prefs = P.SystemCheck(SimpleNamespace(config={}))
    prefs.typing_status_label, prefs.fix_typing_button = Widget(), Widget()
    prefs._show_typing_status()
    return P, prefs


def test_only_an_older_helper_needs_an_update():
    for running, wanted in (("1.0", True), ("1.3", True), ("garbled", True),
                            (hotkey_helper.VERSION, False), ("1.10", False), ("9.0", False)):
        assert si.is_older(running) is wanted, running


def test_a_newer_helper_than_this_tuxtalk_is_left_alone(monkeypatch):
    P, prefs = _typing_prefs(monkeypatch, running="9.0")
    assert prefs.fix_typing_button.label != P.UPDATE_HELPER_LABEL


def test_the_service_cant_take_the_machine():
    lines = si.UNIT.splitlines()
    assert {"CPUQuota=25%", "MemoryMax=64M", "TasksMax=4"} <= set(lines)


@pytest.mark.parametrize("running", ["1.0", "1.5"])
def test_an_older_helper_is_offered_an_update(monkeypatch, running):
    """1.5 too: test builds installed a 1.5 whose hide could leave Right Ctrl
    held for the desktop, and only a newer number gets it replaced."""
    P, prefs = _typing_prefs(monkeypatch, running=running)
    assert prefs.fix_typing_button.label == P.UPDATE_HELPER_LABEL and prefs.fix_typing_button.sensitive
    assert "newer hotkey helper" in prefs.typing_status_label.markup


def test_still_in_the_input_group_is_said_plainly(monkeypatch):
    P, prefs = _typing_prefs(monkeypatch, running=hotkey_helper.VERSION, in_group=True)
    assert prefs.fix_typing_button.label == P.LEAVE_GROUP_LABEL and prefs.fix_typing_button.sensitive
    assert "other programs can still read" in prefs.typing_status_label.markup


def test_leaving_the_group_uses_pkexecs_own_uid_and_drops_the_old_rule():
    script = si.leave_input_group_script()
    assert subprocess.run(["bash", "-n"], input=script, text=True).returncode == 0
    assert 'id -nu "${PKEXEC_UID:?}"' in script and 'gpasswd -d "$user" input' in script
    from tuxtalk.uinput_helper import UDEV_RULE_PATH
    assert f"rm -f {UDEV_RULE_PATH}" in script


def test_the_service_keeps_select_safe():
    assert "LimitNOFILE=512" in si.UNIT.splitlines()


def test_switching_leaves_the_input_group_in_the_same_password_prompt():
    script = si.install_script(leave_group=True)
    assert subprocess.run(["bash", "-n"], input=script, text=True).returncode == 0
    assert 'id -nu "${PKEXEC_UID:?}"' in script and 'gpasswd -d "$user" input' in script
    assert script.index("gpasswd") < script.index("systemctl restart")
    assert "gpasswd" not in si.install_script()      # a new user is never in it


@pytest.mark.parametrize("shortcut, refused", [
    ("Ctrl+Alt+P", False), ("Ctrl+Super+V", False), ("Ctrl+Shift+H", True),
    ("Alt+P", True), ("Super+V", True), ("Shift+F8", False), ("", False),
])
def test_which_shortcuts_the_helper_refuses(shortcut, refused):
    from tuxtalk.hotkey_helper_client import refused_shortcut
    assert refused_shortcut(shortcut) is refused


def test_a_hand_edited_shortcut_that_cant_work_is_warned_about(monkeypatch):
    from tuxtalk import prefs_system_check as P, uinput_helper

    class Widget:
        markup = label = ""
        sensitive = None

        def set_markup(self, m):
            self.markup = m

        def set_label(self, v):
            self.label = v

        def set_sensitive(self, v):
            self.sensitive = v

    monkeypatch.setattr(uinput_helper, "check_all_device_permissions", lambda: (True, ""))
    monkeypatch.setattr(uinput_helper, "check_user_in_input_group", lambda: False)
    monkeypatch.setattr(si, "running_version", lambda: hotkey_helper.VERSION)
    prefs = P.SystemCheck(SimpleNamespace(config={}))
    prefs.typing_status_label, prefs.fix_typing_button = Widget(), Widget()
    prefs.win.config = {"voice_commands_hotkey": "Ctrl+Shift+H", "paste_last_hotkey": "Ctrl+Alt+P"}
    prefs._show_typing_status()
    assert "Ctrl+Shift+H won't work" in prefs.typing_status_label.markup
    assert "Ctrl+Alt+P" not in prefs.typing_status_label.markup


def test_still_being_in_the_group_is_reported_not_hidden(monkeypatch):
    """gpasswd can fail (the group in /usr/lib/group on an immutable system):
    the script says so, and Preferences then doesn't claim the user left."""
    for script in (si.install_script(leave_group=True), si.leave_input_group_script()):
        assert f"echo {si.STILL_IN_GROUP}" in script
        assert subprocess.run(["bash", "-n"], input=script, text=True).returncode == 0

    class Done:
        returncode, stdout, stderr = 0, si.STILL_IN_GROUP + "\n", ""

    monkeypatch.setattr(si.subprocess, "run", lambda *a, **k: Done())
    assert si._pkexec("true", "tested") == (True, si.STILL_IN_GROUP)


@pytest.mark.parametrize("groups, still_in", [
    ("ron wheel input", True),
    ("input ron", True),
    ("ron input-foo wheel", False),
    ("ron uinput wheel", False),
    ("ron wheel", False),
])
def test_the_still_in_the_group_check_reads_whole_group_names(tmp_path, groups, still_in):
    """Against the real `id` neither this computer nor CI is in "input", so
    a broken pattern would have passed: here `id` answers for the user."""
    stub(tmp_path, "id", f'echo "{groups}"')
    ran = subprocess.run(["/bin/bash", "-c", "user=ron\n" + si._still_in_group_check()],
                         env={"PATH": str(tmp_path)}, capture_output=True, text=True)
    assert (si.STILL_IN_GROUP in ran.stdout) == still_in
