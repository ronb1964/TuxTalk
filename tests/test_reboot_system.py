"""The onboarding "Restart Now" button reboots via systemd-logind.

`systemctl reboot` lets an active local desktop session reboot without a
password. The helper must never raise — if reboot is unavailable or refused it
returns False so the UI can fall back to asking the user to restart manually.
"""

import types


from tuxtalk import uinput_helper as uh


def test_reboot_invokes_systemctl_reboot(monkeypatch):
    calls = {}

    def run(argv, capture_output=True, text=True, timeout=10):
        calls["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(uh.subprocess, "run", run)
    assert uh.reboot_system() is True
    assert calls["argv"][:2] == ["systemctl", "reboot"]


def test_reboot_returns_false_when_refused(monkeypatch):
    def run(argv, **k):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="denied")
    monkeypatch.setattr(uh.subprocess, "run", run)
    assert uh.reboot_system() is False


def test_reboot_never_raises(monkeypatch):
    def run(argv, **k):
        raise FileNotFoundError("systemctl")
    monkeypatch.setattr(uh.subprocess, "run", run)
    assert uh.reboot_system() is False
