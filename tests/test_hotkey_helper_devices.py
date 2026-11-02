"""The hotkey helper (hotkey_helper.py): which input devices it reads as
keyboards, what it says about those it leaves out, and how a keyboard it
may only read is used (issue #26). The rest is in test_hotkey_helper.py."""
import ctypes
import errno
import os
import struct

import pytest

from hotkey_helper_fakes import FakeKeyboard
from hotkey_helper_fakes import ready_client as _ready
from tuxtalk import hotkey_helper as hh

F8, RCTRL, LSHIFT = 66, 97, 42


# --- which devices are keyboards -----------------------------------------------------------

_LETTERS = set(range(16, 26)) | set(range(30, 39))      # Q..P, A..L


@pytest.mark.parametrize("name,read", [
    ("USB Keyboard", True),
    ("kanata", True),                       # a remapper's keyboard (issue #23)
    (hh.OWN_KEYBOARD_NAME, False)])         # the one TuxTalk types with
def test_which_devices_are_read_as_keyboards(name, read):
    assert _counts_as_keyboard(_LETTERS | {hh.BTN_LEFT}, name) is read


def test_a_device_without_letter_keys_is_no_keyboard():
    assert _counts_as_keyboard({hh.BTN_LEFT, 66}, "Power Button") is False


def _counts_as_keyboard(keys, name):
    try:
        hh.check_keyboard(keys, name)
    except hh.Skipped:
        return False
    return True


def test_only_a_uinput_device_counts_as_made_by_a_program(tmp_path):
    """A Bluetooth LE mouse comes through /dev/uhid, under /sys/devices/virtual
    too: taken for a program's keyboard, a mouse claiming letter keys was read."""
    chars = tmp_path / "char"
    chars.mkdir()
    (chars / "13:80").symlink_to("/sys/devices/virtual/input/input40/event20")
    (chars / "13:81").symlink_to(
        "/sys/devices/virtual/misc/uhid/0005:046D:B023.0001/input/input41/event21")
    (chars / "13:82").symlink_to("/sys/devices/pci0000:00/0000:11:00.0/usb1/1-3/input/input42/event22")
    assert hh.made_through_uinput(os.makedev(13, 80), str(chars)) is True
    assert hh.made_through_uinput(os.makedev(13, 81), str(chars)) is False
    assert hh.made_through_uinput(os.makedev(13, 82), str(chars)) is False


def test_the_helper_knows_the_name_tuxtalk_types_with():
    """The helper runs as root on its own and can't import TuxTalk."""
    from tuxtalk import virtual_keyboard
    assert hh.OWN_KEYBOARD_NAME == virtual_keyboard.NAME


def test_a_remappers_keyboard_is_listened_to_but_never_held(setup, capsys):
    """kanata's keyboard carries the mouse's motion and the text TuxTalk
    types: held while the hotkey is down, the mouse froze and the dictation
    typed meanwhile was lost. Its keys reach the desktop as they are, and
    the service is told that's how this keyboard works, with the journal
    saying so once rather than at every dictation (issue #23)."""
    helper, kb, connect, say, heard, _seat = setup
    kb.listen_only = "a program's keyboard and mouse in one"
    sock = _ready(connect, say, heard)
    capsys.readouterr()
    for _ in range(2):
        kb.press(F8)
        assert heard(sock) == ["DOWN 66 -"]
        say(sock, "HIDE 66")
        assert heard(sock) == ["ERR listen-only keyboard"]
        assert kb.calls == [] and helper.hide is None
        kb.press(F8, 0)
        assert heard(sock) == ["UP 66"]
    assert capsys.readouterr().out.count("not holding keyboard") == 1


# --- devices it can't or won't read (issue #26) --------------------------------------------

