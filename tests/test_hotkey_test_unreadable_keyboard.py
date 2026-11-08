"""The hotkey test is honest when the dictation service can't read the keyboard.

The service hears hotkeys only through evdev (/dev/input). Before the user is
in the input group, evdev opens no keyboard, yet the dialog's GTK fallback
still sees the keys. Those keys must show as seen but not active yet, with a
note saying what makes them work, never a plain "Working!". It can be used from the keyboard alone, too.
"""
import logging
import os
import time
from types import SimpleNamespace

import pytest

from gtk_text import widgets
from tuxtalk import keyboard_devices, uinput_helper
from tuxtalk import hotkey_test_dialog as hkd

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
        self._gone = False
        if self._events:
            os.write(self._w, b"x")

    def unplug(self):
        """Gone: select() fires and the read fails, as evdev's does."""
        self._gone = True
        os.write(self._w, b"x")

    def capabilities(self):
        return self._caps

    def fileno(self):
        return self._r

    def read(self):
        if self._gone:
            raise OSError(19, "No such device")
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
    try:
        assert [d.path for d in opened] == ["/dev/input/kbd"]
    finally:
        keyboard_devices.close_devices(opened)


def test_a_device_whose_name_isnt_utf8_is_skipped_and_said_once(monkeypatch, caplog):
    """evdev decodes a device's name strictly: a Bluetooth device with a
    Latin-1 name raised out of the scan, and the hotkey test failed. It's
    left out now, and the log says why once, not at every scan."""
    monkeypatch.setattr(keyboard_devices, "_undecodable", set())

    def make(path):
        if path.endswith("latin1"):
            raise UnicodeDecodeError("utf-8", b"Clavier \xe9", 8, 9, "invalid byte")
        return FakeDevice(path)
    paths = ["/dev/input/latin1", "/dev/input/kbd"]
    with caplog.at_level(logging.INFO, logger=keyboard_devices.logger.name):
        opened = keyboard_devices.open_new_keyboards([], lambda: paths, make)
        try:
            assert [d.path for d in opened] == ["/dev/input/kbd"]
            assert keyboard_devices.open_new_keyboards(opened, lambda: paths, make) == []
        finally:
            keyboard_devices.close_devices(opened)
    said = [r for r in caplog.records
            if r.levelno == logging.INFO and "isn't UTF-8" in r.getMessage()]
    assert len(said) == 1 and "/dev/input/latin1" in said[0].getMessage()


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
    assert "Working!" in hkd._key_seen(True)[0]
    assert hkd._hotkey_test_notice(True) is None


def test_a_key_only_gtk_saw_does_not_claim_working():
    markup, words = hkd._key_seen(False)
    assert "Working" not in markup and "Working" not in words
    assert "not active yet" in markup


def test_with_the_hotkey_helper_the_notice_asks_for_no_restart(monkeypatch):
    """Fix Typing installs the helper there, and its hotkeys work at once."""
    from tuxtalk import secure_input
    monkeypatch.setattr(uinput_helper, "input_group_pending_restart", lambda: False)
    monkeypatch.setattr(secure_input, "can_install", lambda: (True, ""))
    notice = hkd._hotkey_test_notice(False)
    assert uinput_helper.FIX_TYPING_PATH in notice and "restart" not in notice


def test_without_the_hotkey_helper_the_notice_names_fix_typing_and_a_restart(monkeypatch):
    """The same path as System check and the notices, so nobody looks for two places."""
    from tuxtalk import secure_input
    monkeypatch.setattr(uinput_helper, "input_group_pending_restart", lambda: False)
    monkeypatch.setattr(secure_input, "can_install", lambda: (False, "no systemd"))
    notice = hkd._hotkey_test_notice(False)
    assert uinput_helper.FIX_TYPING_PATH in notice and "restart" in notice


TEST_KEYS = {"hold": "F8", "toggle": "F9"}


class _ChangeKey:
    """A row's Change Key button, for what a screen reader is told."""
    description = "Seen, not active yet."

    def get_accessible(self):
        return self

    def get_description(self):
        return self.description

    def set_description(self, words):
        self.description = words


class _Notice:
    """The dialog's notice label: what it shows, and whether."""
    markup, shown = None, False

    def set_markup(self, markup):
        self.markup = markup

    def show(self):
        self.shown = True

    def hide(self):
        self.shown = False


