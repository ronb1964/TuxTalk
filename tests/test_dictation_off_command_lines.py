"""A dictation never goes on a program's command line.

Every user of the computer can read any process's command line
(/proc/<pid>/cmdline, `ps -ef`), so text handed to wtype or wl-copy there
could be read by someone else logged in. It goes in on the program's stdin,
which only TuxTalk and the program see.
"""
import subprocess

import pytest

from tuxtalk import app, clipboard, virtual_keyboard

# Starts like an option, so it also shows the text can't be read as one.
SECRET = "--my bank pin is 4321"


class Recorder:
    """Stands in for subprocess.Popen and subprocess.run: keeps each command
    line and what was sent to the program's stdin."""

    def __init__(self):
        self.argvs = []
        self.stdin = []
        self.env = None

    def popen(self, argv, **options):
        self.argvs.append(list(argv))
        self.env = options.get("env")
        recorder = self

        class Proc:
            returncode = 0

            def communicate(self, input=None, timeout=None):
                recorder.stdin.append(input)
                return (b"", b"")
        return Proc()

    def run(self, argv, input=None, **options):
        self.argvs.append(list(argv))
        self.stdin.append(input)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def text_on_a_command_line(self):
        return any(SECRET in arg for argv in self.argvs for arg in argv)


@pytest.fixture
def recorder(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(subprocess, "Popen", r.popen)
    monkeypatch.setattr(subprocess, "run", r.run)
    monkeypatch.setattr(clipboard, "_sensitive_supported", lambda: True)
    return r


def test_wtype_gets_the_dictation_on_stdin(recorder, monkeypatch):
    monkeypatch.setattr(app, "_which", lambda name: name == "wtype")
    # No access to /dev/uinput: wtype is the way left to type.
    monkeypatch.setattr(virtual_keyboard, "_open", lambda: None)

    assert app._type_text_raw(SECRET) is True
    assert recorder.argvs == [["wtype", "-"]]
    assert recorder.stdin == [SECRET.encode("utf-8")]


def test_wtype_reads_the_dictation_as_utf8_under_any_locale(recorder, monkeypatch):
    """Under a C locale wtype stopped at "é" and dropped the rest unseen."""
    from tuxtalk import keyboard_layout
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setattr(app, "_which", lambda name: name == "wtype")
    monkeypatch.setattr(virtual_keyboard, "_open", lambda: None)
    # A layout with é on it (French, say), so it isn't pasted instead.
    monkeypatch.setattr(keyboard_layout, "layout_key_events", lambda text: [(30, 1), (30, 0)])
    assert app._type_text_raw("café au lait") is True
    assert recorder.stdin == ["café au lait".encode("utf-8")]
    assert recorder.env["LC_ALL"] == "C.UTF-8"


@pytest.mark.parametrize("copy", [
    lambda text: clipboard.copy_text(text),
    lambda text: clipboard._wl_copy_sensitive(text),
    lambda text: clipboard._wl_copy_primary(text),
    lambda text: app._copy_to_primary(text),
], ids=["copy", "copy marked sensitive", "primary selection", "primary before a paste"])
def test_wl_copy_gets_the_dictation_on_stdin(recorder, monkeypatch, copy):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: f"/usr/bin/{name}")
    copy(SECRET)
    assert recorder.argvs, "wl-copy was not run"
    assert not recorder.text_on_a_command_line()
    assert recorder.stdin[-1] == SECRET.encode("utf-8")
