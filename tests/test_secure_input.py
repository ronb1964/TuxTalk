"""Setting up the hotkey helper (secure_input.py) instead of the input group."""
import subprocess
from types import SimpleNamespace

import pytest

from stand_ins import ShownWidget
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


LEFT_CTRL_TOLD = ("Left Ctrl going down and up while Left Ctrl+Space is a dictation key, "
                  "never what you type")


def test_every_place_that_says_what_the_helper_reports_names_left_ctrl(monkeypatch):
    """Helper 1.14 reports Left Ctrl's own press and release to TuxTalk while
    Left Ctrl+Space is a dictation key: the screens, the README and the
    service's description say so, not only Right Ctrl and Right Alt."""
    from pathlib import Path
    from tuxtalk import prefs_system_check, uinput_helper
    monkeypatch.setattr(si, "can_install", lambda: (True, ""))
    monkeypatch.setattr(uinput_helper, "check_user_in_input_group", lambda: False)
    readme = " ".join(Path(__file__).parents[1].joinpath("README.md").read_text().split())
    for text in (si.fix_description()[0][0], prefs_system_check._helper_state([])[0], readme):
        assert LEFT_CTRL_TOLD in text
    [description] = [line for line in si.UNIT.splitlines() if line.startswith("Description=")]
    assert "Left Ctrl" in description and len(description) < 120


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
    from tuxtalk import uinput_helper
    monkeypatch.setattr(uinput_helper, "_readable_keyboards", lambda: [])
    # A helper before 1.12 doesn't say how many keyboards it reads (None).
    monkeypatch.setattr(si, "helper_hello", lambda: ("1.11", None))
    ok, reason = uinput_helper.check_input_read_permission()
    assert ok and "hotkey helper" in reason
    monkeypatch.setattr(si, "helper_hello", lambda: ("1.13", 2))
    assert uinput_helper.check_input_read_permission()[0]


def test_a_helper_that_reads_no_keyboard_doesnt_count_as_hotkeys_working(monkeypatch):
    """Issue #26: it answered, so first run and the typing checks said the
    hotkeys should work while it read no keyboard at all."""
    from tuxtalk import uinput_helper
    monkeypatch.setattr(si, "helper_hello", lambda: ("1.13", 0))
    ok, reason = uinput_helper.check_input_read_permission()
    assert not ok and "can't see your keyboard" in reason


def test_first_run_fix_with_the_helper_asks_for_no_restart(monkeypatch):
    from tuxtalk import welcome_dialog as wd
    monkeypatch.setattr(si, "can_install", lambda: (True, ""))
    monkeypatch.setattr(si, "install_with_pkexec", lambda: (True, ""))
    result = wd._fix_typing_steps()
    assert result["uinput_fixed"] and result["needs_restart"] is False


def test_preferences_offers_the_helper_to_input_group_users(monkeypatch):
    from tuxtalk import prefs_system_check as P, uinput_helper

    monkeypatch.setattr(uinput_helper, "check_all_device_permissions", lambda hello: (True, ""))
    monkeypatch.setattr(uinput_helper, "check_user_in_input_group", lambda: False)
    monkeypatch.setattr(si, "can_install", lambda: (True, ""))
    prefs = P.SystemCheck(SimpleNamespace(config={}))
    prefs.typing_status_label, prefs.fix_typing_button = ShownWidget(), ShownWidget()
    prefs._show_typing_status()
    assert prefs.fix_typing_button.label == P.SWITCH_TO_HELPER_LABEL
    assert prefs.fix_typing_button.sensitive
    assert "'input' group" in prefs.typing_status_label.markup

    monkeypatch.setattr(si, "helper_hello", lambda: (hotkey_helper.VERSION, None))
    prefs._show_typing_status()
    assert prefs.fix_typing_button.label == "✓ Ready" and not prefs.fix_typing_button.sensitive


def _typing_prefs(monkeypatch, *, running, installed=True, in_group=False, read_only=(),
                  also_mice=(), watched=None):
    from tuxtalk import prefs_system_check as P, uinput_helper

    monkeypatch.setattr(uinput_helper, "check_all_device_permissions", lambda hello: (True, ""))
    monkeypatch.setattr(uinput_helper, "check_user_in_input_group", lambda: in_group)
    monkeypatch.setattr("tuxtalk.helper_keyboard_access.read_only_keyboards",
                        lambda: read_only)
    monkeypatch.setattr("tuxtalk.helper_keyboard_access.keyboards_also_mice",
                        lambda: also_mice)
    monkeypatch.setattr(si, "helper_hello", lambda: (running, watched))
    monkeypatch.setattr(si, "is_installed", lambda: installed)
    prefs = P.SystemCheck(SimpleNamespace(config={}))
    prefs.typing_status_label, prefs.fix_typing_button = ShownWidget(), ShownWidget()
    prefs._show_typing_status()
    return P, prefs