@pytest.mark.parametrize("keyboards, says", [
    ("blind", "see your keyboard"),             # a 1.12 helper reading no keyboard
    ("old", None),                              # a 1.11 helper doesn't say: as before
    ("seeing", None),
    ("none", "Fix Typing Permissions"),
])
def test_a_helper_that_sees_no_keyboard_gets_the_notice_not_silence(monkeypatch, keyboards,
                                                                     says):
    """Issue #26: the helper answered while reading no keyboard, and the
    hotkey test said nothing about why no key ever worked."""
    import threading
    monkeypatch.setattr(uinput_helper, "input_group_pending_restart", lambda: False)
    count = {"blind": 0, "old": None, "seeing": 2}.get(keyboards)
    reading = [] if keyboards == "none" else [SimpleNamespace(helper_keyboards=count)]
    dialog = SimpleNamespace(_stop=threading.Event(), keyboard_notice=_Notice(),
                             _heard_by_reader=set(), keys=dict(TEST_KEYS))
    hkd.HotkeyTestDialog._show_reading(dialog, reading)
    assert dialog.keyboard_notice.shown is bool(says)
    if says:
        assert says in dialog.keyboard_notice.markup


@pytest.mark.parametrize("read_only, says", [(["/dev/input/event3"], "can only be read"),
                                             ([], None)])
def test_a_helper_too_old_for_the_keyboard_says_why_keys_arent_active(monkeypatch,
                                                                       read_only, says):
    """A 1.11 helper skips a keyboard that may only be read: its keys
    showed "Seen, not active yet" with nothing saying why."""
    import threading
    monkeypatch.setattr("tuxtalk.helper_keyboard_access.read_only_keyboards",
                        lambda: read_only)
    helper = SimpleNamespace(helper_keyboards=None, helper_version="1.11")
    dialog = SimpleNamespace(_stop=threading.Event(), keyboard_notice=_Notice(),
                             _heard_by_reader=set(), keys=dict(TEST_KEYS))
    hkd.HotkeyTestDialog._show_reading(dialog, [helper])
    assert dialog.keyboard_notice.shown is bool(says)
    if says:
        assert says in dialog.keyboard_notice.markup and "Update Hotkey Helper" in \
            dialog.keyboard_notice.markup


@pytest.mark.parametrize("kind, version, says, goes", [
    ("keyboards_also_mice", "1.12", "may not reach", True),
    ("read_only_keyboards", "1.11", "keys from that keyboard don&apos;t reach", False)])
def test_an_old_helpers_notice_goes_once_a_key_was_heard_only_where_it_may_not_apply(
        monkeypatch, kind, version, says, goes):
    """A Logitech mouse beside a 1.12 helper asks for the update, though the
    keyboard may well be read: "may not reach TuxTalk" beside "Working!"
    after F8 reached the helper said two things at once. A read-only
    keyboard's keys certainly don't reach it, whatever another keyboard's do."""
    import threading
    monkeypatch.setattr(f"tuxtalk.helper_keyboard_access.{kind}", lambda: ["/dev/input/event5"])
    helper = SimpleNamespace(helper_keyboards=4, helper_version=version)
    dialog = SimpleNamespace(_stop=threading.Event(), keyboard_notice=_Notice(),
                             _heard_by_reader=set(), keys=dict(TEST_KEYS))
    hkd.HotkeyTestDialog._show_reading(dialog, [helper])
    assert dialog.keyboard_notice.shown and says in dialog.keyboard_notice.markup
    dialog._heard_by_reader.add("F8")
    hkd.HotkeyTestDialog._show_reading(dialog, [helper])
    assert dialog.keyboard_notice.shown is not goes