class FakeEvdev:
    """The kernel's side of Keyboard.open for a plain file standing in for a
    device node: what each ioctl answers, and the grabs asked for."""

    def __init__(self, keys, pointer=False, name="USB Keyboard"):
        self.keys, self.pointer, self.name = keys, pointer, name
        self.grabs, self.masks = [], []

    def ioctl(self, fd, request, arg=0, mutate=False):
        if request == hh.EVIOCGRAB:
            self.grabs.append(arg)
        elif request == hh.EVIOCSMASK:
            self.masks.append(arg)
        elif request == hh.EVIOCGBIT(hh.EV_KEY, hh.KEY_BYTES):
            for code in self.keys:
                arg[code // 8] |= 1 << code % 8
        elif request == hh.EVIOCGBIT(hh.EV_REL, 2) and self.pointer:
            arg[0] |= 1 << hh.REL_X
        elif request == hh.EVIOCGNAME(256):
            arg[:len(self.name)] = self.name.encode()
        return 0


def _device(folder, name="event7", writable=True):
    """A plain file at *folder*/*name* that the helper opens as a device node."""
    node = folder / name
    node.write_bytes(b"")
    node.chmod(0o644 if writable else 0o444)
    return node


needs_permissions = pytest.mark.skipif(os.geteuid() == 0, reason="root opens any file")


@needs_permissions
def test_a_keyboard_refused_read_write_is_watched_read_only_and_never_held(
        setup, monkeypatch, tmp_path, capsys):
    """The helper opened keyboards read-write only: where the input group
    may only read them, it watched none and nothing said so. Read-only, its
    hotkeys are reported; it's never grabbed nor written to, and a hide is
    turned down the way a remapper's keyboard's is."""
    helper, fake, connect, say, heard, _seat = setup
    helper.keyboards.pop(fake.path)
    node = _device(tmp_path / "input", writable=False)
    evdev = FakeEvdev(_LETTERS | {F8})
    monkeypatch.setattr(hh, "fcntl", evdev)
    helper.scan()
    kb = helper.keyboards[str(node)]
    assert kb.listen_only and "never held: it may only be read here" in capsys.readouterr().out
    sock = _ready(connect, say, heard)
    node.chmod(0o644)
    with node.open("ab") as events:
        events.write(hh.EVENT.pack(0, 0, hh.EV_KEY, F8, 1))
    assert heard(sock) == ["DOWN 66 -"]
    real_write, written_to = os.write, []
    monkeypatch.setattr(hh.os, "write",
                        lambda fd, data: written_to.append(fd) or real_write(fd, data))
    say(sock, "HIDE 66")
    assert heard(sock) == ["ERR listen-only keyboard"]
    assert evdev.grabs == [] and helper.hide is None and kb.fd not in written_to


@needs_permissions
def test_a_device_that_cant_be_opened_is_logged_once_with_why(setup, monkeypatch, tmp_path,
                                                              capsys):
    helper, _kb, _connect, _say, _heard, _seat = setup
    node = _device(tmp_path / "input")
    node.chmod(0)
    monkeypatch.setattr(hh, "fcntl", FakeEvdev(_LETTERS))
    for _ in range(3):
        helper.scan()
    out = capsys.readouterr().out
    assert out.count(f"not watching {node}: couldn't open it (EACCES") == 1


@pytest.mark.parametrize("evdev", [
    FakeEvdev({116}, name="Power Button"),                  # no letter keys
    FakeEvdev(_LETTERS, name=hh.OWN_KEYBOARD_NAME),         # TuxTalk's own
])
def test_a_device_that_is_no_keyboard_is_left_out_without_a_word(
        setup, monkeypatch, tmp_path, capsys, evdev):
    """Power buttons and lid switches would fill the journal."""
    helper, _kb, _connect, _say, _heard, _seat = setup
    node = _device(tmp_path / "input")
    monkeypatch.setattr(hh, "fcntl", evdev)
    for _ in range(3):
        helper.scan()
    assert str(node) not in helper.keyboards
    assert "not watching" not in capsys.readouterr().out


@pytest.mark.parametrize("name", ["Logitech MX Keys", "Logitech MX Master"])
def test_a_keyboard_that_is_a_mouse_too_is_read_with_its_motion_kept_out(
        setup, monkeypatch, tmp_path, name):
    """A Logitech receiver gives its keyboards a mouse's motion and buttons
    (an MX Keys, issue #26) and its mice a keyboard's keys: left out as a
    mouse, the keyboard's F8 never reached TuxTalk. Hardware, so it isn't
    listen-only; its motion is masked off so the mouse never wakes the helper."""
    helper, _kb, _connect, _say, _heard, _seat = setup
    node = _device(tmp_path / "input")
    evdev = FakeEvdev(_LETTERS | {F8, hh.BTN_LEFT}, pointer=True, name=name)
    monkeypatch.setattr(hh, "fcntl", evdev)
    monkeypatch.setattr(hh, "made_through_uinput", lambda rdev: False)
    helper.scan()
    kb = helper.keyboards[str(node)]
    assert kb.name == name and kb.mouse and kb.listen_only is None
    assert len(evdev.masks) == 1


def test_a_hotkey_on_a_keyboard_that_is_a_mouse_too_is_hidden(setup):
    """An MX Keys holds its own F8 like any keyboard, so it doesn't repeat
    into the window being dictated into, and gets it back at the release."""
    helper, kb, connect, say, heard, _seat = setup
    kb.mouse = True
    sock = _ready(connect, say, heard)
    kb.press(F8)
    assert heard(sock) == ["DOWN 66 -"]
    say(sock, "HIDE 66")
    assert heard(sock) == ["OK HIDE"]
    assert ("grab", True) in kb.calls
    kb.press(F8, 0)
    assert heard(sock) == ["UP 66"]
    assert kb.calls[-1] == ("grab", False) and helper.hide is None


def test_a_mouse_holding_shift_for_a_hotkey_elsewhere_is_never_held(setup):
    """Shift on a Logitech mouse's button and F8 on the keyboard: the mouse
    would be held with the keyboard, its pointer frozen until F8 came up.
    The hide is refused whole, as for a read-only keyboard."""
    helper, kb, connect, say, heard, _seat = setup
    mouse = FakeKeyboard("/dev/input/event-mouse")
    mouse.mouse = True
    kb.group = mouse.group = "real"
    helper.keyboards[mouse.path] = mouse
    sock = _ready(connect, say, heard, "66 S:66")
    mouse.press(LSHIFT)
    assert heard(sock) == []
    kb.press(F8)
    assert heard(sock) == ["DOWN 66 42"]
    say(sock, "HIDE 66 42")
    assert heard(sock) == ["ERR a mouse holds a modifier"]
    assert kb.calls == mouse.calls == [] and helper.hide is None


def test_watching_only_mice_is_said_once(setup, capsys):
    """A Logitech mouse counts as a keyboard, so the count can't say the
    keyboard itself is missing; the journal does."""
    helper, kb, _connect, _say, _heard, _seat = setup
    kb.mouse = True
    for _ in range(3):
        helper.scan()
    assert capsys.readouterr().out.count("watching only keyboards that are mice too") == 1


def test_a_programs_keyboard_and_mouse_in_one_is_never_held(setup, monkeypatch, tmp_path):
    helper, _kb, _connect, _say, _heard, _seat = setup
    node = _device(tmp_path / "input")
    monkeypatch.setattr(hh, "fcntl", FakeEvdev(_LETTERS | {hh.BTN_LEFT}, pointer=True,
                                               name="kanata"))
    monkeypatch.setattr(hh, "made_through_uinput", lambda rdev: True)
    helper.scan()
    assert helper.keyboards[str(node)].listen_only == "a program's keyboard and mouse in one"


def test_only_key_events_are_asked_of_the_kernel(monkeypatch):
    """EVIOCSMASK (_IOW('E', 0x93, struct input_mask) in linux/input.h) on
    type 0 takes a bitmap of event types in C longs: EV_SYN and EV_KEY."""
    asked = []
    monkeypatch.setattr(hh.fcntl, "ioctl", lambda fd, request, arg: asked.append((request, arg)))
    hh.mask_to_keys(3)
    request, arg = asked[0]
    assert request == 0x40104593
    kind, size, address = struct.unpack("IIQ", arg)
    assert (kind, size) == (hh.EV_SYN, ctypes.sizeof(ctypes.c_long))
    assert ctypes.c_ulong.from_address(address).value == 0b11


def test_a_kernel_that_cant_mask_events_still_has_the_keyboard_read(
        setup, monkeypatch, tmp_path, capsys):
    """Before Linux 4.4 there's no EVIOCSMASK: the keyboard is read all the
    same, every event of it, and the journal says so once."""
    helper, _kb, _connect, _say, _heard, _seat = setup
    node = _device(tmp_path / "input")

    class NoMasks(FakeEvdev):
        def ioctl(self, fd, request, arg=0, mutate=False):
            if request == hh.EVIOCSMASK:
                raise OSError(errno.EINVAL, "Invalid argument")
            return super().ioctl(fd, request, arg, mutate)
    monkeypatch.setattr(hh, "fcntl", NoMasks(_LETTERS))
    for _ in range(2):
        helper.scan()
    assert str(node) in helper.keyboards
    assert capsys.readouterr().out.count(f"reading every event of {node}") == 1


def test_another_seats_keyboard_is_logged_once(setup, monkeypatch, tmp_path, capsys):
    helper, _kb, _connect, _say, _heard, _seat = setup
    node = _device(tmp_path / "input")
    monkeypatch.setattr(hh, "fcntl", FakeEvdev(_LETTERS))
    monkeypatch.setattr(hh, "udev_seat", lambda rdev: "seat1")
    helper.scan()
    helper.scan()
    assert capsys.readouterr().out.count(
        f"not watching {node}: 'USB Keyboard' is on seat1") == 1


def test_watching_no_keyboard_at_all_is_said_once_until_one_comes(setup, monkeypatch,
                                                                   tmp_path, capsys):
    helper, fake, _connect, _say, _heard, _seat = setup
    helper.keyboards.pop(fake.path)
    helper.scan()
    helper.scan()
    assert capsys.readouterr().out.count("watching no keyboard at all") == 1
    node = _device(tmp_path / "input")
    monkeypatch.setattr(hh, "fcntl", FakeEvdev(_LETTERS))
    helper.scan()
    helper.drop_keyboard(helper.keyboards[str(node)])
    node.unlink()
    helper.scan()
    assert capsys.readouterr().out.count("watching no keyboard at all") == 1


def test_the_hello_says_how_many_keyboards_are_watched(setup):
    helper, _kb, connect, say, heard, _seat = setup
    second = FakeKeyboard("/dev/input/event-two")
    helper.keyboards[second.path] = second
    sock = connect()
    say(sock, "HELLO 1")
    assert heard(sock) == [f"HELLO 1 {hh.VERSION} keyboards=2"]


def _read_only_beside(helper, kb):
    """A keyboard that may only be read, in the real keyboards' group with
    the setup's writable *kb* (two keyboards, one of them 0640)."""
    other = FakeKeyboard("/dev/input/event-read-only")
    other.listen_only = "it may only be read here"
    kb.group = other.group = "real"
    helper.keyboards[other.path] = other
    return other


def test_a_hide_with_shift_on_a_read_only_keyboard_is_refused_whole(setup):
    """Shift held on a read-only keyboard and F8 on a writable one: hiding
    F8 alone was answered OK while the desktop still saw Shift down, so text
    typed during the hold came out shifted."""
    helper, kb, connect, say, heard, _seat = setup
    other = _read_only_beside(helper, kb)
    sock = _ready(connect, say, heard, "66 S:66")
    other.press(LSHIFT)
    assert heard(sock) == []
    kb.press(F8)
    assert heard(sock) == ["DOWN 66 42"]
    say(sock, "HIDE 66 42")
    assert heard(sock) == ["ERR listen-only keyboard"]
    assert kb.calls == other.calls == [] and helper.hide is None


def test_a_hide_of_right_ctrl_on_a_read_only_keyboard_holds_no_other(setup):
    """Right Ctrl, the dictation key, on a read-only keyboard and Shift on a
    writable one: the writable keyboard was held, swallowing what was typed
    on it, and the answer OK while the desktop still saw Ctrl down."""
    helper, kb, connect, say, heard, _seat = setup
    other = _read_only_beside(helper, kb)
    sock = _ready(connect, say, heard, "97 S:66")
    kb.press(LSHIFT)
    assert heard(sock) == []
    other.press(RCTRL)
    assert heard(sock) == ["DOWN 97 42"]
    say(sock, "HIDE 97 42")
    assert heard(sock) == ["ERR listen-only keyboard"]
    assert kb.calls == other.calls == [] and helper.hide is None


@needs_permissions
def test_a_read_only_keyboard_made_writable_is_opened_again_to_be_held(setup, monkeypatch,
                                                                       tmp_path):
    """Opened read-only, it stayed listen-only for good: after a udev rule
    or a permission fix, its held hotkey still reached the desktop until
    it was unplugged or the helper restarted."""
    helper, fake, _connect, _say, _heard, _seat = setup
    helper.keyboards.pop(fake.path)
    node = _device(tmp_path / "input", writable=False)
    monkeypatch.setattr(hh, "fcntl", FakeEvdev(_LETTERS))
    helper.scan()
    assert helper.keyboards[str(node)].listen_only == hh.READ_ONLY
    node.chmod(0o644)
    helper.scan()
    assert helper.keyboards[str(node)].listen_only is None


def test_an_input_folder_that_cant_be_listed_is_said_once(setup, tmp_path, capsys):
    """It returned silently: the hello said keyboards=0 and the journal
    nothing at all."""
    helper, fake, _connect, _say, _heard, _seat = setup
    helper.keyboards.pop(fake.path)
    helper.input_dir = str(tmp_path / "no-input")
    for _ in range(3):
        helper.scan()
    out = capsys.readouterr().out
    assert out.count(f"couldn't list {helper.input_dir} (ENOENT") == 1
    assert out.count("watching no keyboard at all") == 1


def test_right_ctrl_held_on_a_read_only_keyboard_too_refuses_the_hide(setup):
    """Right Ctrl down on a writable keyboard, then on a read-only one as
    well: hiding it on the writable one alone was answered OK while the
    read-only one still held Ctrl for the desktop."""
    helper, kb, connect, say, heard, _seat = setup
    other = _read_only_beside(helper, kb)
    sock = _ready(connect, say, heard, "97")
    kb.press(RCTRL)
    assert heard(sock) == ["DOWN 97 -"]
    other.press(RCTRL)
    assert heard(sock) == []                        # held already: no second DOWN
    say(sock, "HIDE 97")
    assert heard(sock) == ["ERR listen-only keyboard"]
    assert kb.calls == other.calls == [] and helper.hide is None


@needs_permissions
def test_a_read_only_keyboard_isnt_opened_again_while_its_hotkey_is_held(
        setup, monkeypatch, tmp_path):
    """Closing it lets every key on it go: a permission fix (udevadm
    trigger) while F8 was held there ended the recording mid-hold."""
    helper, fake, connect, say, heard, _seat = setup
    helper.keyboards.pop(fake.path)
    node = _device(tmp_path / "input", writable=False)
    monkeypatch.setattr(hh, "fcntl", FakeEvdev(_LETTERS | {F8}))
    helper.scan()
    sock = _ready(connect, say, heard, "66")
    held = helper.keyboards[str(node)]
    node.chmod(0o644)
    helper.on_key(held, F8, 1)
    assert heard(sock) == ["DOWN 66 -"]
    helper.scan()
    assert helper.keyboards[str(node)] is held and heard(sock) == []
    helper.on_key(held, F8, 0)
    assert heard(sock) == ["UP 66"]
    helper.scan()
    assert helper.keyboards[str(node)].listen_only is None
