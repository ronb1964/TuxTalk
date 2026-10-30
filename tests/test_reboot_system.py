"""The onboarding "Restart Now" button reboots via systemd-logind.

`systemctl reboot` lets an active local desktop session reboot without a
password. The helper must never raise — if reboot is unavailable or refused it
returns False so the UI can fall back to asking the user to restart manually.
"""

import subprocess
import types

import pytest

from tuxtalk import appimage_copy


def test_reboot_invokes_systemctl_reboot(monkeypatch):
    calls = {}

    def run(argv, **kwargs):
        calls["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(appimage_copy.subprocess, "run", run)
    assert appimage_copy.reboot_system() is True
    assert calls["argv"][:2] == ["systemctl", "reboot"]


def test_reboot_returns_false_when_refused(monkeypatch):
    def run(argv, **k):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="denied")
    monkeypatch.setattr(appimage_copy.subprocess, "run", run)
    assert appimage_copy.reboot_system() is False


@pytest.mark.parametrize("error", [FileNotFoundError("systemctl"),
                                   PermissionError("systemctl"),
                                   subprocess.TimeoutExpired(["systemctl", "reboot"], 10)])
def test_reboot_never_raises(monkeypatch, error):
    def run(argv, **k):
        raise error
    monkeypatch.setattr(appimage_copy.subprocess, "run", run)
    assert appimage_copy.reboot_system() is False
