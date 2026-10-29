"""The hotkey helper (hotkey_helper.py): it reads the keyboards so TuxTalk
needn't be in the "input" group, and tells only the user at the screen about
hotkeys, never about typing.

Keyboards here are pipes that events are written into, so no real device or
privilege is needed; the grab and inject calls are recorded instead.
"""
import os
import socket
import time

import pytest

from tuxtalk import hotkey_helper as hh

F8, F9, ESC, P, A = 66, 67, 1, 25, 30
LCTRL, LALT, LSHIFT = 29, 56, 42


class FakeKeyboard(hh.Keyboard):
    """A keyboard made of a pipe. The kernel's view of which keys are down is
    kept too: an injected event comes back only if it changes that, as with a
    real evdev device."""

    def __init__(self, path="/dev/input/event-fake"):
        r, w = os.pipe()
        os.set_blocking(r, False)
        super().__init__(path, r, "Fake keyboard", set(), False, f"own:{path}")
        self.w = w
        self.kernel = set()
        self.calls = []

    def _event(self, code, value):
        # Stamped like the kernel does after EVIOCSCLOCKID(CLOCK_MONOTONIC).
        sec, usec = divmod(int(time.monotonic() * 1e6), 1_000_000)
        os.write(self.w, hh.EVENT.pack(sec, usec, hh.EV_KEY, code, value))

    def press(self, code, value=1):
        """The user presses (1) or lets go of (0) a key."""
        if value == 1:
            self.kernel.add(code)
        elif value == 0:
            self.kernel.discard(code)
        self._event(code, value)

    def kernel_down(self):
        return set(self.kernel)

    def grab(self, on):
        self.calls.append(("grab", on))
        self.grabbed = on
        return True

    def inject(self, code, value):
        self.calls.append(("inject", code, value))
        if (value == 1) != (code in self.kernel):
            (self.kernel.add if value == 1 else self.kernel.discard)(code)
            self._event(code, value)
        return True


@pytest.fixture
def setup(tmp_path, monkeypatch):
    seat = tmp_path / "seat0"
    seat.write_text(f"ACTIVE=c1\nACTIVE_UID={os.getuid()}\n")
    (tmp_path / "input").mkdir()
    helper = hh.Helper(socket_dir=str(tmp_path), seat_file=str(seat),
                       input_dir=str(tmp_path / "input"))
    helper.listen()
    keyboard = FakeKeyboard()
    helper.keyboards[keyboard.path] = keyboard
    clients = []

    def connect():
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(helper.socket_path)
        sock.settimeout(0.05)
        clients.append(sock)
        spin()
        return sock

    def spin(times=4):
        for _ in range(times):
            helper.run_once(timeout=0.01)

    def say(sock, line):
        sock.sendall(line.encode() + b"\n")
        spin()

    def heard(sock):
        spin()
        data = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except (socket.timeout, BlockingIOError):
                break
            if not chunk:
                break
            data += chunk
        return data.decode().splitlines()

    yield helper, keyboard, connect, say, heard, seat
    for sock in clients:
        sock.close()
    helper.listener.close()


def _ready(connect, say, heard, specs="66 67 1 CA:25"):
    sock = connect()
    say(sock, "HELLO 1")
    say(sock, f"WATCH {specs}")
    assert heard(sock) == [f"HELLO 1 {hh.VERSION}", "OK WATCH"]
    return sock


# --- what may be watched --------------------------------------------------------------

@pytest.mark.parametrize("spec", ["66", "88", "183", "194", "1", "119", "70", "97", "100",
                                  "CA:25", "CA:30", "CM:30", "AM:30", "CAS:30", "S:66", "C:66"])
def test_hotkeys_and_modifier_combos_can_be_watched(spec):
    hh.parse_spec(spec)


@pytest.mark.parametrize("spec", [
    "30",       # a letter on its own: that's typing
    "2",        # a number
    "28",       # Enter
    "14",       # Backspace
    "57",       # Space
    "S:30",     # Shift+letter is a capital letter
    "C:30",     # one modifier can be made to type (XKB level-3 options)
    "A:30",
    "M:20",
    "CS:30",    # Shift doesn't count as the second one
    "AS:30",
    "42",       # a modifier on its own: Shift types capitals
    "29",       # Left Ctrl and Left Alt: only Right Ctrl and Right Alt are dictation keys
    "56",
    "125",
    "C:29",     # a modifier as the key
    "X:30",     # not a modifier letter
    "CC:30",    # a repeated one
    "0", "999", "nope", "C:",
])
def test_anything_that_reveals_typing_is_refused(spec):
    with pytest.raises(ValueError):
        hh.parse_spec(spec)


