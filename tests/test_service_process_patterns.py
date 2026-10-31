"""Killing the dictation service must never kill the tray that owns it.

One helper owns the rule, because the obvious patterns hit the wrong processes:

  * a bare "talktype" matches the tray, Preferences, and any shell command that
    merely mentions the project path;
  * "-m tuxtalk" matches "-m tuxtalk.tray", the tray's own command line (and
    pkill reads a pattern starting with a dash as an option and aborts);
  * "bin/dictate" is a prefix of "bin/dictate-tray", which is the only launcher
    the shipped AppImage actually contains.

These tests pin the property that matters: it hits the service, and nothing
else, above all not the tray.

The rule compares whole arguments, not substrings. pkill/pgrep -f match a regex
against the joined command line, so a shell running
`bash -c "pgrep -f tuxtalk.app"` would pass for the service and be sent SIGUSR1
by the tray or SIGUSR2 by the Preferences hotkey test. Both signals kill a
process that doesn't handle them.
"""

import os
import pathlib
import re
import signal

import pytest

from tuxtalk import service_launcher

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Real argument lists, as they appear in /proc/<pid>/cmdline. The AppImage ones
# were taken from an extracted v0.7.2: usr/bin/dictate-tray execs
# "python3 -m tuxtalk.tray", and usr/bin/dictate does not ship at all, so the
# service runs as the module. A pip install's `dictate` console script is run
# by the kernel as "<python> /path/bin/dictate".
SERVICE_ARGVS = [
    ["/home/ron/Projects/TalkType/.venv/bin/python", "-m", "tuxtalk.app"],
    ["/tmp/.mount_TalkTyabc/usr/bin/python3", "-m", "tuxtalk.app"],
    ["python3", "-X", "faulthandler", "-m", "tuxtalk.app"],
    ["/opt/talktype/usr/bin/dictate"],
    ["/opt/talktype/usr/bin/dictate", "--verbose"],
    ["/usr/bin/python3", "/home/u/.local/bin/dictate"],
]

# Anything here being matched is a bug. The tray is the parent process: killing
# it takes down the menu, the D-Bus service and the user's only way to quit.
NOT_THE_SERVICE_ARGVS = [
    # -m tuxtalk.app as mere data: Quit would SIGTERM, then SIGKILL, these.
    ["python3", "-c", "import time; time.sleep(600)", "-m", "tuxtalk.app"],
    ["python3", "-", "-m", "tuxtalk.app"],
    ["vim", "-m", "tuxtalk.app"],
    ["/home/ron/Projects/TalkType/.venv/bin/python", "-m", "tuxtalk.tray"],
    ["/tmp/.mount_TalkTyabc/usr/bin/python3", "-m", "tuxtalk.tray"],
    ["/tmp/.mount_TalkTyabc/usr/bin/dictate-tray"],
    ["/tmp/.mount_TalkTyabc/usr/bin/python3", "-m", "tuxtalk.prefs"],
    ["/bin/bash", "-c", "cd /home/ron/Projects/TalkType && ./build-release.sh"],
    ["node", "/home/ron/.local/share/talktype-notes/index.js"],
    # The ones a substring match gets wrong:
    ["/bin/bash", "-c", "pgrep -af tuxtalk.app"],
    ["grep", "-rn", "tuxtalk.app", "src/"],
    ["less", "/tmp/tuxtalk.app.log"],
    ["vim", "/opt/talktype/usr/bin/dictate"],
    ["/bin/bash", "-c", "/opt/talktype/usr/bin/dictate"],
    [],
]


class TestTheRuleHitsTheService:
    @pytest.mark.parametrize("argv", SERVICE_ARGVS)
    def test_every_service_argv_is_matched(self, argv):
        assert service_launcher.is_service_argv(argv), (
            f"the service running as {argv!r} would not be found"
        )


class TestTheRuleSparesEverythingElse:
    @pytest.mark.parametrize("argv", NOT_THE_SERVICE_ARGVS)
    def test_not_matched(self, argv):
        assert not service_launcher.is_service_argv(argv), (
            f"{argv!r} would be taken for the service"
        )

    def test_the_tray_is_spared_specifically(self):
        """'-m tuxtalk' as a pattern would match '-m tuxtalk.tray'."""
        assert not service_launcher.is_service_argv(
            ["/usr/bin/python3", "-m", "tuxtalk.tray"]
        )

    def test_dictate_tray_is_spared(self):
        """'bin/dictate' is a prefix of the only launcher the AppImage ships."""
        assert not service_launcher.is_service_argv(
            ["/tmp/.mount_x/usr/bin/dictate-tray"]
        )


