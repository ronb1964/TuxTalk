"""An old TalkType Flatpak running next to TuxTalk is stopped.

The Flatpak's sandbox has its own runtime folder, so it can't see TuxTalk's
single-instance lock: both would answer the hotkeys and type every dictation
twice. On start, a non-Flatpak tray asks `flatpak ps` whether the old app
runs, kills it and says so once. Any trouble asking means "not running".
"""

import subprocess

import pytest

from tuxtalk import service_launcher, startup_notices

OLD = "io.github.ronb1964.TalkType"


class FakeRun:
    """Stands in for subprocess.run; records each command."""

    def __init__(self, ps_output="", ps_error=None, kill_code=0, kill_error=None):
        self.calls = []
        self.ps_output = ps_output
        self.ps_error = ps_error
        self.kill_code = kill_code
        self.kill_error = kill_error

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        # Startup must never wait on a hung flatpak for long.
        assert 0 < kwargs.get("timeout", 0) <= 5
        if cmd[:2] == ["flatpak", "ps"]:
            if self.ps_error:
                raise self.ps_error
            return subprocess.CompletedProcess(cmd, 0, stdout=self.ps_output, stderr="")
        if cmd[:2] == ["flatpak", "kill"]:
            if self.kill_error:
                raise self.kill_error
            return subprocess.CompletedProcess(cmd, self.kill_code, stdout="", stderr="no")
        raise AssertionError(f"unexpected command {cmd}")

    def commands(self):
        return [c for c, _ in self.calls]


@pytest.fixture(autouse=True)
def not_in_flatpak(monkeypatch):
    monkeypatch.delenv("FLATPAK_ID", raising=False)


def test_a_running_old_flatpak_is_killed():
    run = FakeRun(ps_output=f"org.mozilla.firefox\n{OLD}\n")
    assert service_launcher.stop_old_talktype_flatpak(run=run) is True
    assert ["flatpak", "kill", OLD] in run.commands()


@pytest.mark.parametrize("output", [
    "",
    "org.mozilla.firefox\n",
    "Application\nio.github.ronb1964.TuxTalk\n",   # the new Flatpak is not the old one
])
def test_nothing_is_killed_when_the_old_flatpak_is_not_running(output):
    run = FakeRun(ps_output=output)
    assert service_launcher.stop_old_talktype_flatpak(run=run) is False
    assert all(c[:2] != ["flatpak", "kill"] for c in run.commands())


@pytest.mark.parametrize("error", [
    FileNotFoundError("flatpak"),                  # Flatpak isn't installed
    subprocess.TimeoutExpired("flatpak", 3),
    PermissionError("no"),
])
def test_an_error_asking_flatpak_counts_as_not_running(error):
    run = FakeRun(ps_error=error)
    assert service_launcher.stop_old_talktype_flatpak(run=run) is False
    assert all(c[:2] != ["flatpak", "kill"] for c in run.commands())


def test_a_failed_kill_is_not_reported_as_stopped():
    assert service_launcher.stop_old_talktype_flatpak(
        run=FakeRun(ps_output=f"{OLD}\n", kill_code=1)) is False
    assert service_launcher.stop_old_talktype_flatpak(
        run=FakeRun(ps_output=f"{OLD}\n",
                    kill_error=subprocess.TimeoutExpired("flatpak", 5))) is False


def test_inside_a_flatpak_nothing_is_asked(monkeypatch):
    # The sandbox has no flatpak command, and must not kill anything.
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    run = FakeRun(ps_output=f"{OLD}\n")
    assert service_launcher.stop_old_talktype_flatpak(run=run) is False
    assert run.calls == []


def test_the_tray_tells_the_user_once_when_it_stopped_one():
    shown = []
    startup_notices.handle_old_talktype_flatpak(stop=lambda: True,
                                                notify=lambda t, b: shown.append((t, b)))
    assert len(shown) == 1
    title, body = shown[0]
    assert "TalkType" in title
    assert f"flatpak uninstall {OLD}" in body


def test_the_tray_says_nothing_when_none_was_running():
    shown = []
    startup_notices.handle_old_talktype_flatpak(stop=lambda: False,
                                                notify=lambda t, b: shown.append((t, b)))
    assert shown == []


def test_the_tray_survives_an_unexpected_error():
    def boom():
        raise RuntimeError("broken")
    startup_notices.handle_old_talktype_flatpak(stop=boom, notify=lambda t, b: None)
