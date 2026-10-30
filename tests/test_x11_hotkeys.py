"""Other X11 desktops: the X server holds TuxTalk's hotkeys back from the
focused app (the "^[[19~" before the words in a terminal on Linux Mint).

These run against a private Xvfb, never the user's own display: a second X
client stands in for the focused app, and XTEST presses the keys.
"""
import ctypes
import ctypes.util
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import types

import pytest

from tuxtalk import xcb_ctypes
from tuxtalk import x11_hotkeys as xh

_XTST = ctypes.util.find_library("Xtst")
pytestmark = pytest.mark.skipif(not (shutil.which("Xvfb") and _XTST),
                                reason="needs Xvfb and libXtst")

# evdev codes as X keycodes (evdev + 8)
_F6, _F7, _F8, _F9 = 64 + 8, 65 + 8, 66 + 8, 67 + 8
_CTRL, _ALT, _V, _Q = 29 + 8, 56 + 8, 47 + 8, 16 + 8
_NUM_LOCK = 16
_CW_EVENT_MASK = 1 << 11
_KEY_PRESS = 2


def _cfg(hotkey="F8", toggle="F9", vc="Ctrl+Alt+V"):
    return types.SimpleNamespace(hotkey=hotkey, toggle_hotkey=toggle,
                                 voice_commands_hotkey=vc, paste_last_hotkey="")


@pytest.fixture
def xserver(monkeypatch):
    """A private X server on an X11 Cinnamon session; DISPLAY points at it."""
    read, write = os.pipe()
    server = subprocess.Popen(["Xvfb", "-displayfd", str(write), "-nolisten", "tcp"],
                              pass_fds=(write,), stderr=subprocess.DEVNULL)
    os.close(write)
    with os.fdopen(read) as f:
        display = f":{f.readline().strip()}"
    monkeypatch.setenv("DISPLAY", display)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(xh, "_x", None)
    yield display
    server.terminate()
    server.wait()
    if xh._x is not None:
        # The server is gone, so the reader thread has stopped waiting on
        # the connection and it can be freed.
        for thread in threading.enumerate():
            if thread.name == "tt-x11-hotkeys":
                thread.join(5)
        xcb_ctypes.xcb_lib().xcb_disconnect(xh._x[0])


class App:
    """Another X client, standing in for the app that has the focus: with no
    window manager, keys nobody grabbed go to the root window, and this one
    listens there."""

    def __init__(self):
        # The test's own calls, declared on the library the module loaded.
        self.lib = lib = xcb_ctypes.xcb_lib()
        P, U8, U32 = ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32
        lib.xcb_poll_for_event.restype = P
        lib.xcb_poll_for_event.argtypes = [P]
        lib.xcb_change_window_attributes_checked.restype = xcb_ctypes.Cookie
        lib.xcb_change_window_attributes_checked.argtypes = [P, U32, U32, ctypes.POINTER(U32)]
        lib.xcb_grab_keyboard.restype = xcb_ctypes.Cookie
        lib.xcb_grab_keyboard.argtypes = [P, U8, U32, U32, U8, U8]
        lib.xcb_grab_keyboard_reply.restype = ctypes.POINTER(U8)
        lib.xcb_grab_keyboard_reply.argtypes = [P, xcb_ctypes.Cookie, P]
        screen = ctypes.c_int(0)
        self.conn = self.lib.xcb_connect(None, ctypes.byref(screen))
        self.root = xcb_ctypes.x11_root(self.lib, self.conn, screen.value)
        mask = (ctypes.c_uint32 * 1)(1)          # KeyPress
        cookie = lib.xcb_change_window_attributes_checked(self.conn, self.root,
                                                          _CW_EVENT_MASK, mask)
        assert not lib.xcb_request_check(self.conn, cookie)

    def keys_received(self):
        # A round trip: the reply comes after every event sent before it.
        reply = self.lib.xcb_intern_atom_reply(
            self.conn, self.lib.xcb_intern_atom(self.conn, 1, 7, b"PRIMARY"), None)
        xcb_ctypes.free(reply)
        got = []
        while event := self.lib.xcb_poll_for_event(self.conn):
            raw = ctypes.string_at(event, 2)
            if raw[0] & 0x7f == _KEY_PRESS:
                got.append(raw[1])
            xcb_ctypes.free(event)
        return got

    def try_grab(self, keycode, mods=0):
        """True if this client could grab the key: nobody else holds it."""
        error = self.lib.xcb_request_check(self.conn, self.lib.xcb_grab_key_checked(
            self.conn, 0, self.root, mods, keycode, 1, 1))
        if error:
            xcb_ctypes.free(error)
            return False
        self.lib.xcb_ungrab_key(self.conn, keycode, self.root, mods)
        self.lib.xcb_flush(self.conn)
        return True

    def keyboard_free(self):
        """True if no client has the whole keyboard grabbed right now."""
        lib = self.lib
        reply = lib.xcb_grab_keyboard_reply(
            self.conn, lib.xcb_grab_keyboard(self.conn, 0, self.root, 0, 1, 1), None)
        status = reply[1]                    # 0: Success, 1: AlreadyGrabbed
        xcb_ctypes.free(reply)
        lib.xcb_ungrab_keyboard(self.conn, 0)
        lib.xcb_flush(self.conn)
        return status == 0

    def close(self):
        self.lib.xcb_disconnect(self.conn)


