"""The hotkey test is honest when the dictation service can't read the keyboard.

The service hears hotkeys only through evdev (/dev/input). Before the user is
in the input group, evdev opens no keyboard, yet the dialog's GTK fallback
still sees the keys. Those keys must show as seen but not active yet, with a
note saying what makes them work, never a plain "Working!". It can be used from the keyboard alone, too.
"""
import os
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("gi")
from gtk_text import widgets  # noqa: E402
from tuxtalk import keyboard_devices, uinput_helper  # noqa: E402
from tuxtalk import hotkey_test_dialog as hkd  # noqa: E402

EV_KEY = 1
KEYBOARD_KEYS = [30, 48, 46, 32, 66]        # KEY_A, KEY_B, KEY_C, KEY_D, KEY_F8
GAMEPAD_KEYS = [0x130, 0x131, 0x133, 0x134]  # BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST


class FakeDevice:
    """An evdev device on a pipe: select() fires only while it holds *events*."""

    def __init__(self, path, kind="keyboard", events=()):
        self.path, self.name = path, path
        self._caps = {"keyboard": {EV_KEY: KEYBOARD_KEYS},
                      "gamepad": {EV_KEY: GAMEPAD_KEYS, 3: [0, 1]},
                      "mouse": {2: [0]}}[kind]
        self._r, self._w = os.pipe()
        self._events = list(events)
        if self._events:
            os.write(self._w, b"x")

    def capabilities(self):
        return self._caps

    def fileno(self):
        return self._r

    def read(self):
        if not self._events:
            raise BlockingIOError
        os.read(self._r, 1)
        events, self._events = self._events, []
        yield from events

    def close(self):
        for fd in (self._r, self._w):
            try:
                os.close(fd)
            except OSError:
                pass


def _input_device(denied=(), queued=None):
    """evdev's InputDevice for the fake nodes: *denied* paths can't be
    opened, and *queued* maps a path to the events its keyboard holds."""
    def make(path):
        if path in denied:
            raise PermissionError(path)
        return FakeDevice(path, kind=path.rpartition("/")[2] if path.endswith(
            ("gamepad", "mouse")) else "keyboard", events=(queued or {}).get(path, ()))
    return make


def test_keyboards_are_opened():
    paths = ["/dev/input/kbd", "/dev/input/mouse"]
    opened = keyboard_devices.open_new_keyboards([], lambda: paths, _input_device())
    assert [d.path for d in opened] == ["/dev/input/kbd"]


def test_a_game_controller_is_not_a_keyboard():
    """logind lets any user open a game controller, and it reports key events;
    the service still can't hear F8 through it."""
    paths = ["/dev/input/gamepad"]
    assert keyboard_devices.open_new_keyboards([], lambda: paths, _input_device()) == []


def test_no_devices_when_permission_is_denied():
    paths = ["/dev/input/kbd"]
    assert keyboard_devices.open_new_keyboards([], lambda: paths, _input_device(denied=paths)) == []


def test_no_devices_when_listing_fails():
    def boom():
        raise OSError("no /dev/input")
    assert keyboard_devices.open_new_keyboards([], boom, _input_device()) == []


def test_a_key_the_service_can_read_shows_working():
    assert "Working!" in hkd._key_seen_markup(True)
    assert hkd._hotkey_test_notice(True) is None


def test_a_key_only_gtk_saw_does_not_claim_working():
    markup = hkd._key_seen_markup(False)
    assert "Working" not in markup
    assert "not active yet" in markup


def test_the_notice_asks_for_a_restart_when_setup_is_done(monkeypatch):
    monkeypatch.setattr(uinput_helper, "input_group_pending_restart", lambda: True)
    notice = hkd._hotkey_test_notice(False)
    assert "restart your computer" in notice
    assert "Fix Typing" not in notice


def test_the_notice_points_to_fix_typing_when_setup_is_not_done(monkeypatch):
    monkeypatch.setattr(uinput_helper, "input_group_pending_restart", lambda: False)
    notice = hkd._hotkey_test_notice(False)
    assert "Fix Typing Permissions" in notice
    assert "restart" in notice


def _run_dialog(monkeypatch, evdev_paths, during, denied=(), hotkeys=("F8", "F9"),
                queued=None):
    """Run the real dialog, with the dictation service and the settings left
    alone, and call *during(dialog)* while it is up. *hotkeys* are the
    (hold, toggle) keys it starts with; *queued* as for _input_device."""
    from gi.repository import Gtk
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    evdev = pytest.importorskip("evdev")
    from tuxtalk import config, gnome_keybindings, service_launcher

    monkeypatch.setattr(config, "load_config",
                        lambda: SimpleNamespace(hotkey=hotkeys[0], toggle_hotkey=hotkeys[1]))
    monkeypatch.setattr(config, "save_config", lambda c: None)
    monkeypatch.setattr(gnome_keybindings, "disable_for_hotkey_test", lambda: None)
    monkeypatch.setattr(gnome_keybindings, "restore", lambda saved: None)
    monkeypatch.setattr(service_launcher, "stop_dictation_service", lambda force=False: None)
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [])
    monkeypatch.setattr(hkd.time, "sleep", lambda s: None)
    monkeypatch.setattr(evdev, "list_devices", lambda: evdev_paths)
    monkeypatch.setattr(evdev, "InputDevice", _input_device(denied=denied, queued=queued))
    monkeypatch.setattr(uinput_helper, "input_group_pending_restart", lambda: True)

    monkeypatch.setattr(Gtk.Dialog, "run", lambda dialog: during(dialog) or Gtk.ResponseType.OK)
    hkd.show_hotkey_test_dialog()