def test_left_ctrl_space_picked_with_a_helper_too_old_for_it_brings_the_notice():
    """Helper 1.13 doesn't report the pair: Change Key saved it without a
    word, and the key showed "Seen, not active yet" with nothing saying why."""
    import threading
    from tuxtalk.config import CTRL_SPACE
    helper = SimpleNamespace(helper_keyboards=1, helper_version="1.13")
    dialog = SimpleNamespace(_stop=threading.Event(), keyboard_notice=_Notice(),
                             _heard_by_reader=set(), keys=dict(TEST_KEYS), capturing="hold",
                             rows={"hold": (_Notice(), _ChangeKey())}, _reading_now=[helper],
                             _show_status=lambda which, key: None, _end_capture=lambda: None)
    dialog._show_reading = lambda kbs: hkd.HotkeyTestDialog._show_reading(dialog, kbs)
    dialog._announce = lambda which, words: hkd.HotkeyTestDialog._announce(dialog, which, words)
    dialog._show_reading([helper])
    assert not dialog.keyboard_notice.shown
    hkd.HotkeyTestDialog._capture(dialog, CTRL_SPACE)
    assert dialog.keys["hold"] == CTRL_SPACE
    assert dialog.keyboard_notice.shown and "too old" in dialog.keyboard_notice.markup
    said = dialog.rows["hold"][1].description           # what Orca reads out
    assert said.startswith("Seen, not active yet.") and "too old" in said


def test_the_helpers_count_is_asked_again_while_the_test_is_open(monkeypatch):
    """Its hello said it once: a keyboard plugged in mid-test turned
    Working beside "can't see your keyboard", and the last one unplugged
    never brought the notice."""
    import threading
    from tuxtalk import secure_input
    helper = SimpleNamespace(helper_keyboards=0)
    dialog = SimpleNamespace(_stop=threading.Event(), keyboard_notice=_Notice(),
                             _heard_by_reader=set(), keys=dict(TEST_KEYS))
    dialog._show_reading = lambda kbs: hkd.HotkeyTestDialog._show_reading(dialog, kbs)
    monkeypatch.setattr(hkd.GLib, "idle_add", lambda fn, *args: fn(*args))
    dialog._show_reading([helper])
    assert dialog.keyboard_notice.shown
    for count, shown in ((1, False), (None, False), (0, True)):
        monkeypatch.setattr(secure_input, "keyboards_watched", lambda count=count: count)
        hkd.HotkeyTestDialog._recount_helper(dialog, [helper])
        assert dialog.keyboard_notice.shown is shown, count


@pytest.mark.parametrize("keyname, logged", [("F8", True), ("Right Ctrl", True),
                                             ("a", False), ("Return", False),
                                             ("space", False), ("BackSpace", False)])
def test_only_test_keys_are_logged(keyname, logged, caplog):
    """Without the hotkey helper the test hears keys typed in any window: a
    line per letter would put a password's length and timing in the log,
    which Report a Problem shares."""
    dialog = SimpleNamespace(ready=True, capturing=None, _mark_working=lambda key: None)
    with caplog.at_level(logging.INFO):
        for action in ("press", "release"):
            hkd.HotkeyTestDialog._on_key(dialog, keyname, action)
    assert ("Hotkey test:" in caplog.text) is logged


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


def _key(dialog, keyval, signal="key-press-event", state=0):
    """A key press (or release) as GTK delivers it to the dialog, with the
    modifiers in *state* held."""
    from gi.repository import Gdk
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS if signal == "key-press-event"
                          else Gdk.EventType.KEY_RELEASE)
    event.key.keyval = keyval
    event.key.state = Gdk.ModifierType(state)
    # GTK finds key bindings (Tab, Space) by the key's code, not its name.
    found, keys = Gdk.Keymap.get_for_display(dialog.get_display()).get_entries_for_keyval(keyval)
    if found:
        event.key.hardware_keycode = keys[0].keycode
    event.key.window = dialog.get_window()
    dialog.emit(signal, event)


def _run_dialog_with_f8_release(monkeypatch, evdev_paths, denied=()):
    """Run the real dialog; while it is up, release F8 through GTK and
    return the visible label texts."""
    from gi.repository import Gdk
    seen = {}

    def during(dialog):
        _key(dialog, Gdk.KEY_F8, "key-release-event")
        seen["texts"] = _visible_texts(dialog)
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


def _until(condition, seconds=5):
    """Run GTK's loop until *condition*() holds (the reader's keys arrive
    through it), at most *seconds*."""
    from gi.repository import Gtk
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        Gtk.main_iteration_do(False)


F8_PRESS_AND_RELEASE = [SimpleNamespace(type=EV_KEY, code=66, value=value) for value in (1, 0)]


def _run_dialog_with_f8_from_the_reader(monkeypatch, during):
    """The real dialog, with a keyboard the reader opens once it runs whose
    F8 it hears (a keyboard's keys from before the test are flushed)."""
    monkeypatch.setattr(hkd, "RESCAN_SECONDS", 0)
    paths = ["/dev/input/event3"]

    def plug_in_then(dialog):
        paths.append("/dev/input/event5")
        during(dialog)
    _run_dialog(monkeypatch, paths, plug_in_then,
                queued={"/dev/input/event5": F8_PRESS_AND_RELEASE})