def test_say_hello_first_and_refusals_are_named(setup):
    _helper, _kb, connect, say, heard, _seat = setup
    sock = connect()
    say(sock, "WATCH 66")
    say(sock, "HELLO 2")
    say(sock, "HELLO 1")
    say(sock, "WATCH 66 30")
    assert heard(sock) == ["ERR say HELLO first", "ERR this helper speaks protocol 1",
                           f"HELLO 1 {hh.VERSION}",
                           "ERR key 30 is only reported with two of Ctrl, Alt and Super"]


# --- what is reported ---------------------------------------------------------------------

def test_a_watched_key_is_reported_down_and_up(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(F8)
    kb.press(F8, 2)          # auto-repeat: never reported
    kb.press(F8, 0)
    assert heard(sock) == ["DOWN 66 -", "UP 66"]


def test_typing_is_never_reported(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    for code in (A, 31, 32, 28, 14, LSHIFT, A, LSHIFT):
        kb.press(code)
        kb.press(code, 0)
    assert heard(sock) == []


def test_modifiers_go_with_the_key_and_their_release_follows(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(LSHIFT)
    kb.press(F8)
    kb.press(F8, 0)
    kb.press(LSHIFT, 0)
    assert heard(sock) == ["DOWN 66 42", "UP 66", "UP 42"]


def test_a_combo_needs_its_modifiers(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(P)                # P alone: typing
    kb.press(P, 0)
    kb.press(LCTRL)
    kb.press(P)                # Ctrl+P: not the combo
    kb.press(P, 0)
    kb.press(LALT)
    kb.press(P)                # Ctrl+Alt+P
    kb.press(P, 0)
    kb.press(LALT, 0)
    kb.press(LCTRL, 0)
    assert heard(sock) == ["DOWN 25 29,56", "UP 25", "UP 56", "UP 29"]


def test_only_the_user_at_the_screen_hears(setup):
    _helper, kb, connect, say, heard, seat = setup
    sock = _ready(connect, say, heard)
    seat.write_text(f"ACTIVE_UID={os.getuid() + 1}\n")     # someone else's session in front
    kb.press(F8)
    kb.press(F8, 0)
    seat.write_text("ACTIVE=c1\n")                          # no active user at all
    kb.press(F9)
    kb.press(F9, 0)
    assert heard(sock) == []


def test_a_press_comes_up_when_its_own_keyboard_lets_go(setup):
    """Holding the same key on another keyboard (a program's virtual one,
    say) can't keep a recording going."""
    helper, kb, connect, say, heard, _seat = setup
    other = FakeKeyboard("/dev/input/event-other")
    helper.keyboards[other.path] = other
    sock = _ready(connect, say, heard)
    kb.press(F8)
    assert heard(sock) == ["DOWN 66 -"]
    other.press(F8)
    assert heard(sock) == []           # already down: no second DOWN
    kb.press(F8, 0)
    assert heard(sock) == ["UP 66"]    # the press's own keyboard let go
    other.press(F8, 0)
    assert heard(sock) == []


def test_an_unplugged_keyboard_lets_its_keys_go(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(F8)
    assert heard(sock) == ["DOWN 66 -"]
    helper.drop_keyboard(kb)
    assert heard(sock) == ["UP 66"]


# --- hiding a held hotkey ---------------------------------------------------------------

def test_hide_tells_the_desktop_it_went_up_then_keeps_the_keyboards(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(F8)
    heard(sock)
    say(sock, "HIDE 66")
    assert heard(sock) == ["OK HIDE"]
    assert kb.calls == [("inject", F8, 0), ("grab", True), ("inject", F8, 1)]
    # Our own injected events don't count as the user's.
    assert helper.hide is not None and kb.down == {F8}
    kb.press(F8, 0)                     # the real release still reaches us
    say(sock, "UNHIDE")
    assert heard(sock) == ["UP 66", "OK UNHIDE"]
    assert kb.calls[-1] == ("grab", False) and helper.hide is None


def test_hide_is_refused_unless_the_hotkey_is_held(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    say(sock, "HIDE 66")
    kb.press(A)                        # a held letter isn't the client's to hide
    say(sock, "HIDE 30")
    assert heard(sock) == ["ERR the hotkey isn't held", "ERR the hotkey isn't held"]
    assert helper.hide is None and kb.calls == []


def test_a_release_waiting_to_be_read_beats_the_hide(setup):
    """Let go of F8 just as the hide is asked for: the release counts, nothing
    is grabbed, and the client hears UP before the refusal."""
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(F8)
    heard(sock)
    kb.press(F8, 0)                    # not read yet
    sock.sendall(b"HIDE 66\n")
    helper.on_client_data(next(iter(helper.clients.values())))
    assert heard(sock) == ["UP 66", "ERR the hotkey isn't held"]
    assert ("grab", True) not in kb.calls


def test_the_keyboards_come_back_by_themselves_on_letting_go(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(F8)
    heard(sock)
    say(sock, "HIDE 66")
    kb.press(F8, 0)                    # and TuxTalk never says UNHIDE
    heard(sock)
    assert helper.hide is None and kb.calls[-1] == ("grab", False)


def test_the_keyboards_come_back_when_tuxtalk_goes_away(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(F8)
    heard(sock)
    say(sock, "HIDE 66")
    sock.close()
    heard(connect())
    assert helper.hide is None and kb.calls[-1] == ("grab", False)


def test_a_keyboard_kept_too_long_comes_back_though_the_key_is_still_down(setup, monkeypatch):
    """The last safety net: whatever went wrong (a key the kernel never
    reported up, a TuxTalk that never said UNHIDE), the keyboard isn't kept
    past HIDE_MAX_S."""
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(F8)
    heard(sock)
    say(sock, "HIDE 66")
    assert heard(sock) == ["OK HIDE"]
    helper.run_once(0)
    assert helper.hide is not None                  # held, and not for long yet
    monkeypatch.setattr(hh, "HIDE_MAX_S", 0)
    time.sleep(0.01)
    helper.run_once(0)
    assert helper.hide is None and kb.calls[-1] == ("grab", False)
    assert kb.down == {F8}


def test_someone_else_cant_hide(setup):
    helper, kb, connect, say, heard, seat = setup
    sock = _ready(connect, say, heard)
    kb.press(F8)
    heard(sock)
    seat.write_text(f"ACTIVE_UID={os.getuid() + 1}\n")
    say(sock, "HIDE 66")
    assert heard(sock) == ["ERR only the user at the screen can do that"]


# --- limits ---------------------------------------------------------------------------------

def test_a_line_too_long_ends_the_connection(setup):
    helper, _kb, connect, say, _heard, _seat = setup
    sock = connect()
    sock.sendall(b"X" * (hh.MAX_LINE + 10))
    for _ in range(4):
        helper.run_once(timeout=0.01)
    assert helper.clients == {}


def test_connections_never_pile_up_past_the_limit(setup):
    helper, _kb, connect, _say, _heard, _seat = setup
    for _ in range(hh.MAX_CLIENTS + 3):
        connect()
    assert len(helper.clients) == hh.MAX_CLIENTS


def test_the_active_user_is_read_from_logind(tmp_path):
    seat = tmp_path / "seat0"
    seat.write_text("# This is private data. Do not parse.\nACTIVE=c2\nACTIVE_UID=1000\nUIDS=1000\n")
    assert hh.read_active_uid(str(seat)) == 1000
    assert hh.read_active_uid(str(tmp_path / "missing")) is None


def test_the_helper_needs_nothing_but_the_standard_library():
    """It runs with the system's python3 in isolated mode, outside TuxTalk."""
    import ast
    tree = ast.parse(open(hh.__file__).read())
    imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert imported <= {"errno", "fcntl", "os", "select", "signal", "socket", "struct", "sys", "time"}


# --- from the security review, 2026-10-15 -------------------------------------------------

RALT, LMETA = 100, 125


def test_a_virtual_keyboard_cant_lend_its_modifiers_to_the_real_one(setup):
    """A program makes a virtual keyboard and holds Ctrl+Alt there: keys typed
    on the real keyboard must not turn into reportable combos."""
    helper, kb, connect, say, heard, _seat = setup
    virtual = FakeKeyboard("/dev/input/event-virtual")
    helper.keyboards[virtual.path] = virtual
    sock = _ready(connect, say, heard, "CA:25 CA:30 CA:31")
    virtual.press(LCTRL)
    virtual.press(LALT)
    heard(sock)
    for code in (A, 31, P):
        kb.press(code)
        kb.press(code, 0)
    assert heard(sock) == []


def test_modifiers_down_before_the_keyboard_was_opened_dont_count(setup):
    helper, _kb, connect, say, heard, _seat = setup
    late = FakeKeyboard("/dev/input/event-late")
    late.down = {LCTRL, LALT}             # already held when it appeared
    helper.keyboards[late.path] = late
    sock = _ready(connect, say, heard, "CA:25")
    late.press(P)
    late.press(P, 0)
    assert heard(sock) == []
    late.press(LCTRL, 0)
    late.press(LALT, 0)
    late.press(LCTRL)
    late.press(LALT)
    late.press(P)
    assert heard(sock) == ["DOWN 25 29,56"]


def test_altgr_never_unlocks_a_key(setup):
    """Right Alt is AltGr on most layouts: Ctrl+AltGr+key can type @ or €."""
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard, "CA:25 CA:16")
    kb.press(LCTRL)
    kb.press(RALT)
    kb.press(16)                         # Q: AltGr+Q is @ on a German layout
    kb.press(16, 0)
    assert heard(sock) == []


def test_no_combos_while_a_keyboard_is_held_and_hidden_modifiers_stop_counting(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard, "66 CA:25")
    kb.press(LCTRL)
    kb.press(LALT)
    kb.press(F8)                          # Ctrl+Alt+F8 held, then hidden
    heard(sock)
    say(sock, "HIDE 66 29 56")
    assert heard(sock) == ["OK HIDE"]
    kb.press(P)                           # the desktop doesn't see this
    kb.press(P, 0)
    kb.press(F8, 0)
    say(sock, "UNHIDE")
    heard(sock)
    kb.press(P)                           # Ctrl+Alt still physically held, but the
    kb.press(P, 0)                        # desktop was told they went up
    assert "DOWN 25" not in " ".join(heard(sock))


def test_modifiers_pressed_while_held_dont_count(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard, "66 CA:25")
    kb.press(F8)
    heard(sock)
    say(sock, "HIDE 66")
    kb.press(LCTRL)                       # pressed while we hold the keyboard
    kb.press(LALT)
    kb.press(F8, 0)
    say(sock, "UNHIDE")
    heard(sock)
    kb.press(P)
    assert heard(sock) == []


def test_one_hide_per_press(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(F8)
    heard(sock)
    say(sock, "HIDE 66")
    say(sock, "UNHIDE")
    say(sock, "HIDE 66")                  # same press, still held
    assert heard(sock) == ["OK HIDE", "OK UNHIDE", "ERR the hotkey isn't held"]
    kb.press(F8, 0)
    kb.press(F8)                          # a new press may
    heard(sock)
    say(sock, "HIDE 66")
    assert heard(sock) == ["OK HIDE"]


def test_the_keyboard_comes_back_when_someone_else_is_at_the_screen(setup):
    helper, kb, connect, say, heard, seat = setup
    sock = _ready(connect, say, heard)
    kb.press(F8)
    heard(sock)
    say(sock, "HIDE 66")
    seat.write_text(f"ACTIVE_UID={os.getuid() + 1}\n")
    heard(sock)
    assert helper.hide is None and kb.calls[-1] == ("grab", False)


def test_only_the_keyboard_the_hotkey_is_held_on_is_grabbed(setup):
    helper, kb, connect, say, heard, _seat = setup
    other = FakeKeyboard("/dev/input/event-other")
    helper.keyboards[other.path] = other
    sock = _ready(connect, say, heard)
    kb.press(F8)
    heard(sock)
    say(sock, "HIDE 66")
    assert ("grab", True) in kb.calls and other.calls == []


def test_someone_not_at_the_screen_cant_connect(setup):
    helper, _kb, connect, _say, _heard, seat = setup
    seat.write_text(f"ACTIVE_UID={os.getuid() + 1}\n")
    sock = connect()
    assert helper.clients == {}
    assert sock.recv(10) == b""           # closed


def test_a_connection_that_never_says_hello_is_dropped(setup, monkeypatch):
    helper, _kb, connect, _say, _heard, _seat = setup
    monkeypatch.setattr(hh, "HELLO_DEADLINE_S", 0.05)
    connect()
    assert len(helper.clients) == 1
    time.sleep(0.1)
    helper.run_once(timeout=0.01)
    assert helper.clients == {}


def test_a_full_house_makes_room_for_the_user_at_the_screen(setup, monkeypatch):
    helper, _kb, connect, say, heard, _seat = setup
    monkeypatch.setattr(hh, "MAX_CLIENTS", 2)
    _ready(connect, say, heard)
    _ready(connect, say, heard)
    next(c for c in helper.clients.values() if c.sock.fileno() >= 0).uid = os.getuid() + 1
    third = connect()
    say(third, "HELLO 1")
    assert heard(third) == [f"HELLO 1 {hh.VERSION}"]
    assert sorted(c.uid for c in helper.clients.values()) == [os.getuid()] * 2


def test_lost_events_are_made_up_from_the_kernel(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(F8)
    assert heard(sock) == ["DOWN 66 -"]
    kb.kernel.discard(F8)                 # released while events were being lost
    os.write(kb.w, hh.EVENT.pack(0, 0, hh.EV_SYN, hh.SYN_DROPPED, 0)
             + hh.EVENT.pack(0, 0, hh.EV_KEY, F8, 1)        # stale, skipped
             + hh.EVENT.pack(0, 0, hh.EV_SYN, hh.SYN_REPORT, 0))
    assert heard(sock) == ["UP 66"]


def test_virtual_keyboards_cant_crowd_out_real_ones(setup, monkeypatch, tmp_path):
    helper, _kb, _connect, _say, _heard, _seat = setup
    folder = tmp_path / "input"
    for i in range(hh.MAX_VIRTUAL_KEYBOARDS + 4):
        (folder / f"event{i}").write_text("")

    def fake_open(path):
        keyboard = FakeKeyboard(path)
        keyboard.virtual = True
        return keyboard
    monkeypatch.setattr(hh.Keyboard, "open", staticmethod(fake_open))
    helper.scan()
    assert sum(1 for k in helper.keyboards.values() if k.virtual) == hh.MAX_VIRTUAL_KEYBOARDS


def test_other_seats_keyboards_are_not_read(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    rdev = os.makedev(13, 70)
    (data / "c13:70").write_text("E:ID_INPUT=1\nE:ID_SEAT=seat1\n")
    assert hh.udev_seat(rdev, str(data)) == "seat1"
    assert hh.udev_seat(os.makedev(13, 71), str(data)) == "seat0"


# --- from the second security review, 2026-10-15 -------------------------------------------

def test_a_real_keyboard_split_over_two_devices_still_makes_combos(setup):
    """Some keyboards send Ctrl and Alt from one device and letters from
    another. Real keyboards pool their modifiers, as the desktop does."""
    helper, kb, connect, say, heard, _seat = setup
    kb.group = "real"
    letters = FakeKeyboard("/dev/input/event-letters")
    letters.group = "real"
    helper.keyboards[letters.path] = letters
    sock = _ready(connect, say, heard, "CA:25")
    kb.press(LCTRL)
    kb.press(LALT)
    letters.press(P)
    assert heard(sock) == ["DOWN 25 29,56"]


def test_a_virtual_keyboard_still_cant_lend_to_real_ones(setup):
    helper, kb, connect, say, heard, _seat = setup
    kb.group = "real"
    virtual = FakeKeyboard("/dev/input/event-virtual")      # a group of its own
    helper.keyboards[virtual.path] = virtual
    sock = _ready(connect, say, heard, "CA:25")
    virtual.press(LCTRL)
    virtual.press(LALT)
    kb.press(P)
    assert heard(sock) == []


def test_holding_the_hotkey_on_a_virtual_keyboard_cant_pin_the_grab(setup, monkeypatch):
    """A program holds F8 on its own virtual keyboard while the user holds
    the real one, then asks for the grab: the real keyboard comes back as
    soon as the user lets go, and TuxTalk hears the release."""
    helper, kb, connect, say, heard, _seat = setup
    virtual = FakeKeyboard("/dev/input/event-virtual")
    helper.keyboards[virtual.path] = virtual
    tuxtalk = _ready(connect, say, heard)
    hostile = _ready(connect, say, heard)
    kb.press(F8)                                # the user, on the real keyboard
    heard(tuxtalk)
    heard(hostile)
    virtual.press(F8)
    say(hostile, "HIDE 66")
    assert heard(hostile) == ["OK HIDE"]
    assert virtual.calls == []                  # only the press's own keyboard
    kb.press(F8, 0)
    assert heard(tuxtalk) == ["UP 66"]
    assert helper.hide is None and kb.calls[-1] == ("grab", False)


def test_a_program_holding_every_place_cant_keep_tuxtalk_out(setup):
    helper, _kb, connect, say, heard, _seat = setup
    for _ in range(hh.MAX_CLIENTS):
        _ready(connect, say, heard)             # all of them said hello
    tuxtalk = connect()
    say(tuxtalk, "HELLO 1")
    assert heard(tuxtalk) == [f"HELLO 1 {hh.VERSION}"]
    assert len(helper.clients) == hh.MAX_CLIENTS


def test_only_real_devices_share_modifiers(tmp_path):
    chars = tmp_path / "char"
    chars.mkdir()
    (chars / "13:70").symlink_to("/sys/devices/pci0000:00/0000:11:00.0/usb1/1-3/1-3:1.2/input/input14/event3")
    (chars / "13:71").symlink_to("/sys/devices/virtual/input/input36/event26")
    assert hh.device_group(os.makedev(13, 70), str(chars)) == "real"
    assert hh.device_group(os.makedev(13, 71), str(chars)).startswith("virtual:")
    # Can't tell (no /sys): the safe side.
    assert hh.device_group(os.makedev(13, 72), str(chars)).startswith("virtual:")


# --- fixes from the Codex review (helper 1.3) ----------------------------------------

def test_a_virtual_keyboard_holding_the_hotkey_cant_swallow_the_real_one(setup):
    """A program holds F8 on its own virtual keyboard first: the real F8 is
    still reported, so it can't switch dictation (or Esc) off."""
    helper, kb, connect, say, heard, _seat = setup
    virtual = FakeKeyboard("/dev/input/event-virtual")
    virtual.virtual = True
    helper.keyboards[virtual.path] = virtual
    tuxtalk = _ready(connect, say, heard)
    virtual.press(F8)
    assert heard(tuxtalk) == ["DOWN 66 -"]
    kb.press(F8)
    assert heard(tuxtalk) == []                 # taken over, no second DOWN
    kb.press(F8, 0)
    assert heard(tuxtalk) == ["UP 66"]          # the real release ends it
    kb.press(F8)
    assert heard(tuxtalk) == ["DOWN 66 -"]      # and the next real press counts
    kb.press(F8, 0)
    assert heard(tuxtalk) == ["UP 66"]
    virtual.press(F8, 0)
    assert heard(tuxtalk) == []


def test_a_virtual_keyboard_cant_take_over_a_real_press(setup):
    helper, kb, connect, say, heard, _seat = setup
    virtual = FakeKeyboard("/dev/input/event-virtual")
    virtual.virtual = True
    helper.keyboards[virtual.path] = virtual
    tuxtalk = _ready(connect, say, heard)
    kb.press(F8)
    assert heard(tuxtalk) == ["DOWN 66 -"]
    virtual.press(F8)
    assert heard(tuxtalk) == []
    virtual.press(F8, 0)
    assert heard(tuxtalk) == []
    kb.press(F8, 0)
    assert heard(tuxtalk) == ["UP 66"]


def test_two_presses_in_one_group_are_reported_once(setup):
    helper, kb, connect, say, heard, _seat = setup
    kb.group = "real"
    second = FakeKeyboard("/dev/input/event-second")
    second.group = "real"
    helper.keyboards[second.path] = second
    tuxtalk = _ready(connect, say, heard)
    kb.press(F8)
    second.press(F8)
    assert heard(tuxtalk) == ["DOWN 66 -"]


def test_a_keyboard_that_cant_be_held_is_refused_and_left_alone(setup, monkeypatch):
    """The grab failed: the keys aren't marked down again (the desktop would
    repeat them) and the helper doesn't claim it holds the keyboard."""
    helper, kb, connect, say, heard, _seat = setup
    tuxtalk = _ready(connect, say, heard)
    kb.press(F8)
    heard(tuxtalk)

    def grab_fails(on):
        kb.calls.append(("grab", on))
        return False

    monkeypatch.setattr(kb, "grab", grab_fails)
    say(tuxtalk, "HIDE 66")
    assert heard(tuxtalk) == ["ERR couldn't hold the keyboard"]
    assert ("inject", F8, 1) not in kb.calls
    assert helper.hide is None


def test_a_keyboard_that_cant_be_let_go_is_closed(setup, monkeypatch):
    """Letting go failed: closing the device is the kernel's own way to end a
    grab, so the keyboard can never stay locked."""
    helper, kb, connect, say, heard, _seat = setup
    tuxtalk = _ready(connect, say, heard)
    kb.press(F8)
    heard(tuxtalk)
    say(tuxtalk, "HIDE 66")
    assert heard(tuxtalk) == ["OK HIDE"]
    closed = []
    monkeypatch.setattr(kb, "grab", lambda on: False)
    monkeypatch.setattr(kb, "close", lambda: closed.append(True))
    helper.end_hide("test")
    assert closed == [True]
    assert kb.path not in helper.keyboards and helper.hide is None
    assert heard(tuxtalk) == ["UP 66"]          # its key can't come up any more


# --- fixes from guelz's review (helper 1.4) -------------------------------------------

def test_a_client_gets_four_shortcuts_at_most(setup):
    _helper, _kb, connect, say, heard, _seat = setup
    sock = connect()
    say(sock, "HELLO 1")
    say(sock, "WATCH 66 CA:25 CA:30 CA:31 CA:32 CA:33")
    assert heard(sock) == [f"HELLO 1 {hh.VERSION}", "ERR no more than 4 shortcuts"]
    say(sock, "WATCH 66 67 1 CA:25 CA:30 CA:31 CA:32")
    assert heard(sock) == ["OK WATCH"]


def test_ctrl_alt_held_down_doesnt_turn_typing_into_shortcuts(setup):
    """Two different shortcut keys per hold of Ctrl+Alt, then nothing: a
    stuck Ctrl+Alt (or one meaning AltGr in a Windows VM) can't report what
    is typed. Letting go starts a new hold."""
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard, specs="66 CA:25 CA:30 CA:31")
    kb.press(LCTRL)
    kb.press(LALT)
    for code in (P, P, A, 31):
        kb.press(code)
        kb.press(code, 0)
    assert heard(sock) == ["DOWN 25 29,56", "UP 25", "DOWN 25 29,56", "UP 25",
                           "DOWN 30 29,56", "UP 30"]
    kb.press(LALT, 0)
    kb.press(LALT)
    kb.press(31)
    kb.press(31, 0)
    assert heard(sock) == ["UP 56", "DOWN 31 29,56", "UP 31"]


def test_esc_is_never_hidden(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    kb.press(ESC)
    heard(sock)
    say(sock, "HIDE 1")
    assert heard(sock) == ["ERR the hotkey isn't held"]
    assert helper.hide is None and kb.calls == []


def test_a_release_just_before_hiding_isnt_swallowed(setup):
    """The key comes up between reading the kernel's state and our "it went
    up": the kernel drops ours, and the real release must still count, or
    the keyboard stays held with the key long up."""
    helper, kb, connect, say, heard, _seat = setup
    kb.monotonic = True
    sock = _ready(connect, say, heard)
    kb.press(F8)
    assert heard(sock) == ["DOWN 66 -"]
    real_kernel_down = kb.kernel_down

    def releasing_kernel_down():
        down = real_kernel_down()
        kb.kernel_down = real_kernel_down
        kb.press(F8, 0)               # the user lets go right now
        return down
    kb.kernel_down = releasing_kernel_down
    say(sock, "HIDE 66")
    assert heard(sock) == ["UP 66", "OK HIDE"]
    assert helper.hide is None and helper.injected == []
    assert F8 not in kb.kernel and kb.calls[-1] == ("grab", False)
    kb.press(F8)                      # and the next press is a press
    assert heard(sock) == ["DOWN 66 -"]


def test_a_keyboard_programs_can_write_to_is_kept_apart(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    rdev = os.makedev(13, 67)
    (data / "c13:67").write_text("E:ID_INPUT_KEYBOARD=1\nG:uaccess\nQ:uaccess\n")
    node = os.stat_result((0o20660, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    tagged = os.stat_result((0o20660, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    plain = os.makedev(13, 68)

    def with_rdev(st, rdev):
        class St:
            st_mode, st_uid, st_gid = st.st_mode, st.st_uid, st.st_gid
        St.st_rdev = rdev
        return St
    assert hh.others_can_write(with_rdev(tagged, rdev), str(data))
    assert not hh.others_can_write(with_rdev(node, plain), str(data))
    everyone = os.stat_result((0o20666, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    assert hh.others_can_write(with_rdev(everyone, plain), str(data))

    chars = tmp_path / "char"
    chars.mkdir()
    (chars / "13:67").symlink_to("/sys/devices/pci0000:00/0000:11:00.0/usb1/1-3/input/input14/event3")
    assert hh.device_group(rdev, str(chars)) == "real"
    assert hh.device_group(rdev, str(chars), writable=True) == "own:13:67"


def test_bluetooth_keyboards_are_real_while_uhid_is_roots_alone(tmp_path):
    chars = tmp_path / "char"
    chars.mkdir()
    (chars / "13:69").symlink_to(
        "/sys/devices/virtual/misc/uhid/0005:046D:B342.0009/input/input30/event9")
    rdev = os.makedev(13, 69)
    assert hh.device_group(rdev, str(chars), uhid_private=True) == "real"
    assert hh.device_group(rdev, str(chars), uhid_private=False) == "virtual:13:69"


def test_uhid_handed_out_by_a_rule_isnt_private(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    st = {"mode": 0o20600}

    class FakeStat:
        st_uid, st_gid, st_rdev = 0, 0, os.makedev(10, 239)

    def fake_stat(path):
        fake = FakeStat()
        fake.st_mode = st["mode"]
        return fake
    monkeypatch.setattr(hh.os, "stat", fake_stat)
    assert hh.uhid_is_private("/dev/uhid", str(data))
    st["mode"] = 0o20660                         # GROUP="input", MODE="0660", uaccess
    assert not hh.uhid_is_private("/dev/uhid", str(data))


def test_everyone_waiting_is_let_in_at_once(setup):
    helper, _kb, _connect, _say, _heard, _seat = setup
    socks = []
    for _ in range(5):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(helper.socket_path)
        socks.append(sock)
    helper.run_once(timeout=0.01)
    assert len(helper.clients) == 5
    for sock in socks:
        sock.close()


def test_a_full_house_pushes_out_the_newest_not_tuxtalk(setup):
    helper, _kb, connect, say, heard, _seat = setup
    tuxtalk = _ready(connect, say, heard)
    for _ in range(hh.MAX_CLIENTS - 1):
        _ready(connect, say, heard)
    for _ in range(3):
        _ready(connect, say, heard)              # a program connecting again and again
    say(tuxtalk, "WATCH 66")
    assert heard(tuxtalk) == ["OK WATCH"]


def test_a_keyboard_turned_away_as_one_too_many_gets_in_later(setup, monkeypatch, tmp_path):
    helper, _kb, _connect, _say, _heard, _seat = setup
    folder = tmp_path / "input"
    for i in range(hh.MAX_VIRTUAL_KEYBOARDS + 1):
        (folder / f"event{i}").write_text("")

    def fake_open(path):
        keyboard = FakeKeyboard(path)
        keyboard.virtual = True
        return keyboard
    monkeypatch.setattr(hh.Keyboard, "open", staticmethod(fake_open))
    helper.scan()
    waiting = str(folder / f"event{hh.MAX_VIRTUAL_KEYBOARDS}")
    assert waiting not in helper.keyboards
    helper.drop_keyboard(helper.keyboards[str(folder / "event0")])
    (folder / "event0").unlink()
    helper.scan()
    assert waiting in helper.keyboards


# --- Right Ctrl and Right Alt as dictation keys ---------------------------------------

RCTRL = 97


def test_right_ctrl_on_its_own_is_reported_down_and_up(setup):
    """For keyboards whose F-keys need Fn: Right Ctrl alone is a hotkey."""
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard, "97 100")
    kb.press(RCTRL)
    kb.press(RCTRL, 0)
    kb.press(LSHIFT)
    kb.press(RALT)                       # with Shift: a voice edit
    kb.press(RALT, 0)
    kb.press(LSHIFT, 0)
    assert heard(sock) == ["DOWN 97 -", "UP 97", "DOWN 100 42", "UP 100", "UP 42"]


def test_left_ctrl_and_alt_stay_unreported_beside_a_right_ctrl_hotkey(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard, "97")
    for code in (LCTRL, LALT, LSHIFT):
        kb.press(code)
        kb.press(code, 0)
    assert heard(sock) == []


def test_a_right_ctrl_hotkey_still_makes_shortcuts_with_left_alt(setup):
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard, "97 CA:25")
    kb.press(RCTRL)
    kb.press(LALT)
    kb.press(P)
    assert heard(sock) == ["DOWN 97 -", "DOWN 25 56,97"]


def test_a_held_right_ctrl_hotkey_is_hidden_like_f8(setup):
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard, "97")
    kb.press(RCTRL)
    heard(sock)
    say(sock, "HIDE 97")
    assert heard(sock) == ["OK HIDE"]
    assert kb.calls == [("inject", RCTRL, 0), ("grab", True), ("inject", RCTRL, 1)]
    kb.press(A)                          # Ctrl+A doesn't reach the desktop
    kb.press(A, 0)
    kb.press(RCTRL, 0)
    say(sock, "UNHIDE")
    assert heard(sock) == ["UP 97", "OK UNHIDE"]
    assert helper.hide is None


def test_a_right_ctrl_held_with_another_hotkey_comes_up_once(setup):
    """Right Ctrl is the reported key and F9's modifier at once: one UP."""
    _helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard, "97 67")
    kb.press(RCTRL)
    kb.press(F9)
    kb.press(F9, 0)
    kb.press(RCTRL, 0)
    say(sock, "WATCH 66")                # back to F8
    kb.press(RCTRL)
    kb.press(RCTRL, 0)
    assert heard(sock) == ["DOWN 97 -", "DOWN 67 97", "UP 67", "UP 97", "OK WATCH"]


def _release_fails(monkeypatch, keyboard, code):
    """Injecting *code*'s release into *keyboard* fails, as a write to a
    device on its way out does."""
    inject = keyboard.inject

    def failing(c, value):
        if (c, value) == (code, 0):
            keyboard.calls.append(("inject failed", c, value))
            return False
        return inject(c, value)

    monkeypatch.setattr(keyboard, "inject", failing)


def test_a_right_ctrl_the_desktop_still_sees_down_isnt_called_hidden(setup, monkeypatch):
    """Its release couldn't be injected: answering OK would let TuxTalk type
    while the desktop holds Ctrl (a dictated "w" closing the tab)."""
    helper, kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard, "97")
    kb.press(RCTRL)
    heard(sock)
    _release_fails(monkeypatch, kb, RCTRL)
    say(sock, "HIDE 97")
    assert heard(sock) == ["ERR couldn't hide the keys"]
    assert helper.hide is None and not kb.grabbed
    assert ("grab", True) not in kb.calls


def test_a_failed_release_on_a_second_keyboard_gives_the_first_back(setup, monkeypatch):
    """F8 was hidden and held on one keyboard, then the Right Ctrl held with it
    on another couldn't be let go: the first keyboard isn't kept."""
    helper, kb, connect, say, heard, _seat = setup
    second = FakeKeyboard("/dev/input/event-second")
    kb.group = second.group = "real"
    helper.keyboards[second.path] = second
    sock = _ready(connect, say, heard, "C:66")
    second.press(RCTRL)
    assert heard(sock) == []
    kb.press(F8)
    assert heard(sock) == ["DOWN 66 97"]
    _release_fails(monkeypatch, second, RCTRL)
    say(sock, "HIDE 66 97")
    assert heard(sock) == ["ERR couldn't hide the keys"]
    assert ("grab", True) in kb.calls and not kb.grabbed
    assert helper.hide is None


# --- helper 1.7: the code review's small findings ---------------------------------------

def test_a_line_longer_than_max_line_is_refused_even_with_its_newline(setup):
    """Only a line still unterminated was measured: a 4 KB line that ended
    in a newline was parsed."""
    helper, _kb, connect, say, heard, _seat = setup
    sock = _ready(connect, say, heard)
    sock.sendall(b"WATCH " + b"66 " * 300 + b"\n")
    assert heard(sock) == []
    assert helper.clients == {}


def test_a_keyboard_that_never_runs_dry_doesnt_hold_the_loop(setup, monkeypatch):
    """A program flooding its own virtual keyboard: settle's drain stops
    after DRAIN_READS reads instead of reading for as long as it types."""
    helper, kb, _connect, _say, _heard, _seat = setup
    reads = []
    monkeypatch.setattr(helper, "read_keyboard", lambda keyboard: reads.append(1) or 1)
    helper.drain(kb)
    assert len(reads) == hh.DRAIN_READS