def _key(dialog, keyval, signal="key-press-event"):
    """A key press (or release) as GTK delivers it to the dialog."""
    from gi.repository import Gdk
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS if signal == "key-press-event"
                          else Gdk.EventType.KEY_RELEASE)
    event.key.keyval = keyval
    # GTK finds key bindings (Tab, Space) by the key's code, not its name.
    found, keys = Gdk.Keymap.get_for_display(dialog.get_display()).get_entries_for_keyval(keyval)
    if found:
        event.key.hardware_keycode = keys[0].keycode
    event.key.window = dialog.get_window()
    dialog.emit(signal, event)


def _run_dialog_with_f8_release(monkeypatch, evdev_paths, denied=()):
    """Run the real dialog; while it is up, release F8 through GTK and
    return the visible label texts."""
    from gi.repository import Gdk, Gtk
    seen = {}

    def during(dialog):
        _key(dialog, Gdk.KEY_F8, "key-release-event")
        seen["texts"] = [w.get_text() for w in widgets(dialog)
                         if isinstance(w, Gtk.Label) and w.get_visible()]
    _run_dialog(monkeypatch, evdev_paths, during, denied)
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


def test_a_keyboard_plugged_in_during_the_test_is_heard(monkeypatch):
    """The test opened the keyboards once, as it started: F8 on a keyboard
    plugged in while it was open went unseen, though the service hears it."""
    from gi.repository import Gtk
    monkeypatch.setattr(hkd, "RESCAN_SECONDS", 0)
    paths, plugged = ["/dev/input/event3"], "/dev/input/event5"
    f8 = [SimpleNamespace(type=EV_KEY, code=66, value=value) for value in (1, 0)]
    seen = {}

    def during(dialog):
        paths.append(plugged)
        deadline = time.monotonic() + 5
        while "texts" not in seen or ("✓ Working!" not in seen["texts"]
                                      and time.monotonic() < deadline):
            Gtk.main_iteration_do(False)
            seen["texts"] = [w.get_text() for w in widgets(dialog)
                             if isinstance(w, Gtk.Label) and w.get_visible()]
    _run_dialog(monkeypatch, paths, during, queued={plugged: f8})
    assert "✓ Working!" in seen["texts"]


# --- used from the keyboard alone ------------------------------------------------

def test_the_hotkey_test_can_be_used_without_a_mouse(monkeypatch):
    """Tab goes on to the dialog, to move the focus, and Space presses Change
    Key; the test used to take every key for itself, so nothing in it could
    be reached from the keyboard."""
    from gi.repository import Gdk, Gtk
    seen = {"dialog got": []}

    def during(dialog):
        # Connected after the test's own handler: it hears only what that passed on.
        dialog.connect("key-press-event",
                       lambda d, e: seen["dialog got"].append(Gdk.keyval_name(e.keyval)))
        for key in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Return, Gdk.KEY_F8):
            _key(dialog, key)
        buttons = [w for w in widgets(dialog)
                   if isinstance(w, Gtk.Button) and w.get_label() == "Change Key"]
        buttons[0].grab_focus()
        _key(dialog, Gdk.KEY_space)
        _key(dialog, Gdk.KEY_space, "key-release-event")
        seen["capturing"] = not buttons[0].get_sensitive()
        seen["names"] = [b.get_accessible().get_name() for b in buttons]
    _run_dialog(monkeypatch, ["/dev/input/event3"], during)
    assert seen["dialog got"] == ["Tab", "ISO_Left_Tab", "Return", "space"]
    assert seen["capturing"]
    assert seen["names"] == ["Change the push-to-talk key", "Change the toggle mode key"]


def test_keys_still_queued_when_the_test_closes_change_nothing(monkeypatch):
    """The keyboard reader hands its keys to GTK's thread; one still waiting
    there when the window has closed must not mark a key on it."""
    made = []

    class Recorded(hkd.HotkeyTestDialog):
        def __init__(self, config):
            super().__init__(config)
            made.append(self)
    monkeypatch.setattr(hkd, "HotkeyTestDialog", Recorded)
    _run_dialog(monkeypatch, ["/dev/input/event3"], lambda dialog: None)
    from gi.repository import Gdk
    release = Gdk.Event.new(Gdk.EventType.KEY_RELEASE)
    release.key.keyval = Gdk.KEY_F8
    made[0]._on_gtk_release(None, release.key)
    assert made[0].rows["hold"][2].get_text() == "Not tested"