def test_only_an_older_helper_needs_an_update():
    """1.9, installed by test builds, and 1.10 are older than 1.11: the
    numbers after the dot are compared as numbers, not as text."""
    assert hotkey_helper.VERSION == "1.15" and si.NEEDED_HELPER_VERSION == "1.11"
    for running, older, needed in (("1.0", True, True), ("1.3", True, True),
                                   ("garbled", True, True), ("1.9", True, True),
                                   ("1.10", True, True), ("1.11", True, False),
                                   ("1.12", True, False), ("1.13", True, False),
                                   (hotkey_helper.VERSION, False, False), ("1.15", False, False),
                                   ("9.0", False, False), (None, False, False)):
        assert si.is_older(running) is older, running
        assert si.needs_update(running) is needed, running


def test_a_helper_only_older_than_the_one_shipped_is_offered_without_a_warning(monkeypatch):
    """With no keyboard here that only a newer helper reads, the update
    mostly adds what System check and Report a Problem can say: it's there
    for the asking, and System check doesn't open itself to call that a
    problem."""
    P, prefs = _typing_prefs(monkeypatch, running="1.11")
    assert prefs.fix_typing_button.label == P.UPDATE_HELPER_LABEL and prefs.fix_typing_button.sensitive
    assert "✓ Ready" in prefs.typing_status_label.markup
    assert f"(1.11 → {hotkey_helper.VERSION})" in prefs.typing_status_label.markup
    assert P._typing_state([])[3] is True


@pytest.mark.parametrize("running", ["1.13", "1.14"])
def test_a_helper_before_1_15_is_offered_the_update_quietly(monkeypatch, running):
    """1.14 came only with test builds, before a Left Ctrl kept down after
    a hold worked as Ctrl."""
    _P, prefs = _typing_prefs(monkeypatch, running=running)
    assert f"({running} → 1.15)" in prefs.typing_status_label.markup
    assert "✓ Ready" in prefs.typing_status_label.markup


@pytest.mark.parametrize("running, kind", [("1.11", "read_only"), ("1.12", "also_mice")])
def test_a_helper_too_old_for_a_keyboard_here_is_a_warning(monkeypatch, running, kind):
    P, prefs = _typing_prefs(monkeypatch, running=running, **{kind: ["/dev/input/event3"]})
    assert prefs.fix_typing_button.label == P.UPDATE_HELPER_LABEL
    assert "needs a newer hotkey helper" in prefs.typing_status_label.markup
    assert P._typing_state([])[3] is False


@pytest.mark.parametrize("access", [True, False])
def test_a_helper_that_sees_no_keyboard_is_said_plainly(monkeypatch, access):
    """Not "Fix Typing and restart" either: with no keyboard read, the
    access check fails too, and neither would help."""
    from tuxtalk import uinput_helper
    P, prefs = _typing_prefs(monkeypatch, running=hotkey_helper.VERSION, watched=0)
    monkeypatch.setattr(uinput_helper, "check_all_device_permissions", lambda hello: (access, ""))
    prefs._show_typing_status()
    assert "see your keyboard, so the dictation keys can" in \
        prefs.typing_status_label.markup
    assert "Report a Problem" in prefs.typing_status_label.markup
    assert prefs.fix_typing_button.visible is False and P._typing_state([])[3] is False


@pytest.mark.parametrize("watched", [None, 1])          # None: a 1.11 helper, which doesn't say
def test_a_helper_that_doesn_t_say_or_sees_a_keyboard_is_ready(monkeypatch, watched):
    _P, prefs = _typing_prefs(monkeypatch, running=hotkey_helper.VERSION, watched=watched)
    assert "✓ Ready" in prefs.typing_status_label.markup
    assert "keyboard, so" not in prefs.typing_status_label.markup


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
    # The helper reports these keys on their own, whatever is held with them.
    ("Ctrl+Scroll Lock", False), ("Ctrl+Right Ctrl", False),
])
def test_which_shortcuts_the_helper_refuses(shortcut, refused):
    from tuxtalk.hotkey_helper_client import refused_shortcut
    assert refused_shortcut(shortcut) is refused


