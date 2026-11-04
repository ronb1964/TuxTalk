"""System check asks the hotkey helper once per refresh. It runs on GTK's
thread, and every ask is a connection that waits for the helper's hello, so
a helper that answers slowly stalled Preferences once per ask."""
import html
import socket
import threading
from types import SimpleNamespace

import pytest

from stand_ins import ShownWidget


class Helper:
    """A stand-in helper on a socket that says *hello* to every connection
    and counts them."""

    def __init__(self, path, hello):
        self.hello, self.connections = hello, 0
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._say_hello, args=(conn,), daemon=True).start()

    def _say_hello(self, conn):
        with conn:
            conn.recv(64)
            conn.sendall(self.hello.encode() + b"\n")
            conn.recv(64)               # until the client hangs up


def _refresh(monkeypatch, tmp_path, *, hello, installed=True, uinput_ok=True):
    """System check's typing status once, with a helper saying *hello* (None:
    the socket is there and nothing answers); returns (the label's markup,
    how many connections the helper took)."""
    from tuxtalk import prefs_system_check as prefs_mod
    from tuxtalk import secure_input, uinput_helper
    path = str(tmp_path / "helper.sock")
    monkeypatch.setenv("TUXTALK_HOTKEYS_SOCKET", path)
    helper = Helper(path, hello) if hello else None
    if hello is None:
        open(path, "w").close()         # a socket file nobody answers on
    monkeypatch.setattr(secure_input, "is_installed", lambda: installed)
    monkeypatch.setattr(uinput_helper, "check_uinput_permission", lambda: (uinput_ok, "r"))
    monkeypatch.setattr(uinput_helper, "_readable_keyboards", lambda: [])
    monkeypatch.setattr(uinput_helper, "check_user_in_input_group", lambda: False)
    monkeypatch.setattr(uinput_helper, "check_udev_rule_exists", lambda: False)
    monkeypatch.setattr(secure_input, "can_install", lambda: (True, ""))
    prefs = prefs_mod.SystemCheck(SimpleNamespace(config={}))
    prefs.typing_status_label, prefs.fix_typing_button = ShownWidget(), ShownWidget()
    prefs._show_typing_status()
    return html.unescape(prefs.typing_status_label.markup), helper.connections if helper else 0


@pytest.mark.parametrize("hello, wanted", [
    ("HELLO 1 1.13 keyboards=2", "✓ Ready"),                  # working
    ("HELLO 1 1.13 keyboards=0", "can't see your keyboard"),  # reads no keyboard
    ("HELLO 1 1.11", "✓ Ready"),                              # before 1.12: no count
    ("HELLO 1 1.4", "needs a newer hotkey helper"),           # older than this TuxTalk needs
])
def test_system_check_asks_an_answering_helper_once(monkeypatch, tmp_path, hello, wanted):
    markup, connections = _refresh(monkeypatch, tmp_path, hello=hello)
    assert wanted in markup
    assert connections == 1


def test_system_check_asks_an_answering_helper_once_when_typing_is_blocked(monkeypatch, tmp_path):
    markup, connections = _refresh(monkeypatch, tmp_path, hello="HELLO 1 1.13 keyboards=1",
                                   uinput_ok=False)
    assert "TuxTalk can't type" in markup
    assert connections == 1


def test_a_silent_helper_is_still_called_silent(monkeypatch, tmp_path):
    markup, _ = _refresh(monkeypatch, tmp_path, hello=None)
    assert "isn't answering" in markup
