"""The hotkey test is honest when the dictation service can't read the keyboard.

The service hears hotkeys only through evdev (/dev/input). Before the user is
in the input group, evdev opens no keyboard, yet the dialog's GTK fallback
still sees the keys. Those keys must show as seen but not active yet, with a
note saying what makes them work, never a plain "Working!".
"""
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("gi")
from gtk_text import widgets  # noqa: E402
from tuxtalk import uinput_helper  # noqa: E402
from tuxtalk import welcome_dialog as wd  # noqa: E402

EV_KEY = 1
KEYBOARD_KEYS = [30, 48, 46, 32, 66]        # KEY_A, KEY_B, KEY_C, KEY_D, KEY_F8
GAMEPAD_KEYS = [0x130, 0x131, 0x133, 0x134]  # BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST


class FakeDevice:
    """An evdev device whose select() never fires: a pipe nobody writes."""

    def __init__(self, path, kind="keyboard"):
        self.path, self.name = path, path
        self._caps = {"keyboard": {EV_KEY: KEYBOARD_KEYS},
                      "gamepad": {EV_KEY: GAMEPAD_KEYS, 3: [0, 1]},
                      "mouse": {2: [0]}}[kind]
        self._r, self._w = os.pipe()

    def capabilities(self):
        return self._caps

    def fileno(self):
        return self._r

    def read(self):
        raise BlockingIOError

    def close(self):
        for fd in (self._r, self._w):
            try:
                os.close(fd)
            except OSError:
                pass


def _input_device(denied=()):
    def make(path):
        if path in denied:
            raise PermissionError(path)
        return FakeDevice(path, kind=path.rpartition("/")[2] if path.endswith(
            ("gamepad", "mouse")) else "keyboard")
    return make


def test_keyboards_are_opened():
    paths = ["/dev/input/kbd", "/dev/input/mouse"]
    opened = wd._open_key_devices(lambda: paths, _input_device())
    assert [d.path for d in opened] == ["/dev/input/kbd"]


def test_a_game_controller_is_not_a_keyboard():
    """logind lets any user open a game controller, and it reports key events;
    the service still can't hear F8 through it."""
    paths = ["/dev/input/gamepad"]
    assert wd._open_key_devices(lambda: paths, _input_device()) == []


def test_no_devices_when_permission_is_denied():
    paths = ["/dev/input/kbd"]
    assert wd._open_key_devices(lambda: paths, _input_device(denied=paths)) == []


def test_no_devices_when_listing_fails():
    def boom():
        raise OSError("no /dev/input")
    assert wd._open_key_devices(boom, _input_device()) == []


def test_a_key_the_service_can_read_shows_working():
    assert "Working!" in wd._key_seen_markup(True)
    assert wd._hotkey_test_notice(True) is None


def test_a_key_only_gtk_saw_does_not_claim_working():
    markup = wd._key_seen_markup(False)
    assert "Working" not in markup
    assert "not active yet" in markup


def test_the_notice_asks_for_a_restart_when_setup_is_done(monkeypatch):
    monkeypatch.setattr(uinput_helper, "_input_group_pending_restart", lambda: True)
    notice = wd._hotkey_test_notice(False)
    assert "restart your computer" in notice
    assert "Fix Typing" not in notice


def test_the_notice_points_to_fix_typing_when_setup_is_not_done(monkeypatch):
    monkeypatch.setattr(uinput_helper, "_input_group_pending_restart", lambda: False)
    notice = wd._hotkey_test_notice(False)
    assert "Fix Typing Permissions" in notice
    assert "restart" in notice


def _run_dialog_with_f8_release(monkeypatch, evdev_paths, denied=()):
    """Run the real dialog; while it is up, release F8 through GTK and
    return the visible label texts."""
    from gi.repository import Gdk, Gtk
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    evdev = pytest.importorskip("evdev")
    from tuxtalk import config, gnome_keybindings, service_launcher

    monkeypatch.setattr(config, "load_config",
                        lambda: SimpleNamespace(hotkey="F8", toggle_hotkey="F9"))
    monkeypatch.setattr(config, "save_config", lambda c: None)
    monkeypatch.setattr(gnome_keybindings, "disable_for_hotkey_test", lambda: None)
    monkeypatch.setattr(gnome_keybindings, "restore", lambda saved: None)
    monkeypatch.setattr(service_launcher, "stop_dictation_service", lambda force=False: None)
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [])
    monkeypatch.setattr(wd.time, "sleep", lambda s: None)
    monkeypatch.setattr(evdev, "list_devices", lambda: evdev_paths)
    monkeypatch.setattr(evdev, "InputDevice", _input_device(denied=denied))
    monkeypatch.setattr(uinput_helper, "_input_group_pending_restart", lambda: True)

    seen = {}

    def fake_run(dialog):
        event = Gdk.Event.new(Gdk.EventType.KEY_RELEASE)
        event.key.keyval = Gdk.KEY_F8
        event.key.window = dialog.get_window()
        dialog.emit("key-release-event", event)
        seen["texts"] = [w.get_text() for w in widgets(dialog) if isinstance(w, Gtk.Label) and w.get_visible()]
        return Gtk.ResponseType.OK

    monkeypatch.setattr(Gtk.Dialog, "run", fake_run)
    wd.show_hotkey_test_dialog()
    return seen["texts"]


def test_dialog_without_keyboard_access_says_seen_not_working(monkeypatch):
    paths = ["/dev/input/event3"]
    texts = _run_dialog_with_f8_release(monkeypatch, paths, denied=paths)
    assert "✓ Seen, not active yet" in texts
    assert "✓ Working!" not in texts
    assert any("restart your computer" in t for t in texts)


def test_dialog_with_only_a_game_controller_says_seen_not_working(monkeypatch):
    paths = ["/dev/input/gamepad", "/dev/input/event3"]
    texts = _run_dialog_with_f8_release(monkeypatch, paths, denied=paths[1:])
    assert "✓ Seen, not active yet" in texts
    assert "✓ Working!" not in texts


def test_dialog_with_keyboard_access_still_says_working(monkeypatch):
    texts = _run_dialog_with_f8_release(monkeypatch, ["/dev/input/event3"])
    assert "✓ Working!" in texts
    assert not any("can't read the keyboard" in t for t in texts)