def test_a_hand_edited_shortcut_that_cant_work_is_warned_about(monkeypatch):
    from tuxtalk import prefs_system_check as P, uinput_helper

    monkeypatch.setattr(uinput_helper, "check_all_device_permissions", lambda hello: (True, ""))
    monkeypatch.setattr(uinput_helper, "check_user_in_input_group", lambda: False)
    monkeypatch.setattr(si, "helper_hello", lambda: (hotkey_helper.VERSION, None))
    prefs = P.SystemCheck(SimpleNamespace(config={}))
    prefs.typing_status_label, prefs.fix_typing_button = ShownWidget(), ShownWidget()
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


@pytest.mark.parametrize("running, result", [
    ("1.10", (False, si.NOT_CONFIRMED)),      # cut short: the old helper still runs
    (None, (False, si.NOT_CONFIRMED)),
    (hotkey_helper.VERSION, (True, "")),
])
def test_an_install_that_ran_too_long_is_judged_by_the_helper_running(monkeypatch, running,
                                                                      result):
    """The time ran out after the password: the root script was waited for,
    but with its pipes closed it can die part way. "Hotkey helper updated"
    is said only if this TuxTalk's helper is the one running."""
    monkeypatch.setattr(si, "_run_as_root", lambda script, what: (True, si.RAN_TOO_LONG))
    monkeypatch.setattr(si, "is_installed", lambda: True)
    monkeypatch.setattr(si, "running_version", lambda: running)
    assert si.install_with_pkexec() == result


@pytest.mark.parametrize("running, read_only, also_mice, says", [
    ("1.11", ["/dev/input/event3"], [], "can only be read"),
    ("1.11", [], ["/dev/input/event5"], "both at once"),
    ("1.12", [], ["/dev/input/event5"], "both at once"),
    ("1.12", ["/dev/input/event3"], [], None),           # 1.12 reads that one
    ("1.13", [], ["/dev/input/event5"], None),
    (None, [], ["/dev/input/event5"], None)])
def test_an_update_is_asked_for_only_where_a_keyboard_here_is_left_out(
        monkeypatch, running, read_only, also_mice, says):
    """A Logitech keyboard is a mouse too to Linux, and a helper before 1.13
    left it out, so its F8 never reached TuxTalk (issue #26): there, and
    where only 1.12 can read a keyboard, the update is asked for at start."""
    monkeypatch.setattr("tuxtalk.helper_keyboard_access.read_only_keyboards",
                        lambda: read_only)
    monkeypatch.setattr("tuxtalk.helper_keyboard_access.keyboards_also_mice",
                        lambda: also_mice)
    reason = si.missed_keyboard_reason(running)
    assert (reason is None) is (says is None) and (says or "") in (reason or "")
    assert si.needs_update(running) is (says is not None)


def test_a_helper_too_old_for_any_keyboard_says_what_is_certain(monkeypatch):
    """Helper 1.4 doesn't report Right Ctrl or Right Alt at all: beside a
    Logitech mouse, the offer said only that a keyboard "may" be left out."""
    from tuxtalk import update_offers
    monkeypatch.setattr("tuxtalk.helper_keyboard_access.keyboards_also_mice",
                        lambda: ["/dev/input/event5"])
    assert si.missed_keyboard_reason("1.4") is None and si.needs_update("1.4")
    assert "too old" in si.outdated_reason("1.4")
    assert "Right Ctrl" in update_offers.helper_update_text("1.4")


@pytest.mark.parametrize("helper_possible, wording", [
    (True, "until you run Fix Typing</span>"),
    (False, "until you run Fix Typing and restart</span>"),
])
def test_system_check_asks_for_a_restart_only_where_fix_typing_needs_one(
        monkeypatch, helper_possible, wording):
    """The hotkey helper takes effect at once; only the input group needs a
    restart, so "and restart" would send helper users to reboot for nothing."""
    from tuxtalk import prefs_system_check as P, uinput_helper

    monkeypatch.setattr(uinput_helper, "check_all_device_permissions", lambda hello: (False, ""))
    monkeypatch.setattr(uinput_helper, "check_udev_rule_exists", lambda: False)
    monkeypatch.setattr(uinput_helper, "check_user_in_input_group", lambda: False)
    monkeypatch.setattr(uinput_helper, "check_input_read_permission", lambda hello: (True, ""))
    monkeypatch.setattr(si, "helper_hello", lambda: None)
    monkeypatch.setattr(si, "is_installed", lambda: False)
    monkeypatch.setattr(si, "can_install", lambda: (helper_possible, ""))
    markup, label, offered, ok = P._typing_state([])
    assert wording in markup and label == P.FIX_TYPING_LABEL and offered and not ok
