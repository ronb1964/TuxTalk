"""0.18 types on its own keyboard, so what an older TuxTalk set up to run
ydotoold goes: the ydotoold it started, and the systemd user unit Fix Typing
wrote. A ydotoold or unit of another program's stays.
"""
import subprocess
import types

import pytest

from tuxtalk import ydotoold_leftovers


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    return tmp_path


def test_the_one_an_older_tuxtalk_started_is_stopped(runtime):
    # A real process named ydotoold: a copy of sleep under that name.
    import shutil
    fake = runtime / "ydotoold"
    shutil.copy(shutil.which("sleep"), fake)
    proc = subprocess.Popen([str(fake), "30"])
    try:
        (runtime / "tuxtalk-ydotoold.pid").write_text(str(proc.pid))
        ydotoold_leftovers.stop_ydotoold_we_started()
        assert proc.wait(timeout=5) != 0             # it was stopped
        assert not (runtime / "tuxtalk-ydotoold.pid").exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_a_reused_number_is_not_stopped(runtime):
    """The noted number now belongs to some other program: leave it alone."""
    proc = subprocess.Popen(["sleep", "30"])
    try:
        (runtime / "tuxtalk-ydotoold.pid").write_text(str(proc.pid))
        ydotoold_leftovers.stop_ydotoold_we_started()
        assert proc.poll() is None                    # still running
    finally:
        proc.kill()


def test_nothing_noted_stops_nothing(runtime, monkeypatch):
    killed = []
    monkeypatch.setattr(ydotoold_leftovers.os, "kill", lambda *a: killed.append(a))
    ydotoold_leftovers.stop_ydotoold_we_started()                     # no file: e.g. systemd's ydotoold
    (runtime / "tuxtalk-ydotoold.pid").write_text("not a number")
    ydotoold_leftovers.stop_ydotoold_we_started()
    assert killed == []


OUR_UNIT = """[Unit]
Description=ydotool daemon (TuxTalk keystroke injection)
"""


@pytest.fixture
def home(monkeypatch, tmp_path, runtime):
    monkeypatch.setenv("HOME", str(tmp_path))
    env = types.SimpleNamespace(calls=[], returncode=0)

    def run(cmd, **k):
        env.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, env.returncode, "", "Access denied")
    monkeypatch.setattr(ydotoold_leftovers.subprocess, "run", run)
    env.unit = tmp_path / ".config" / "systemd" / "user" / "ydotoold.service"
    env.unit.parent.mkdir(parents=True)
    return env


def test_the_unit_tuxtalk_wrote_is_disabled_and_removed(home):
    home.unit.write_text(OUR_UNIT)
    ydotoold_leftovers.retire_ydotoold()
    assert not home.unit.exists()
    assert ["systemctl", "--user", "disable", "--now", "ydotoold.service"] in home.calls


def test_a_ydotoold_unit_of_someone_elses_stays(home):
    home.unit.write_text("[Unit]\nDescription=ydotool daemon\n")
    ydotoold_leftovers.retire_ydotoold()
    assert home.unit.exists() and home.calls == []


def test_a_unit_that_wouldnt_stop_is_kept_for_next_time(home):
    """Removed while systemd still runs it, ydotoold would go on until logout
    and nothing would try again."""
    home.unit.write_text(OUR_UNIT)
    home.returncode = 1
    ydotoold_leftovers.retire_ydotoold()
    assert home.unit.exists()