@pytest.fixture
def app(xserver):
    a = App()
    yield a
    a.close()


def _fake_keys(events):
    """Send (keycode, down) events through XTEST, on a connection of its own."""
    x11 = ctypes.CDLL(ctypes.util.find_library("X11"))
    xtst = ctypes.CDLL(_XTST)
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int,
                                       ctypes.c_ulong]
    display = x11.XOpenDisplay(None)
    for code, down in events:
        xtst.XTestFakeKeyEvent(display, code, int(down), 0)
    x11.XSync(display, 0)
    x11.XCloseDisplay(display)


def press(*keycodes):
    """Press *keycodes* in order and let them go in reverse, like a combo."""
    _fake_keys([(c, True) for c in keycodes] + [(c, False) for c in reversed(keycodes)])


def test_dictation_keys_no_longer_reach_the_app(app):
    press(_F8)
    assert app.keys_received() == [_F8]      # the leak, before the claim

    assert xh.claim(_cfg())
    press(_F8)
    press(_F9)
    press(_CTRL, _ALT, _V)
    assert app.keys_received() == [_CTRL, _ALT]
    press(_F7)
    assert app.keys_received() == [_F7]      # other keys still arrive


def test_typing_reaches_the_app_while_the_toggle_key_is_still_down(app):
    """F9 stops a dictation on the press and types it at once. If the X
    server kept the whole keyboard for TuxTalk until F9 came up, the typed
    text would go nowhere."""
    xh.claim(_cfg())
    _fake_keys([(_F9, True)])
    try:
        deadline = time.monotonic() + 2
        while not app.keyboard_free():
            assert time.monotonic() < deadline, "the keyboard stayed grabbed while F9 was down"
            time.sleep(0.01)
        press(_F7)                           # stands in for the typed text
        assert app.keys_received() == [_F7]
    finally:
        _fake_keys([(_F9, False)])


def test_held_back_with_num_lock_on_too(app):
    xh.claim(_cfg())
    assert not app.try_grab(_F8, _NUM_LOCK)


def test_a_bare_letter_is_never_taken(app):
    """It would stop the user typing that letter while dictation runs."""
    xh.claim(_cfg(hotkey="F8", toggle="Q", vc=""))
    assert app.try_grab(_Q)


def test_a_changed_hotkey_lets_go_of_the_old_one(app):
    xh.claim(_cfg())
    xh.claim(_cfg(hotkey="F6"))
    assert app.try_grab(_F8)
    assert not app.try_grab(_F6)


def test_a_crashed_service_gives_the_keys_back(app, xserver):
    """The X server drops a dead client's grabs: F8 can't stay swallowed."""
    code = ("import os, signal, types; from tuxtalk import x11_hotkeys as xh;"
            "assert xh.claim(types.SimpleNamespace(hotkey='F8', toggle_hotkey='F9',"
            " voice_commands_hotkey='', paste_last_hotkey=''));"
            "print('held', flush=True); os.kill(os.getpid(), signal.SIGKILL)")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == -signal.SIGKILL and "held" in result.stdout
    assert app.try_grab(_F8)


def test_a_key_another_program_took_is_named_in_the_log(app, caplog):
    app.lib.xcb_grab_key_checked(app.conn, 0, app.root, 0, _F8, 1, 1)
    app.lib.xcb_flush(app.conn)
    with caplog.at_level(logging.WARNING, logger=xh.logger.name):
        assert xh.claim(_cfg())              # F9 is still held
    assert "Another program already takes F8" in caplog.text
    assert "F9" not in caplog.text


@pytest.mark.parametrize("session,desktop,claims", [
    ("x11", "X-Cinnamon", True), ("x11", "XFCE", True), ("x11", "MATE", True),
    ("x11", "KDE", False),                   # the KWin script does it there
    ("x11", "ubuntu:GNOME", False),          # and the extension here
    ("wayland", "sway", False),              # XWayland only reaches X11 apps
])
def test_which_desktops(monkeypatch, session, desktop, claims):
    monkeypatch.setenv("XDG_SESSION_TYPE", session)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert xh.should_claim() is claims


def test_never_in_the_flatpak(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "XFCE")
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    assert not xh.should_claim()
