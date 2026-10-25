"""TuxTalk's own window is never the window a dictation is pasted into.

GNOME VM test: Preferences → Uninstall Extension, then dictate into Ptyxis.
The extension's last report was TuxTalk's Preferences window ('tuxtalk'), and
with the extension gone nothing replaced it, so every paste went out as plain
Ctrl+V (readline's quoted-insert in a terminal) and nothing appeared. Log:
"Paste: Ctrl+V (class='tuxtalk')".

Two things follow. TuxTalk's own class means "don't know", wherever it comes
from. And when the window is unknown on Wayland, the paste must still land in
a terminal: the dictation goes on the primary selection too and the paste key
is Shift+Insert, which GUI apps (GTK, Qt, Chromium, Firefox, LibreOffice)
paste the clipboard with and terminals (VTE, Konsole, kitty, foot, Alacritty)
paste the primary selection with.
"""
import pathlib
import re
import subprocess

import pytest

from tuxtalk import (app, clipboard, compositor_focus, dbus_service, desktop_identity,
                     focused_window)
from tuxtalk import recording_state as rs
from tuxtalk import live_settings as live
from tuxtalk import keyboard_output
from tuxtalk import delivery

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXT = ROOT / "gnome-extension" / "talktype@ronb1964.github.io"

CTRL_V = [(29, 1), (47, 1), (47, 0), (29, 0)]
CTRL_SHIFT_V = [(42, 1), (29, 1), (47, 1), (47, 0), (29, 0), (42, 0)]
SHIFT_INSERT = [(42, 1), (110, 1), (110, 0), (42, 0)]


# --- TuxTalk's own class is not a target ---------------------------------------------

@pytest.mark.parametrize("wm_class", [
    "tuxtalk", "Tuxtalk", "tuxtalk-dev", "io.github.ronb1964.TuxTalk",
    "talktype", "talktype-dev", "io.github.ronb1964.TalkType",
])
def test_every_name_tuxtalk_windows_go_by_is_its_own(wm_class):
    assert desktop_identity.is_own_window_class(wm_class)


@pytest.mark.parametrize("wm_class", [None, "", "org.gnome.Ptyxis", "python3", "tuxtalker"])
def test_other_windows_are_not_tuxtalks(wm_class):
    assert not desktop_identity.is_own_window_class(wm_class)


def test_a_tuxtalk_window_told_to_the_tray_is_its_own_and_no_paste_target(monkeypatch):
    """Told with its id, or by 0.18's extension without one: no paste
    shortcut is picked for it, and it's still known to be TuxTalk's."""
    monkeypatch.setattr(dbus_service, "_focus", dbus_service.FocusReport("org.gnome.Ptyxis", "4"))
    monkeypatch.setattr(focused_window, "reported",
                        lambda timeout=None: tuple(dbus_service.cached_focus()))
    service = object.__new__(dbus_service.TuxTalkDBusService)
    dbus_service.TuxTalkDBusService.SetFocusedWindow(service, "tuxtalk", "9", "")
    assert focused_window.look() == (None, ("own", ("tray", "9")))
    dbus_service.TuxTalkDBusService.SetFocusedWindow(service, "org.gnome.Ptyxis", "4", "")
    dbus_service.TuxTalkDBusService.SetFocusedWindowClass(service, "tuxtalk")   # 0.18's extension
    assert focused_window.look() == (None, ("own", None))
    assert focused_window.window_class() is None


def _reported(monkeypatch, wm_class, asked):
    """The tray says *wm_class*; the compositor, asked, says *asked*."""
    monkeypatch.setattr(focused_window, "reported", lambda timeout=None: (wm_class, "", ""))
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: (asked, None))


def test_a_reported_tuxtalk_window_gets_no_paste_class(monkeypatch):
    """An older extension still reports TuxTalk's windows."""
    _reported(monkeypatch, "tuxtalk", None)
    assert focused_window.window_class() is None


def test_a_tuxtalk_window_from_the_compositor_gets_no_paste_class(monkeypatch):
    _reported(monkeypatch, "", "Tuxtalk")
    assert focused_window.window_class() is None


def test_the_extension_clears_the_focus_when_it_is_turned_off():
    """Disabled or uninstalled, the extension stops reporting; the class it
    reported last must not outlive it."""
    js = (EXT / "extension.js").read_text()
    destroy = js[js.index("    destroy() {"):]
    destroy = destroy[:destroy.index("super.destroy();")]
    assert re.search(r"SetFocusedWindowClassRemote\(\s*''", destroy)
    assert destroy.index("SetFocusedWindowClassRemote") < destroy.index("this._proxy = null")


# --- the paste keystroke for an unknown window ----------------------------------------