class TestFindingTheServiceInProc:
    """find_service_pids against a fake /proc, so it runs anywhere."""

    @pytest.fixture
    def fake_proc(self, monkeypatch):
        procs = {}  # pid -> (uid, argv)

        def listdir(path):
            assert path == "/proc"
            return [str(p) for p in procs] + ["self", "meminfo"]

        class _Stat:
            def __init__(self, uid):
                self.st_uid = uid

        def stat(path):
            pid = int(path.rsplit("/", 1)[1])
            if pid not in procs:
                raise FileNotFoundError(path)
            return _Stat(procs[pid][0])

        def read_argv(pid):
            return procs[pid][1] if pid in procs else None

        monkeypatch.setattr(service_launcher.os, "listdir", listdir)
        monkeypatch.setattr(service_launcher.os, "stat", stat)
        monkeypatch.setattr(service_launcher, "read_argv", read_argv)
        monkeypatch.setattr(service_launcher.os, "getuid", lambda: 1000)
        monkeypatch.setattr(service_launcher.os, "getpid", lambda: 1)
        return procs

    def test_finds_only_the_service(self, fake_proc):
        fake_proc[10] = (1000, ["python", "-m", "tuxtalk.tray"])
        fake_proc[11] = (1000, ["python", "-m", "tuxtalk.app"])
        fake_proc[12] = (1000, ["/bin/bash", "-c", "pgrep -f tuxtalk.app"])
        assert service_launcher.find_service_pids() == [11]

    def test_ignores_other_users(self, fake_proc):
        """Another user's service is not ours to signal (and os.kill would fail)."""
        fake_proc[20] = (1001, ["python", "-m", "tuxtalk.app"])
        assert service_launcher.find_service_pids() == []

    def test_skips_itself(self, fake_proc):
        fake_proc[1] = (1000, ["python", "-m", "tuxtalk.app"])
        assert service_launcher.find_service_pids() == []

    def test_a_process_that_vanished_is_skipped(self, fake_proc):
        fake_proc[30] = (1000, None)  # cmdline unreadable: exited mid-scan
        assert service_launcher.find_service_pids() == []


class TestReadArgv:
    def test_reads_this_process(self):
        argv = service_launcher.read_argv(os.getpid())
        assert argv and all(isinstance(a, str) and a for a in argv)

    def test_missing_pid_is_none(self):
        assert service_launcher.read_argv(2 ** 30) is None


class TestStopDictationService:
    def _capture(self, monkeypatch, pids):
        sent = []
        monkeypatch.setattr(service_launcher, "find_service_pids", lambda: pids)
        monkeypatch.setattr(service_launcher.os, "kill",
                            lambda pid, sig: sent.append((pid, sig)))
        return sent

    def test_it_terminates_every_service(self, monkeypatch):
        sent = self._capture(monkeypatch, [11, 12])
        service_launcher.stop_dictation_service()
        assert sent == [(11, signal.SIGTERM), (12, signal.SIGTERM)], (
            "a normal stop must not SIGKILL"
        )

    def test_force_uses_sigkill(self, monkeypatch):
        sent = self._capture(monkeypatch, [11])
        service_launcher.stop_dictation_service(force=True)
        assert sent == [(11, signal.SIGKILL)]

    def test_a_process_that_already_exited_does_not_propagate(self, monkeypatch):
        """Quit and onboarding both call this; neither may die on it."""
        monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [11])

        def gone(pid, sig):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(service_launcher.os, "kill", gone)
        service_launcher.stop_dictation_service()  # must not raise

    def test_a_failing_scan_does_not_propagate(self, monkeypatch):
        def boom():
            raise OSError("/proc unavailable")

        monkeypatch.setattr(service_launcher, "find_service_pids", boom)
        service_launcher.stop_dictation_service()  # must not raise


class TestNobodyHandRollsItAnyMore:
    def test_nobody_pkills_or_pgreps_for_the_service(self):
        """Separate patterns at each call site drift apart, and the substring
        matching pkill/pgrep -f do is wrong in its own right, whatever name
        they look for: "tuxtalk.app" matches a shell that mentions it too."""
        offenders = []
        for path in (ROOT / "src" / "tuxtalk").rglob("*.py"):
            if path.name == "service_launcher.py":
                continue
            for line in path.read_text().splitlines():
                if line.lstrip().startswith("#"):
                    continue  # history lessons in comments are fine
                if re.search(r"\bp(?:kill|grep)\b", line):
                    offenders.append(f"{path.name}: {line.strip()}")

        assert offenders == [], (
            f"{offenders} look for the service by hand; use "
            f"service_launcher.find_service_pids() / stop_dictation_service() "
            f"so the rule stays in one place"
        )