def test_a_screen_reader_is_told_how_a_key_tested(monkeypatch):
    """The status beside each key is a plain label, which a screen reader
    isn't told about when it changes: the row's Change Key button says it."""
    from gi.repository import Gtk
    seen = {}

    def during(dialog):
        buttons = [w for w in widgets(dialog)
                   if isinstance(w, Gtk.Button) and w.get_label() == "Change Key"]
        _until(lambda: buttons[0].get_accessible().get_description() == "Working")
        seen["after F8"] = buttons[0].get_accessible().get_description()
        buttons[1].clicked()
        seen["changing"] = buttons[1].get_accessible().get_description()
    _run_dialog_with_f8_from_the_reader(monkeypatch, during)
    assert seen["after F8"] == "Working"
    assert "Esc to cancel" in seen["changing"]


def test_dialog_with_keyboard_access_still_says_working(monkeypatch):
    seen = {}

    def during(dialog):
        _until(lambda: "✓ Working!" in _visible_texts(dialog))
        seen["texts"] = _visible_texts(dialog)
    _run_dialog_with_f8_from_the_reader(monkeypatch, during)
    assert "✓ Working!" in seen["texts"]
    assert not any("can't read the keyboard" in t for t in seen["texts"])


def test_a_key_only_this_window_saw_isnt_working_though_a_keyboard_is_open(monkeypatch):
    """Issue #26: the hotkey helper couldn't see the user's keyboard, and the
    test said "Working" for F8 and F9 because the focused test window got
    them; the dictation service never did."""
    texts = _run_dialog_with_f8_release(monkeypatch, ["/dev/input/event3"])
    assert "✓ Seen, not active yet" in texts
    assert "✓ Working!" not in texts


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
            seen["texts"] = _visible_texts(dialog)
    _run_dialog(monkeypatch, paths, during, queued={plugged: f8})
    assert "✓ Working!" in seen["texts"]


def _visible_texts(dialog):
    from gi.repository import Gtk
    return [w.get_text() for w in widgets(dialog)
            if isinstance(w, Gtk.Label) and w.get_visible()]


def test_a_keyboard_plugged_in_after_the_test_opened_with_none_is_heard(monkeypatch):
    """With no keyboard to open at the start (a Bluetooth one still
    connecting) the test never looked again, so F8 on it went unseen."""
    from gi.repository import Gtk
    monkeypatch.setattr(hkd, "RESCAN_SECONDS", 0)
    paths, plugged = [], "/dev/input/event5"
    f8 = [SimpleNamespace(type=EV_KEY, code=66, value=value) for value in (1, 0)]
    seen = {}

    def during(dialog):
        seen["before"] = _visible_texts(dialog)
        paths.append(plugged)
        deadline = time.monotonic() + 5
        while "✓ Working!" not in seen.setdefault("after", []) and time.monotonic() < deadline:
            Gtk.main_iteration_do(False)
            seen["after"] = _visible_texts(dialog)
    _run_dialog(monkeypatch, paths, during, queued={plugged: f8})
    assert any("can't read the keyboard" in t for t in seen["before"])
    assert "✓ Working!" in seen["after"]
    assert not any("can't read the keyboard" in t for t in seen["after"])


def test_after_the_only_keyboard_is_unplugged_a_key_isnt_called_working(monkeypatch):
    """The test kept the keyboards it opened at the start: with the only one
    unplugged, a key GTK saw still read "Working!", though the service had
    nothing to hear it through."""
    from gi.repository import Gdk, Gtk
    made, seen = [], {}

    class Recorded(hkd.HotkeyTestDialog):
        def __init__(self, config):
            super().__init__(config)
            made.append(self)
    monkeypatch.setattr(hkd, "HotkeyTestDialog", Recorded)
    paths = ["/dev/input/event3"]

    def notice_shown(dialog):
        return any("can't read the keyboard" in t for t in _visible_texts(dialog))

    def during(dialog):
        paths.clear()
        made[0].keyboards[0].unplug()
        deadline = time.monotonic() + 5
        while not notice_shown(dialog) and time.monotonic() < deadline:
            Gtk.main_iteration_do(False)
        _key(dialog, Gdk.KEY_F8, "key-release-event")
        seen["texts"] = _visible_texts(dialog)
        seen["notice"] = notice_shown(dialog)
    _run_dialog(monkeypatch, paths, during)
    assert seen["notice"]
    assert "✓ Seen, not active yet" in seen["texts"]
    assert "✓ Working!" not in seen["texts"]


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