class _Copy:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdin = self
        self.stderr = None
        self.written = b""

    def write(self, data):
        self.written += data

    def close(self):
        pass

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def paste_env(monkeypatch):
    """A Wayland session where wl-copy works; records what is run and pressed."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(keyboard_output, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    monkeypatch.setattr(live, "restore_clipboard", False)
    monkeypatch.setattr(clipboard, "wl_copy_command", lambda: ["wl-copy"])
    env = {"copies": [], "keys": [], "primary_rc": 0}
    monkeypatch.setattr(keyboard_output.subprocess, "Popen",
                        lambda cmd, **kw: env["copies"].append(list(cmd)) or _Copy())

    def run(cmd, **kw):
        env["copies"].append(list(cmd))
        return subprocess.CompletedProcess(cmd, env["primary_rc"])
    monkeypatch.setattr(keyboard_output.subprocess, "run", run)
    monkeypatch.setattr(keyboard_output, "send_keys", lambda keys, **kw: env["keys"].append(list(keys)) or True)
    return env


def test_an_unknown_window_gets_the_text_on_both_selections_and_shift_insert(monkeypatch, paste_env):
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    assert keyboard_output.paste_text("hello") is True
    assert ["wl-copy"] in paste_env["copies"]
    assert ["wl-copy", "--primary"] in paste_env["copies"]
    assert paste_env["keys"] == [SHIFT_INSERT]


def test_without_a_primary_selection_an_unknown_window_gets_ctrl_v(monkeypatch, paste_env):
    """Shift+Insert would paste the old primary selection into a terminal."""
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    paste_env["primary_rc"] = 1
    assert keyboard_output.paste_text("hello") is True
    assert paste_env["keys"] == [CTRL_V]


@pytest.mark.parametrize("wm_class,keys", [
    ("org.gnome.Ptyxis", CTRL_SHIFT_V),
    ("org.gnome.TextEditor", CTRL_V),
])
def test_a_known_window_keeps_its_shortcut_and_leaves_the_primary_alone(monkeypatch, paste_env,
                                                                        wm_class, keys):
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: wm_class)
    assert keyboard_output.paste_text("hello") is True
    assert not any("--primary" in c for c in paste_env["copies"])
    assert paste_env["keys"] == [keys]


# --- wl-copy's own window -------------------------------------------------------------
# Without a data-control protocol, wl-copy sets the clipboard from a small window of
# its own, which takes the focus for a moment and hands it back. A dictation into
# Konsole read the focus in that moment and pasted with Ctrl+V, readline's
# quoted-insert, so ^V appeared instead of the text. Log:
# "Paste: Ctrl+V (class='io.github.bugaevc.wl-clipboard')".

WL_COPY, WL_COPY_AT = "io.github.bugaevc.wl-clipboard", "0,0,1,1|DP-1|0,0,1920,1080"
KONSOLE_AT = "100,100,800,600|DP-1|0,0,1920,1080"
KWRITE_AT = "900,100,800,600|DP-1|0,0,1920,1080"


class _TrayCache:
    """The tray's real focus cache, filled the way the KWin script and the GNOME
    extension fill it (the window, then its moves) and asked like the service asks."""

    def __init__(self):
        self.service = object.__new__(dbus_service.TuxTalkDBusService)

    def report(self, wm_class, geometry):
        dbus_service.TuxTalkDBusService.SetFocusedWindow(self.service, wm_class, "", geometry)
        dbus_service.TuxTalkDBusService.SetFocusedWindowGeometry(self.service, geometry)

    def geometry(self):
        return dbus_service.TuxTalkDBusService.GetFocusedWindow(self.service)[-1]


class _CopyReportingItsWindow(_Copy):
    def __init__(self, tray):
        super().__init__()
        self.tray = tray

    def wait(self, timeout=None):
        self.tray.report(WL_COPY, WL_COPY_AT)
        return super().wait(timeout)


@pytest.fixture
def tray(monkeypatch):
    monkeypatch.setattr(dbus_service, "_focus", dbus_service.FocusReport())
    monkeypatch.setattr(dbus_service, "_clipboard_tool_focused", False)
    tray = _TrayCache()
    monkeypatch.setattr(focused_window, "reported", lambda timeout=None: (
        dbus_service.TuxTalkDBusService.GetFocusedWindow(tray.service)))
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: (None, None))
    return tray


def test_a_terminal_gets_the_terminal_paste_while_wl_copy_has_the_focus(monkeypatch, paste_env,
                                                                         tray):
    tray.report("org.kde.konsole", KONSOLE_AT)
    monkeypatch.setattr(keyboard_output.subprocess, "Popen", lambda cmd, **kw: _CopyReportingItsWindow(tray))
    assert keyboard_output.paste_text("git status") is True
    assert paste_env["keys"] == [CTRL_SHIFT_V]


def test_floating_messages_stay_over_the_window_while_wl_copy_has_the_focus(tray):
    """"Sending in 2 s" is placed from the geometry right after the paste."""
    tray.report("org.kde.konsole", KONSOLE_AT)
    tray.report(WL_COPY, WL_COPY_AT)
    assert tray.geometry() == KONSOLE_AT


def test_the_next_window_after_wl_copy_is_followed_again(tray):
    tray.report("org.kde.konsole", KONSOLE_AT)
    tray.report(WL_COPY, WL_COPY_AT)
    tray.report("org.kde.kwrite", KWRITE_AT)
    assert focused_window.window_class() == "org.kde.kwrite"
    assert tray.geometry() == KWRITE_AT


def test_wl_copys_window_from_the_compositor_counts_as_unknown(monkeypatch):
    """Sway, Hyprland and niri are asked directly, without the tray."""
    _reported(monkeypatch, "", WL_COPY)
    assert focused_window.window_class() is None


def test_undo_remembers_the_window_the_text_went_to_not_wl_copys(monkeypatch, tray):
    """"Delete last sentence" refuses in any window but the remembered one."""
    tray.report("org.kde.kwrite", KWRITE_AT)

    def paste(text):
        tray.report(WL_COPY, WL_COPY_AT)
        return True

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(keyboard_output, "paste_text", paste)
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    delivery.inject_text("Hello. ", "paste")
    assert rs.state.last_inserted_window == "org.kde.kwrite"
