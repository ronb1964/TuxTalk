"""Preferences and the dictation service start as Python modules everywhere.

No package ships usr/bin/dictate or usr/bin/dictate-prefs (the AppImage has
only dictate-tray), so the launchers don't look for them.
"""
import sys

from tuxtalk import service_launcher as sl


class _Popen:
    calls = []

    def __init__(self, argv, env=None, **_):
        _Popen.calls.append((argv, env))
        self.pid = 4242


def test_preferences_runs_the_module_and_keeps_the_callers_backend(monkeypatch):
    _Popen.calls = []
    monkeypatch.setattr(sl.subprocess, "Popen", _Popen)
    monkeypatch.setenv("GDK_BACKEND", "wayland")
    sl.launch_preferences("updates")
    (argv, env), = _Popen.calls
    assert argv == [sys.executable, "-m", "tuxtalk.prefs", "--tab=updates"]
    assert env["GDK_BACKEND"] == "wayland"     # Preferences runs natively


def test_preferences_and_the_service_inherit_the_launchers_pythonpath(monkeypatch):
    """The tray's launcher (dictate-tray, run-dev.sh) sets PYTHONPATH; the
    windows and the service it starts must find the same TuxTalk."""
    _Popen.calls = []
    monkeypatch.setattr(sl.subprocess, "Popen", _Popen)
    monkeypatch.setenv("PYTHONPATH", "/appimage/usr/src")
    sl.launch_dictation_service()
    assert _Popen.calls.pop()[1]["PYTHONPATH"] == "/appimage/usr/src"
    sl.launch_preferences()
    (argv, env), = _Popen.calls
    assert argv == [sys.executable, "-m", "tuxtalk.prefs"]
    assert env["PYTHONPATH"] == "/appimage/usr/src"


def test_the_service_always_runs_the_module(monkeypatch):
    _Popen.calls = []
    monkeypatch.setattr(sl.subprocess, "Popen", _Popen)
    sl.launch_dictation_service()
    (argv, _env), = _Popen.calls
    assert argv == [sys.executable, "-m", "tuxtalk.app"]
    assert not hasattr(sl, "_dictate_script_path")