# --- Left Ctrl+Space ---------------------------------------------------------------

def _pair_dialog(monkeypatch, during, paths=None, queued=None, hotkeys=("Left Ctrl+Space", "F9")):
    """The real dialog, by default with Left Ctrl+Space as the hold key, and
    no hotkey helper asked about it."""
    from tuxtalk import secure_input
    monkeypatch.setattr(secure_input, "running_version", lambda: None)
    _run_dialog(monkeypatch, paths or ["/dev/input/event3"], during, hotkeys=hotkeys,
                queued=queued)


def _reader_hears(monkeypatch, steps):
    """The visible texts once the reader heard *steps* ((code, value), on a
    keyboard plugged in after it flushed the old keys) and marked a key."""
    monkeypatch.setattr(hkd, "RESCAN_SECONDS", 0)
    paths, seen = ["/dev/input/event3"], {}

    def during(dialog):
        paths.append("/dev/input/event5")
        _until(lambda: "✓ Working!" in _visible_texts(dialog))
        seen["texts"] = _visible_texts(dialog)
    events = [SimpleNamespace(type=EV_KEY, code=code, value=value) for code, value in steps]
    _pair_dialog(monkeypatch, during, paths, queued={"/dev/input/event5": events})
    return seen["texts"]


def test_left_ctrl_space_from_the_keyboard_reader_shows_working(monkeypatch):
    """Letting go of Left Ctrl before Space, as people do, still counts."""
    texts = _reader_hears(monkeypatch, [(29, 1), (57, 1), (29, 0), (57, 0)])
    assert texts.count("✓ Working!") == 1


def test_a_space_typed_alone_isnt_left_ctrl_space(monkeypatch):
    """Without the hotkey helper the test hears spaces typed in any window."""
    texts = _reader_hears(monkeypatch, [(57, 1), (57, 0), (67, 1), (67, 0)])   # then F9
    assert texts.count("✓ Working!") == 1       # F9's
    assert "Not tested" in texts


def test_ctrl_space_on_a_focused_button_tests_the_key_and_presses_nothing(monkeypatch):
    """Space presses the focused button, so the test can be used without a
    mouse; with Ctrl held it's the dictation key, and Change Key must not
    open under it."""
    from gi.repository import Gdk, Gtk
    seen = {}

    def during(dialog):
        buttons = [w for w in widgets(dialog)
                   if isinstance(w, Gtk.Button) and w.get_label() == "Change Key"]
        buttons[0].grab_focus()
        ctrl = Gdk.ModifierType.CONTROL_MASK
        _key(dialog, Gdk.KEY_space, state=ctrl)
        _key(dialog, Gdk.KEY_space, "key-release-event", state=ctrl)
        seen["capturing"] = not buttons[0].get_sensitive()
        seen["texts"] = _visible_texts(dialog)
    _pair_dialog(monkeypatch, during)
    assert not seen["capturing"]
    assert "✓ Seen, not active yet" in seen["texts"]


def test_change_key_takes_left_ctrl_space_without_refusing_its_ctrl(monkeypatch):
    """Left Ctrl goes down first: it must not be turned away as a key that
    can't dictate before the Space arrives."""
    from gi.repository import Gdk, Gtk
    seen = {}

    def during(dialog):
        buttons = [w for w in widgets(dialog)
                   if isinstance(w, Gtk.Button) and w.get_label() == "Change Key"]
        buttons[1].clicked()
        _key(dialog, Gdk.KEY_Control_L)
        seen["after ctrl"] = _visible_texts(dialog)
        _key(dialog, Gdk.KEY_space, state=Gdk.ModifierType.CONTROL_MASK)
        seen["after space"] = _visible_texts(dialog)
    _pair_dialog(monkeypatch, during, hotkeys=("F8", "F9"))
    assert not any("can't be used" in t for t in seen["after ctrl"])
    assert any(t.startswith("Toggle mode: Left Ctrl+Space") for t in seen["after space"])
