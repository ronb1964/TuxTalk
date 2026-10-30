"""Running something as root goes through one runner (pkexec_runner), so the
hotkey helper's installer, the "input" group setup and the PortAudio install
all get its protections: a fixed argv and no shell, a time limit, a cancelled
prompt told apart from a failure, and a command still running as root at the
limit waited for instead of crashing the setup."""
import subprocess
from types import SimpleNamespace

import pytest

from tuxtalk import pkexec_runner, portaudio_setup, uinput_helper


def _answer(returncode, stdout="", stderr=""):
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return run


def test_a_shell_string_is_refused_before_anything_runs(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ran"))
    with pytest.raises(TypeError):
        pkexec_runner.run("bash -c 'rm -rf /'", 10)


def test_the_command_runs_through_pkexec_with_no_shell_and_a_time_limit(monkeypatch):
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return SimpleNamespace(returncode=0, stdout="TUXTALK_OK\n", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    outcome = pkexec_runner.run(["/bin/bash", "-s"], 42, script="echo TUXTALK_OK")
    assert outcome == (pkexec_runner.DONE, "TUXTALK_OK\n", "")
    assert seen["cmd"] == ["pkexec", "/bin/bash", "-s"]
    assert seen["input"] == "echo TUXTALK_OK" and seen["timeout"] == 42
    assert "shell" not in seen


@pytest.mark.parametrize("returncode, stderr", [
    (126, "Error executing command as another user: Request dismissed\n"),
    (127, "Error executing command as another user: Not authorized\n\n"
          "This incident has been reported.\n"),
])
def test_a_cancelled_password_prompt_is_a_cancel(monkeypatch, returncode, stderr):
    monkeypatch.setattr(subprocess, "run", _answer(returncode, stderr=stderr))
    assert pkexec_runner.run(["true"], 10).status == pkexec_runner.DECLINED


def test_a_failure_keeps_what_the_command_said(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _answer(1, stderr="usermod: no such group\n"))
    assert pkexec_runner.run(["true"], 10) == (pkexec_runner.FAILED, "",
                                                "usermod: no such group")


def test_without_pkexec_it_says_so(monkeypatch):
    def run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(subprocess, "run", run)
    assert pkexec_runner.run(["true"], 10).status == pkexec_runner.MISSING


def _still_running_as_root(cmd, **kwargs):
    """What subprocess.run raises when the limit passed after the password:
    pkexec runs the command as root and can't be killed."""
    raise PermissionError(1, "Operation not permitted")


def test_the_input_group_setup_still_running_at_the_limit_says_so(monkeypatch):
    """It raised out of the setup screen instead."""
    monkeypatch.setattr(subprocess, "run", _still_running_as_root)
    monkeypatch.setattr(pkexec_runner, "SCRIPT_TIMEOUT_S", 0)
    ok, message = uinput_helper.install_udev_rule_with_pkexec()
    assert ok is False
    assert message.startswith("That took too long to tell whether it worked")


def test_pkexec_that_cannot_start_is_not_mistaken_for_a_late_answer(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _still_running_as_root)
    with pytest.raises(PermissionError):
        pkexec_runner.run(["true"], 60)


@pytest.mark.parametrize("there_after, ok", [(True, True), (False, False)])
def test_a_portaudio_install_past_the_limit_is_judged_by_what_is_there(
        monkeypatch, there_after, ok):
    checks = iter([False, there_after])
    monkeypatch.setattr(portaudio_setup, "portaudio_installed", lambda: next(checks))
    monkeypatch.setattr(portaudio_setup, "get_portaudio_install_command",
                        lambda: ("dnf", ["dnf", "install", "-y", "portaudio"]))
    monkeypatch.setattr(subprocess, "run", _still_running_as_root)
    monkeypatch.setattr(pkexec_runner, "INSTALL_TIMEOUT_S", 0)
    assert portaudio_setup.install_portaudio_with_pkexec()[0] is ok
