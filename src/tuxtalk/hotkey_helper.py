"""TuxTalk's hotkey helper: reads the keyboards so nothing else has to.

Why this exists
---------------
TuxTalk starts dictating when you hold F8. On Wayland no ordinary program is
allowed to watch keys pressed in other windows, so TuxTalk read the keyboards
directly from /dev/input, which meant putting the user in the "input" group.
That group lets EVERY program the user runs read every key, passwords
included: a keylogger permission, given to everything.

This helper is the only thing that reads the keyboards. It runs as a system
service under a throwaway user that can do nothing else (see the unit in
secure_input.py: no network, no home folder, read-only system, no way to gain
privileges), and it only ever tells anyone about hotkeys.

The rule it keeps
-----------------
A key is reported only when the desktop itself sees it as a shortcut, never
as typing:

* F1-F24, Esc, Pause and Scroll Lock, which never type anything;
* Right Ctrl and Right Alt on their own, for keyboards whose F-keys need Fn.
  Where a layout or an XKB option makes one of them AltGr or Compose, a
  client learns when it went down, so that a character of that kind (€, @)
  was typed then, but never which: the keys pressed with it stay unreported;
* any other key only while two of Ctrl, Alt and Super are held with it,
  having gone down while the desktop could see them, on a real keyboard
  (any of them, as the desktop counts them) or on the same virtual one;
  and no more than two different such keys while those modifiers stay down;
* Space while Left Ctrl is held, Left Ctrl counted as those modifiers are
  (not Right Ctrl, never Space alone, no other key with Left Ctrl), and
  Left Ctrl's own press and release, only to a client watching that pair.

"Two of" because any one modifier can be made to type characters: Right Alt
is AltGr on most layouts (it never counts toward the two), and XKB options turn
Left Alt, Super or Right Ctrl into a third-level key too. "Seen by the
desktop" and "the same virtual one" because a program can make a virtual
keyboard: a Ctrl held there, or one the desktop didn't see go down, would
otherwise make every key typed on the real keyboard look like a shortcut.
Real keyboards (USB, built in) can't be made by a program, and the desktop
pools their modifiers, so their Ctrl and their letters may come from
different devices, as they do on some keyboards. A real keyboard that the
user's programs may write to (a udev rule gave it "uaccess", as some RGB and
game-controller packages do) is kept apart like a virtual one: a program
could press its Ctrl. That is looked at again before the real keyboards'
modifiers count together, since a rule can give it "uaccess" later, and a
keyboard udev hasn't finished with yet counts as writable meanwhile. "Two
different keys" because a Ctrl+Alt left held (by a stuck key, or in a
Windows virtual machine, where Ctrl+Alt is AltGr) would otherwise turn
everything typed into shortcuts; a shortcut is one or two keys, typing is
many.

Left Ctrl is the one modifier that unlocks a key alone, and only Space. XKB
never makes Left Ctrl a third-level key, but its options can make it Compose,
a layout switch, Caps Lock, or Alt, Super or Meta: then Left Ctrl+Space may
type a space, which a client then learns, and Left Ctrl's own press tells
when a Compose sequence, a layout switch or Caps Lock began, as Right Ctrl's
does under such options. Even so, only Space, Left Ctrl's own press and
release, and the release of a modifier held at that press are ever
reported, never a letter, a number or Enter. A client that watches the pair
needs Left Ctrl's press and release to hold it back from the desktop while
TuxTalk types (Ctrl and a typed "w" close a tab).

Everything goes only to the user at the screen: logind's active user on
seat0, checked by user id (two sessions of the same user count as one), and
only keyboards of seat0 are read. Letters, numbers, Enter or Shift+letter
are never reported, so even a hostile program that connects learns nothing
about what is typed beyond when an AltGr, Compose or layout-switch key went
down (Right Alt, Right Ctrl, and Left Ctrl to a client watching Left
Ctrl+Space). Each client may ask for four shortcuts at most; TuxTalk asks
for three at most.

Hiding a held hotkey
--------------------
While TuxTalk records it hides the held hotkey from the desktop and keeps
that keyboard to itself, so a held F8 doesn't auto-repeat into the window
being dictated into. That's allowed once per press of a hotkey (never Esc)
that is physically held, for one client at a time. Left Ctrl is never hidden
on its own, only as the modifier of a held hotkey. It grabs the keyboard the
press came from, and another real keyboard only if it holds a modifier
reported with the press (Shift for a voice edit, on a split keyboard). One
that's a mouse as well (a Logitech one: see check_keyboard) moves no pointer
while it's held, and one holding a modifier for a hotkey pressed elsewhere
isn't held: that HIDE is refused. It ends by itself as soon as that key
comes up there, when someone else's session comes to the front, when the
program that asked goes away, or after 15 minutes at most. A press's release
is reported from the keyboard it was pressed on, so holding the same key on
another keyboard can't keep a recording or a grab going. Left Ctrl's own
release waits for the last keyboard of its group holding it: the desktop
sees Ctrl down until then, and TuxTalk mustn't type under it. A keyboard the
helper may only read (its node isn't writable for the input group) is never
held: its keys are reported, and a held hotkey reaches the desktop as it is.

What the helper can't stop: TuxTalk types through /dev/uinput, which the
user at the screen gets (secure_input.py), so any program they run can type
too, anywhere, the lock screen included. It can't read anything through it.

This file must stay self-contained: it is copied to a root-owned place
(/usr/local/libexec/tuxtalk/) and run with the system's own python3 in
isolated mode, outside TuxTalk. Standard library only, Python 3.8 or newer.

The protocol (one line each, over a Unix stream socket)
-------------------------------------------------------
client -> helper
  HELLO 1                      say hello; the helper answers
                               HELLO 1 <version> keyboards=<how many it reads>
                               (before 1.12 without the count; a client reads
                               the words it knows and passes over the rest)
  WATCH <spec> [<spec>...]     the keys to report: a code ("66" = F8) or
                               MODS:code with MODS from C A S M L
                               (Ctrl Alt Shift Meta/Super, and Left Ctrl alone,
                               only with key 57, since 1.14), "CA:25" = Ctrl+Alt+P;
                               a spec with L also reports Left Ctrl's own press and
                               release (DOWN 29 / UP 29) to that client;
                               the specs refused are named in ERR and the rest
                               apply, unless the whole line is refused (too many
                               keys or shortcuts: the last WATCH stays); MODS on
                               a key reported on its own are ignored
  HIDE <code> [<code>...]      hide these held keys from the desktop and keep
                               their keyboard until UNHIDE or they're let go
  UNHIDE                       give the keyboard back
helper -> client
  DOWN <code> <mods>           a watched key went down; mods = the modifier
                               codes held with it, comma-separated, or "-"
  UP <code>                    a key reported DOWN (or one of its modifiers)
                               was let go
  OK <what> / ERR <why>        answers to WATCH, HIDE and UNHIDE; a HIDE of
                               a key from a listen-only keyboard (never held:
                               a remapper's, or one it may only read) gets
                               ERR listen-only keyboard
Codes are Linux evdev key codes (linux/input-event-codes.h).
"""
import array
import contextlib
import errno
import fcntl
import os
import select
import signal
import socket
import struct
import sys
import time

PROTOCOL = 1
VERSION = "1.15"

DEFAULT_SOCKET_DIR = "/run/tuxtalk-hotkeys"
SOCKET_NAME = "hotkeys.sock"
SOCKET_PATH = os.path.join(DEFAULT_SOCKET_DIR, SOCKET_NAME)
DEFAULT_SEAT_FILE = "/run/systemd/seats/seat0"
INPUT_DIR = "/dev/input"
UDEV_DATA_DIR = "/run/udev/data"
SYS_CHAR_DIR = "/sys/dev/char"
# Bluetooth LE keyboards come through BlueZ's /dev/uhid, so they live under
# /sys/devices/virtual too. They count as real only while /dev/uhid is root's
# alone: where a udev rule hands it out (game streaming does), a program
# could make one.
UHID_NODE = "/dev/uhid"
UHID_SYS = "/sys/devices/virtual/misc/uhid/"

# --- evdev numbers (linux/input.h, linux/input-event-codes.h) -------------------

EV_SYN, EV_KEY, EV_REL = 0, 1, 2
SYN_REPORT, SYN_DROPPED = 0, 3
REL_X, BTN_LEFT = 0, 0x110
# The keyboard TuxTalk types with (virtual_keyboard.NAME): never read.
OWN_KEYBOARD_NAME = "TuxTalk virtual keyboard"
KEY_MAX = 0x2FF
KEY_ESC, KEY_SCROLLLOCK, KEY_PAUSE = 1, 70, 119
KEY_LEFTCTRL, KEY_RIGHTCTRL, KEY_RIGHTALT, KEY_SPACE = 29, 97, 100, 57
BUS_VIRTUAL = 0x06

F_KEYS = frozenset(list(range(59, 69)) + [87, 88] + list(range(183, 195)))   # F1-F24
# The modifiers that may also be watched on their own (see the rule at the top).
BARE_MODIFIERS = frozenset({KEY_RIGHTCTRL, KEY_RIGHTALT})
BARE_KEYS = F_KEYS | BARE_MODIFIERS | {KEY_ESC, KEY_PAUSE, KEY_SCROLLLOCK}

MODIFIERS = frozenset({KEY_LEFTCTRL, KEY_RIGHTCTRL, 42, 54, 56, KEY_RIGHTALT, 125, 126})
# The modifiers that can unlock a key, by the letter the protocol uses. Right
# Alt isn't among them: on most layouts it's AltGr, which types @, €, { and
# the like. Shift alone never unlocks anything (it types capitals).
UNLOCKERS = {
    "C": frozenset({29, 97}),       # Ctrl, left and right
    "A": frozenset({56}),           # Left Alt
    "S": frozenset({42, 54}),       # Shift
    "M": frozenset({125, 126}),     # Meta / Super
    "L": frozenset({KEY_LEFTCTRL}),  # Left Ctrl alone, for Space only (parse_spec)
}
LEFT_CTRL = "L"
SHORTCUT_LETTERS = frozenset("CAM")

# Letter keys tell a keyboard from a power button, a lid switch or a game
# controller; TuxTalk's own test (keyboard_devices.is_keyboard_device) is the same.
LETTER_KEYS = frozenset(list(range(16, 26)) + list(range(30, 39)) + list(range(44, 51)))

# struct input_event: a timeval (two longs), then type, code and value.
EVENT = struct.Struct("llHHi")


def _ioc(direction, number, size):
    return (direction << 30) | (size << 16) | (ord("E") << 8) | number


EVIOCGRAB = _ioc(1, 0x90, 4)
EVIOCGID = _ioc(2, 0x02, 8)
EVIOCSCLOCKID = _ioc(1, 0xA0, 4)
EVIOCSMASK = _ioc(1, 0x93, 16)      # struct input_mask: type, codes_size, codes_ptr
CLOCK_MONOTONIC = 1
KEY_BYTES = KEY_MAX // 8 + 1


def EVIOCGBIT(event_type, length):
    return _ioc(2, 0x20 + event_type, length)


def EVIOCGKEY(length):
    return _ioc(2, 0x18, length)


def EVIOCGNAME(length):
    return _ioc(2, 0x06, length)


# The event types a keyboard's reader needs, as EVIOCSMASK takes them (a
# bitmap of types in C longs): the keys, and the reports that end them,
# SYN_DROPPED included. The kernel copies it during the call.
_KEYS_ONLY = array.array("L", [1 << EV_SYN | 1 << EV_KEY])


def mask_to_keys(fd):
    """Have the kernel pass the reader on *fd* nothing but key events. A
    keyboard that's a mouse as well (check_keyboard) would otherwise wake
    it at every movement of the mouse. Raises OSError where the kernel
    can't (before 4.4): every event is read then, and all but the keys
    passed over."""
    address, count = _KEYS_ONLY.buffer_info()
    fcntl.ioctl(fd, EVIOCSMASK,
                struct.pack("IIQ", EV_SYN, count * _KEYS_ONLY.itemsize, address))


# --- limits: nobody gets to make this helper do much work ----------------------------

MAX_CLIENTS = 8
LISTEN_BACKLOG = 64           # connections waiting to be let in or turned away
ACCEPTS_PER_PASS = 64
MAX_LINE = 512
MAX_SPECS = 32
MAX_COMBOS = 4                # shortcuts per client; TuxTalk watches three at most
MAX_COMBO_KEYS_PER_HOLD = 2   # different shortcut keys while the modifiers stay down
MAX_OUTBUF = 64 * 1024
MAX_KEYBOARDS = 48
MAX_VIRTUAL_KEYBOARDS = 8      # made by programs; real keyboards always have room
HELLO_DEADLINE_S = 5.0
RESCAN_S = 2.0
# How long a keyboard whose reads failed with an unexpected error is left
# alone: long enough not to fill the log with a failure at every scan, short
# enough that a built-in keyboard, never plugged in again, comes back.
UNREADABLE_RETRY_S = 300.0
HIDE_MAX_S = 15 * 60          # a held keyboard comes back on release, and never later than this
INJECTED_TTL_S = 2.0
READ_EVENTS = 64              # events per read of a keyboard
# Reads of one keyboard per drain: 4,096 events, far more than a real
# keyboard's kernel buffer holds, so only a program flooding its own virtual
# keyboard reaches it (settle then leaves the injected records to expire).
DRAIN_READS = 64
# An event's kernel time is in whole microseconds; ours isn't.
STAMP_SLACK_S = 2e-6


def log(message):
    """One line to the journal. Never a key the user typed: only devices,
    connections and refusals."""
    print(f"tuxtalk-hotkeys: {message}", flush=True)


# --- what may be reported ------------------------------------------------------------

def parse_spec(spec):
    """("bare", code) or ("combo", code, frozenset of mod letters) for a WATCH
    spec, or raise ValueError saying why it is refused (see the rule at the top)."""
    mods, _, number = spec.rpartition(":")
    try:
        code = int(number)
    except ValueError:
        raise ValueError(f"not a key code: {spec!r}") from None
    if not 0 < code <= KEY_MAX or code in MODIFIERS - BARE_MODIFIERS:
        raise ValueError(f"not a key that can be watched: {spec!r}")
    letters = frozenset(mods.upper())
    if mods and (not letters <= set(UNLOCKERS) or len(letters) != len(mods)):
        raise ValueError(f"bad modifiers in {spec!r}")
    if LEFT_CTRL in letters:
        if code != KEY_SPACE:
            raise ValueError(f"Left Ctrl alone only unlocks Space: {spec!r}")
        return ("combo", code, letters)
    if code in BARE_KEYS:
        return ("bare", code)
    if len(letters & SHORTCUT_LETTERS) < 2:
        raise ValueError(f"key {code} is only reported with two of Ctrl, Alt and Super")
    return ("combo", code, letters)


def mods_satisfy(letters, held):
    """Whether every modifier named in *letters* is among *held*."""
    return all(UNLOCKERS[letter] & held for letter in letters)


def shortcut_mods(held):
    """How many of Ctrl, Alt and Super are among *held*: two make a shortcut."""
    return sum(1 for letter in SHORTCUT_LETTERS if UNLOCKERS[letter] & held)


def bits(buffer):
    """The set bit numbers of an ioctl bit-array reply."""
    return {i * 8 + j for i, byte in enumerate(buffer) for j in range(8) if byte >> j & 1}


def read_active_uid(seat_file):
    """The uid of the user whose session is in front on seat0, or None.
    logind keeps it in a plain file; no answer means nobody gets anything."""
    with contextlib.suppress(OSError), open(seat_file) as f:
        for line in f:
            key, _, value = line.strip().partition("=")
            if key == "ACTIVE_UID" and value.isdigit():
                return int(value)
    return None


def _udev_lines(rdev, data_dir):
    """What udev keeps about the device *rdev*, or None when it keeps nothing."""
    try:
        with open(os.path.join(data_dir, f"c{os.major(rdev)}:{os.minor(rdev)}")) as f:
            return f.read().splitlines()
    except OSError:
        return None


def udev_seat(rdev, data_dir=UDEV_DATA_DIR):
    """The seat udev put this input device on; seat0 unless it says otherwise."""
    for line in _udev_lines(rdev, data_dir) or ():
        if line.startswith("E:ID_SEAT="):
            return line.split("=", 1)[1] or "seat0"
    return "seat0"


def udev_tags(rdev, data_dir=UDEV_DATA_DIR):
    """The tags udev gave the device *rdev*, or None when udev keeps nothing
    about it (yet: a node just made, before udev has finished with it)."""
    lines = _udev_lines(rdev, data_dir)
    return None if lines is None else {line[2:] for line in lines if line[:2] in ("G:", "Q:")}


def _writable_by_others(st, tags):
    if "uaccess" in tags or st.st_mode & 0o002:
        return True
    return bool(st.st_mode & 0o020) and st.st_gid not in (0, *os.getgroups())


def others_can_write(st, data_dir=UDEV_DATA_DIR):
    """Whether someone besides root and this helper's own group may write to
    the device node *st* (an os.stat result): udev's "uaccess" tag (the user
    at the screen gets it), writable by everyone, or by some other group.
    Writing to a keyboard's node presses its keys. Without udev's data the
    tags can't be known: that counts as writable, the safe side, until
    Helper.reclassify looks again."""
    tags = udev_tags(st.st_rdev, data_dir)
    return tags is None or _writable_by_others(st, tags)


def uhid_is_private(node=UHID_NODE, data_dir=UDEV_DATA_DIR):
    """Whether only root can make keyboards through /dev/uhid. It's there
    from boot, so no udev data about it means no tags."""
    try:
        st = os.stat(node)
    except OSError:
        return True                 # no uhid: nobody can make one through it
    return (st.st_uid == 0 and not st.st_mode & 0o066
            and not _writable_by_others(st, udev_tags(st.st_rdev, data_dir) or set()))


def device_group(rdev, sys_dir=SYS_CHAR_DIR, writable=False, uhid_private=True):
    """Which keyboards' modifiers count together. Every real keyboard is in
    one group, "real", as the desktop pools them. A virtual one (made through
    uinput, which a program can do) is in a group of its own, "virtual:...",
    and so is a real one the user's programs may write to (*writable*),
    "own:...": a program could press its Ctrl. When it can't be told, a
    device counts as virtual: the safe side."""
    number = f"{os.major(rdev)}:{os.minor(rdev)}"
    path = os.path.realpath(os.path.join(sys_dir, number))
    physical = path.startswith("/sys/devices/") and not path.startswith("/sys/devices/virtual/")
    if not physical and path.startswith(UHID_SYS) and uhid_private:
        physical = True             # a Bluetooth LE keyboard
    if not physical:
        return f"virtual:{number}"
    return f"own:{number}" if writable else "real"


def node_group(st):
    """device_group's answer for the device node *st* (an os.stat result),
    from its permissions and udev's data as they are now."""
    return device_group(st.st_rdev, writable=others_can_write(st),
                        uhid_private=uhid_is_private())


def node_identity(node):
    """The device node *node* (a path, or an open descriptor) as (device
    number, inode, change time), which changes when a device is plugged in
    again, and when udev changes the node's owner or permissions; None when
    it's gone."""
    try:
        st = os.stat(node)
    except OSError:
        return None
    return st.st_rdev, st.st_ino, st.st_ctime_ns


def made_through_uinput(rdev, sys_dir=SYS_CHAR_DIR):
    """Whether the input device *rdev* is one a program made through uinput:
    its sysfs node is under /sys/devices/virtual/input. A Bluetooth LE
    device (uhid) is not, whoever may use /dev/uhid."""
    path = os.path.realpath(os.path.join(sys_dir, f"{os.major(rdev)}:{os.minor(rdev)}"))
    return path.startswith("/sys/devices/virtual/input/")


class Skipped(Exception):
    """A device that isn't read. *why* says so in plain words for the
    journal, once per device; None for one not worth a line there (a power
    button, a lid switch, the keyboard TuxTalk types with). Never a key."""

    def __init__(self, why=None):
        super().__init__(why)
        self.why = why


def describe_error(error):
    """"EACCES: Permission denied" for the OSError *error*."""
    return f"{errno.errorcode.get(error.errno, error.errno)}: {error.strerror}"


def open_node(path):
    """(descriptor, read_only) for the input device node *path*: read-write,
    or read-only where only reading is allowed (python-evdev, which TuxTalk
    used before the helper, does the same). Raises Skipped when it can't be
    opened at all."""
    flags = os.O_NONBLOCK | os.O_CLOEXEC
    try:
        return os.open(path, os.O_RDWR | flags), False
    except OSError as e:
        if e.errno not in (errno.EACCES, errno.EPERM):
            raise Skipped(f"couldn't open it ({describe_error(e)})") from None
    try:
        return os.open(path, os.O_RDONLY | flags), True
    except OSError as e:
        raise Skipped(f"couldn't open it ({describe_error(e)})") from None


# Why a keyboard opened read-only (open_node) is listen-only.
READ_ONLY = "it may only be read here"


def check_keyboard(keys, name):
    """Raise Skipped unless a device with the key codes *keys* and called
    *name* is a keyboard to read: it has letter keys, and it isn't the
    keyboard TuxTalk types with. One that's a mouse as well is read too,
    as libinput does: a Logitech receiver gives its keyboards a mouse's
    movement and buttons, and its mice a keyboard's keys and lights, so
    the two can't be told apart, and leaving them out left an MX Keys
    unheard (issue #26). Its movement never reaches the helper
    (mask_to_keys)."""
    if len(keys & LETTER_KEYS) < 3 or name == OWN_KEYBOARD_NAME:
        raise Skipped()


# --- keyboards --------------------------------------------------------------------------

class Keyboard:
    """One open /dev/input/event* keyboard and the keys down on it."""

    def __init__(self, path, fd, name, down, virtual, group, listen_only=None, mouse=False):
        """*down*: the keys down when it was opened; *virtual*: a program may
        have made it; *group*: device_group's answer; *listen_only*: why it
        is never held nor written to, or None; *mouse*: it's a mouse too
        (check_keyboard)."""
        self.path, self.fd, self.name, self.virtual, self.group = path, fd, name, virtual, group
        self.mouse = mouse
        # Two kinds. A program's keyboard shaped like a mouse (_probe):
        # a remapper's, carrying the mouse's motion and the text TuxTalk
        # types; holding it would freeze the one and swallow the other. And
        # one opened read-only (open_node): writing to it fails, so its keys
        # couldn't be told to go up before holding it.
        self.listen_only = listen_only
        self.said_listen_only = False      # logged once that it's never held
        self.down = set(down)
        # The modifiers the desktop saw go down on this keyboard: only these
        # unlock a key. One already down when it was opened, pressed while we
        # held the keyboard, or hidden from the desktop by us, waits to be let go.
        self.trusted = set()
        self.grabbed = False
        self.dropping = False      # events were lost; skip to the next report, then re-read
        # Events carry CLOCK_MONOTONIC times (see Helper.was_injected).
        self.monotonic = False

    @classmethod
    def open(cls, path):
        """The keyboard at *path*. Raises Skipped when it isn't one to read
        (a mouse, a power button, another seat's) or can't be opened or asked."""
        fd, read_only = open_node(path)
        # First thing: changing the clock throws away events already waiting.
        try:
            fcntl.ioctl(fd, EVIOCSCLOCKID, struct.pack("i", CLOCK_MONOTONIC))
            monotonic = True
        except OSError:
            monotonic = False
        try:
            keyboard = cls._probe(path, fd, read_only)
        except Skipped:
            os.close(fd)
            raise
        except OSError as e:
            os.close(fd)
            raise Skipped(f"couldn't ask what it is ({describe_error(e)})") from None
        keyboard.monotonic = monotonic
        try:
            mask_to_keys(fd)
        except OSError as e:
            log(f"reading every event of {path}, a mouse's movement included: "
                f"{describe_error(e)}")
        return keyboard

    @classmethod
    def _probe(cls, path, fd, read_only):
        """The keyboard open on *fd*, from what the kernel and udev say about
        it; raises Skipped or OSError as open() explains."""
        keys = bytearray(KEY_BYTES)
        fcntl.ioctl(fd, EVIOCGBIT(EV_KEY, len(keys)), keys, True)
        keys = bits(keys)
        rel = bytearray(2)
        fcntl.ioctl(fd, EVIOCGBIT(EV_REL, len(rel)), rel, True)
        pointer = REL_X in bits(rel) and BTN_LEFT in keys
        st = os.fstat(fd)
        name = bytearray(256)
        fcntl.ioctl(fd, EVIOCGNAME(len(name)), name, True)
        name = name.split(b"\0", 1)[0].decode("utf-8", "replace")
        check_keyboard(keys, name)
        seat = udev_seat(st.st_rdev)
        if seat != "seat0":
            raise Skipped(f"{name!r} is on {seat}, and only seat0's keyboards are read")
        group = node_group(st)
        ident = bytearray(8)
        fcntl.ioctl(fd, EVIOCGID, ident, True)
        virtual = struct.unpack("HHHH", ident)[0] == BUS_VIRTUAL or group.startswith("virtual:")
        state = bytearray(KEY_BYTES)
        fcntl.ioctl(fd, EVIOCGKEY(len(state)), state, True)
        if pointer and made_through_uinput(st.st_rdev):
            listen_only = "a program's keyboard and mouse in one"
        else:
            listen_only = READ_ONLY if read_only else None
        return cls(path, fd, name, bits(state), virtual, group, listen_only, mouse=pointer)

    def current_group(self):
        """Its group as its node's permissions and udev's data say now
        (node_group); raises OSError when the node can't be asked."""
        return node_group(os.fstat(self.fd))

    def kernel_down(self):
        """The keys the kernel says are down on this keyboard right now, or
        None when it can't be asked."""
        state = bytearray(KEY_BYTES)
        try:
            fcntl.ioctl(self.fd, EVIOCGKEY(len(state)), state, True)
        except OSError as e:
            log(f"couldn't read which keys are down on {self.name!r}: {e}")
            return None
        return bits(state)

    def grab(self, on):
        """Keep this keyboard's events to ourselves (on) or give them back."""
        try:
            fcntl.ioctl(self.fd, EVIOCGRAB, 1 if on else 0)
            self.grabbed = on
            return True
        except OSError:
            return False

    def inject(self, code, value):
        """Send a key event into this keyboard, as if it came from it."""
        try:
            os.write(self.fd, EVENT.pack(0, 0, EV_KEY, code, value)
                     + EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))
            return True
        except OSError:
            return False

    def close(self):
        with contextlib.suppress(OSError):
            os.close(self.fd)


# --- clients -------------------------------------------------------------------------------

class Client:
    """One connection, normally the TuxTalk dictation service."""

    def __init__(self, sock, uid):
        self.sock, self.uid = sock, uid
        self.connected_at = time.monotonic()
        self.inbuf = b""
        self.outbuf = b""
        self.hello = False
        self.bare = set()          # codes reported on their own
        self.combos = {}           # code -> list of modifier-letter sets
        self.origin = {}           # key reported DOWN, not yet UP -> the keyboard it came from
        self.mods_owed = {}        # modifier held at a reported DOWN, not yet UP -> its group
        self.hideable = set()      # reported keys whose press hasn't been hidden yet

    def send(self, line):
        self.outbuf += line.encode() + b"\n"
        if len(self.outbuf) > MAX_OUTBUF:
            raise ConnectionError("not reading its messages")
        self.flush()

    def flush(self):
        while self.outbuf:
            try:
                sent = self.sock.send(self.outbuf)
            except BlockingIOError:
                return
            self.outbuf = self.outbuf[sent:]


# --- the helper --------------------------------------------------------------------------

class Helper:
    """The service: its socket, the keyboards it reads and the clients it
    tells about hotkeys (see the rule at the top). One thread, one loop."""

    def __init__(self, socket_dir=DEFAULT_SOCKET_DIR, seat_file=DEFAULT_SEAT_FILE,
                 input_dir=INPUT_DIR):
        """Nothing is opened yet: listen() makes the socket, scan() opens the
        keyboards. The paths can be moved for tests."""
        self.socket_path = os.path.join(socket_dir, SOCKET_NAME)
        self.seat_file = seat_file
        self.input_dir = input_dir
        self.keyboards = {}        # path -> Keyboard
        self.clients = {}          # socket fd -> Client
        self.hide = None           # the HIDE in force (see start_hide)
        # (keyboard path, code, value, sent at, expires) for each event we sent ourselves
        self.injected = []
        self.combo_holds = {}      # group -> shortcut keys reported while its modifiers stay down
        self.combo_logged = set()  # groups whose hold has had "too many keys" logged
        self.next_scan = 0.0
        self.not_keyboards = {}    # path -> identity of a node that isn't a keyboard
        self.unreadable = {}       # path -> (identity, retry at) of a keyboard whose reads failed
        self.too_many = {}         # path -> identity already logged as one keyboard too many
        self.said_no_keyboard = False   # logged that the last scan found none
        self.said_only_mice = False     # logged that every keyboard it reads is a mouse too
        self.said_unlisted = False      # logged that the input folder couldn't be listed
        self.listener = None
        self.running = True

    # -- setup ------------------------------------------------------------------------

    def listen(self):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.socket_path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        sock.bind(self.socket_path)
        # Anyone may connect; only the user at the screen is kept (accept).
        os.chmod(self.socket_path, 0o666)
        sock.listen(LISTEN_BACKLOG)
        sock.setblocking(False)
        self.listener = sock

    def scan(self):
        """Open keyboards that have appeared. Not while hiding, so a keyboard
        plugged in mid-recording isn't left half-grabbed."""
        if self.hide is not None:
            return
        try:
            names = sorted(n for n in os.listdir(self.input_dir) if n.startswith("event"))
        except OSError as e:
            if not self.said_unlisted:
                self.said_unlisted = True
                log(f"couldn't list {self.input_dir} ({describe_error(e)})")
            self.say_if_no_keyboard()
            return
        self.said_unlisted = False
        paths = [os.path.join(self.input_dir, name) for name in names]
        present = set(paths)
        # Gone: nothing left to wait for, and the tables stay as small as
        # /dev/input however often a program makes and destroys a keyboard.
        for table in (self.not_keyboards, self.unreadable, self.too_many):
            for path in table.keys() - present:
                del table[path]
        for keyboard in list(self.keyboards.values()):
            self.reclassify(keyboard)
            self.reopen_if_writable(keyboard)
        for path in paths:
            if path not in self.keyboards:
                self.open_if_keyboard(path)
        self.say_if_no_keyboard()

    def say_if_no_keyboard(self):
        """Warn once when no keyboard is watched, until one is again, and
        once when only mice are: a Logitech mouse counts as a keyboard
        (check_keyboard), so the count can't tell that the keyboard itself
        is missing."""
        if not self.keyboards and not self.said_no_keyboard:
            log("watching no keyboard at all, so no hotkey can reach TuxTalk (each device "
                "left out with letter keys was named, with why, when it was first seen)")
        self.said_no_keyboard = not self.keyboards
        only_mice = bool(self.keyboards) and all(k.mouse for k in self.keyboards.values())
        if only_mice and not self.said_only_mice:
            log("watching only keyboards that are mice too; if F8 does nothing, the "
                "keyboard you type on may be one this helper can't read")
        self.said_only_mice = only_mice

    def reopen_if_writable(self, keyboard):
        """A keyboard opened read-only whose node may now be written to (a
        udev rule reloaded, permissions fixed) is closed, to be opened again
        read-write by this scan, so it can be held again. Not while a key
        someone watches is down on it: closing lets it go, which would end a
        recording mid-hold. A later scan does it."""
        if keyboard.listen_only != READ_ONLY or not os.access(keyboard.path, os.W_OK):
            return
        if any(keyboard.down & (c.bare | c.combos.keys()) for c in self.clients.values()):
            return
        self.drop_keyboard(keyboard, "writable now, opening it again")

    def open_if_keyboard(self, path):
        """Open the device at *path* if it's a keyboard there's room for. One
        that isn't is remembered by its identity, so it isn't opened again,
        and the journal says why once, when that's worth a line."""
        identity = node_identity(path)
        if identity is None:
            return
        if self.not_keyboards.get(path) == identity:
            return                      # a mouse, a button, a game controller
        failed = self.unreadable.get(path)
        if failed is not None and failed[0] == identity and time.monotonic() < failed[1]:
            return
        try:
            keyboard = Keyboard.open(path)
        except Skipped as skipped:
            self.not_keyboards[path] = identity
            if skipped.why:
                log(f"not watching {path}: {skipped.why}")
            return
        virtual = sum(1 for k in self.keyboards.values() if k.virtual)
        if len(self.keyboards) >= MAX_KEYBOARDS or (
                keyboard.virtual and virtual >= MAX_VIRTUAL_KEYBOARDS):
            # Not remembered as "not a keyboard": once there's room it's
            # opened on the next scan. Logged once.
            keyboard.close()
            if self.too_many.get(path) != identity:
                self.too_many[path] = identity
                log(f"not watching {keyboard.name!r}: too many keyboards")
            return
        self.not_keyboards.pop(path, None)
        self.unreadable.pop(path, None)
        self.too_many.pop(path, None)
        self.keyboards[path] = keyboard
        never_held = f", never held: {keyboard.listen_only}" if keyboard.listen_only else ""
        log(f"watching keyboard {keyboard.name!r} ({path}){never_held}")

    def drop_keyboard(self, keyboard, why="gone"):
        log(f"keyboard {why}: {keyboard.name!r} ({keyboard.path})")
        self.keyboards.pop(keyboard.path, None)
        keyboard.close()
        # Its keys can't come up any more: tell whoever was told they went down.
        for code in sorted(keyboard.down):
            self.deliver_up(keyboard, code)
        self.end_combo_hold(keyboard.group)
        if self.hide is not None and not any(
                k.path in self.keyboards for k in self.hide["keyboards"]):
            self.end_hide()

    def resync(self, keyboard):
        """After lost events, believe the kernel. A key found down now is
        down but untrusted; one found up is let go."""
        real = keyboard.kernel_down() or set()      # unknown: let everything go
        for code in sorted(keyboard.down - real):
            keyboard.down.discard(code)
            keyboard.trusted.discard(code)
            self.deliver_up(keyboard, code)
        keyboard.down |= real
        self.end_combo_hold(keyboard.group)

    def reclassify(self, keyboard):
        """Look again at who may write to *keyboard*: a udev rule may have
        given it "uaccess" since it was opened, or udev may only now have
        finished with it. One a program may press keys on mustn't lend its
        Ctrl and Alt to the real keyboards, so a change of group forgets
        which of its modifiers were trusted, and lets go of the modifiers
        clients were told of under the old group."""
        try:
            group = keyboard.current_group()
        except OSError:
            return                      # gone: its next read drops it
        if group == keyboard.group:
            return
        old, keyboard.group = keyboard.group, group
        keyboard.virtual = keyboard.virtual or group.startswith("virtual:")
        keyboard.trusted.clear()
        log(f"keyboard {keyboard.name!r} ({keyboard.path}) now counts as {group}")
        for client in list(self.clients.values()):
            for mod, owed in list(client.mods_owed.items()):
                if owed == old and not self.held(mod, old):
                    del client.mods_owed[mod]
                    # Reported on its own too: its release is the UP, as
                    # in deliver_up (the desktop still sees it down).
                    if mod not in client.origin:
                        self.send(client, f"UP {mod}")
        self.end_combo_hold(old)
        self.end_combo_hold(group)

    def recheck_pool(self, keyboard):
        """Before the real keyboards' modifiers count together for a key on
        *keyboard*, make sure none of them has become one a program can
        write to since the last scan (reclassify)."""
        if keyboard.group != "real":
            return
        for k in [k for k in self.keyboards.values() if k.group == "real"]:
            self.reclassify(k)

    # -- state ------------------------------------------------------------------------

    def held(self, code, group):
        """Whether a keyboard of *group* already holds *code*. Per group, so a
        program's virtual keyboard holding F8 never swallows the real F8."""
        return self.holder(code, group) is not None

    def holder(self, code, group):
        """A keyboard of *group* holding *code*, or None."""
        return next((k for k in self.keyboards.values()
                     if k.group == group and code in k.down), None)

    def group_mods(self, group):
        """The trusted modifiers held on the keyboards of *group*."""
        mods = set()
        for k in self.keyboards.values():
            if k.group == group:
                mods |= k.down & k.trusted
        return mods

    def end_combo_hold(self, group):
        """Once fewer than two of Ctrl, Alt and Super are down in *group*, a
        new modifier hold may report shortcut keys again."""
        if group in self.combo_holds and shortcut_mods(self.group_mods(group)) < 2:
            del self.combo_holds[group]
            self.combo_logged.discard(group)

    def was_injected(self, keyboard, code, value, stamp=None):
        """Whether this event is one we sent into *keyboard* ourselves. One
        the kernel stamped before we sent ours is real, whatever it looks
        like: the key came up just as we were about to say so, and the kernel
        dropped our copy as saying nothing new."""
        now = time.monotonic()
        self.injected = [i for i in self.injected if i[4] > now]
        for i, (p, c, v, sent, _) in enumerate(self.injected):
            if (p, c, v) == (keyboard.path, code, value):
                if keyboard.monotonic and stamp is not None and stamp < sent - STAMP_SLACK_S:
                    continue
                del self.injected[i]
                return True
        return False

    def inject(self, keyboard, code, value):
        sent = time.monotonic()
        if keyboard.inject(code, value):
            self.injected.append((keyboard.path, code, value, sent, sent + INJECTED_TTL_S))
            return True
        return False

    def settle(self, keyboard, codes):
        """After injecting into *keyboard*: read back everything the kernel
        passed on (it does so at once), so a record still waiting is one it
        dropped and is forgotten, not left to swallow the real event later.
        A flood that outlasts the drain keeps them: they expire by themselves.
        A key among *codes* that came up meanwhile, but that our own
        "down again" left down in the kernel, is put up there too (only we
        see it: the keyboard is held)."""
        dry = self.drain(keyboard)
        if keyboard.path not in self.keyboards:
            return
        kernel = keyboard.kernel_down() or set()
        stale = [c for c in codes if c not in keyboard.down and c in kernel]
        for code in stale:
            self.inject(keyboard, code, 0)
        if stale:
            dry = self.drain(keyboard)
        if dry:
            self.injected = [i for i in self.injected if i[0] != keyboard.path]

    # -- keys -------------------------------------------------------------------------

    def on_key(self, keyboard, code, value, stamp=None):
        """A key event read from *keyboard* (*stamp*: its kernel time)."""
        if self.was_injected(keyboard, code, value, stamp):
            return
        if value == 1:
            elsewhere = self.held(code, keyboard.group)
            keyboard.down.add(code)
            if code in MODIFIERS and not keyboard.grabbed:
                keyboard.trusted.add(code)
            if not elsewhere:
                self.deliver_down(keyboard, code)
        elif value == 0:
            keyboard.down.discard(code)
            keyboard.trusted.discard(code)
            self.deliver_up(keyboard, code)
            if code in MODIFIERS:
                self.end_combo_hold(keyboard.group)
        # value 2 is auto-repeat: never reported

    def deliver_up(self, keyboard, code):
        """*code* came up on *keyboard*. A release goes to whoever was told
        about the press from this keyboard, at the screen or not: it says
        nothing new. A modifier is let go once no keyboard of its group holds it."""
        for client in list(self.clients.values()):
            if client.origin.get(code) == keyboard.path:
                still = self.holder(code, keyboard.group) if code == KEY_LEFTCTRL else None
                if still is not None:
                    # The desktop sees Ctrl down while any keyboard of the
                    # pool holds it: that one's release is the UP.
                    client.origin[code] = still.path
                    continue
                del client.origin[code]
                client.hideable.discard(code)
                # A Right Ctrl reported on its own may be owed as another
                # key's modifier too: this one UP settles both.
                client.mods_owed.pop(code, None)
                self.send(client, f"UP {code}")
            elif client.mods_owed.get(code) == keyboard.group and \
                    not self.held(code, keyboard.group):
                del client.mods_owed[code]
                # Still held where it was reported on its own (a program's
                # keyboard let go of its own Ctrl): that keyboard's release
                # is the UP, and an early one would let TuxTalk type while
                # the desktop holds Ctrl.
                if code not in client.origin:
                    self.send(client, f"UP {code}")

    def deliver_down(self, keyboard, code):
        """*code* went down on *keyboard*: DOWN to each client at the screen
        that watches it and may have it now (client_wants). Left Ctrl is
        in the bare set only of a client watching Left Ctrl+Space (watch)."""
        if code in MODIFIERS - BARE_MODIFIERS and code != KEY_LEFTCTRL:
            return
        # Most keys are wanted by nobody: drop them before anything else.
        interested = [c for c in self.clients.values()
                      if c.hello and (code in c.bare or code in c.combos)]
        if not interested:
            return
        active = read_active_uid(self.seat_file)
        if active is None:
            return
        self.recheck_pool(keyboard)
        # Modifiers count only from this keyboard's group (all real keyboards,
        # or this one virtual keyboard), and only those the desktop saw go
        # down (see Keyboard.trusted). A Right Ctrl watched on its own isn't
        # held "with" itself.
        mods = self.group_mods(keyboard.group) - {code}
        for client in interested:
            if client.uid == active and self.client_wants(client, keyboard, code, mods):
                client.origin[code] = keyboard.path
                if code != KEY_LEFTCTRL:
                    # Hidden only as a held hotkey's modifier: alone, a
                    # client could take the keyboard from every Ctrl shortcut.
                    client.hideable.add(code)
                for mod in mods:
                    client.mods_owed[mod] = keyboard.group
                self.send(client, f"DOWN {code} {','.join(map(str, sorted(mods))) or '-'}")

    def client_wants(self, client, keyboard, code, mods):
        """Whether *client* is told *code* went down on *keyboard*, with the
        trusted modifiers *mods* held: on its own, or as a shortcut it watches.
        Not only a question: a real keyboard takes the press over from a
        virtual one here, and a shortcut key counts toward its hold
        (combo_allowed), so it's asked once per press."""
        prior = self.keyboards.get(client.origin.get(code))
        if prior is not None and prior is not keyboard and code in prior.down:
            # Already held on another keyboard: no second DOWN. The press
            # stays with that keyboard, so a program's keyboard can't take
            # over the real press (or the grab). Only a real keyboard takes
            # a press over from one a program may write to (virtual, or real
            # with "uaccess"), quietly: its release is the UP, so a program
            # holding F8 can't swallow the real F8.
            if prior.group != "real" and keyboard.group == "real":
                client.origin[code] = keyboard.path
            return False
        if code in client.bare:
            return True
        # Not while a keyboard is held: the desktop doesn't see those keys.
        return (self.hide is None
                and any(mods_satisfy(letters, mods) for letters in client.combos.get(code, ()))
                and self.combo_allowed(keyboard.group, code))

    def combo_allowed(self, group, code):
        """Whether shortcut key *code* may be reported in this modifier hold:
        a key already reported in it, or one of the first two different ones.
        More is typing with Ctrl+Alt held, not a shortcut (see the top)."""
        keys = self.combo_holds.setdefault(group, set())
        if code in keys:
            return True
        if len(keys) >= MAX_COMBO_KEYS_PER_HOLD:
            if group not in self.combo_logged:
                self.combo_logged.add(group)    # once per hold, never the key
                log("more shortcut keys under one held Ctrl/Alt/Super than a "
                    "shortcut has; not reporting them")
            return False
        keys.add(code)
        return True

    # -- hiding a held hotkey ---------------------------------------------------------

    def start_hide(self, client, codes):
        """HIDE *codes* for *client* (see "Hiding a held hotkey" at the top);
        the answer to send."""
        if client.uid != read_active_uid(self.seat_file):
            return "ERR only the user at the screen can do that"
        if self.hide is not None:
            if self.hide["client"] is client:
                return "OK HIDE"            # already done for this client
            return "ERR the keyboards are already held"
        # Read what's already waiting first, so a key let go a moment ago
        # counts as let go (its UP goes out before this answer).
        for keyboard in list(self.keyboards.values()):
            self.read_keyboard(keyboard)
        presses = self.presses_to_hide(client, codes)
        if not presses:
            return "ERR the hotkey isn't held"
        mods = [c for c in codes if c in client.mods_owed and c in MODIFIERS]
        if self.listen_only_holding(presses, mods):
            # kanata's keyboard (issue #23), or one that may only be read:
            # its keys reach the desktop as they are, which is how that
            # setup works, not a fault. All or nothing: hiding the rest
            # would be answered OK while the desktop still sees part of it.
            return "ERR listen-only keyboard"
        held_on = self.hide_presses(presses, mods)
        if held_on is None:
            return "ERR couldn't hide the keys"
        if not held_on:
            return "ERR couldn't hold the keyboard"
        client.hideable.difference_update(c for _, c in presses)
        self.hide = {"client": client, "uid": client.uid, "presses": presses,
                     "keyboards": held_on, "since": time.monotonic()}
        return "OK HIDE"

    def listen_only_holding(self, presses, mods):
        """Whether a listen-only keyboard holds part of what a HIDE asks
        for: one of *presses* came from it, or it holds one of the pressed
        keys or *mods* in a press's group (Right Ctrl held on two keyboards
        at once: the desktop sees it down while either holds it). Such a
        keyboard is never held nor written to, so that part can't be
        hidden. Logged once per keyboard."""
        origins = {path for path, _ in presses}
        groups = {self.keyboards[path].group for path in origins}
        wanted = set(mods) | {code for _, code in presses}
        for keyboard in self.keyboards.values():
            if keyboard.listen_only and (keyboard.path in origins or (
                    keyboard.group in groups and keyboard.down & wanted)):
                if not keyboard.said_listen_only:
                    keyboard.said_listen_only = True
                    log(f"not holding keyboard {keyboard.name!r}: {keyboard.listen_only}")
                return True
        return False

    def hide_presses(self, presses, mods):
        """Hide *presses* on their keyboards and *mods* on those and the other
        keyboards of their groups: the keyboards now held, or None when one
        couldn't be hidden (any already held are let go)."""
        origins = {path for path, _ in presses}
        groups = {self.keyboards[path].group for path in origins}
        held_on = []
        for keyboard in list(self.keyboards.values()):
            if keyboard.path not in origins and keyboard.group not in groups:
                continue
            # Once each: a Right Ctrl may be a press and another's modifier,
            # and a second injected release would swallow the real one.
            codes = dict.fromkeys([c for path, c in presses if path == keyboard.path] + mods)
            outcome = self.hide_on(keyboard, list(codes), keyboard.path in origins)
            if outcome is None:
                self.let_go(held_on)
                return None
            if outcome:
                held_on.append(keyboard)
        return held_on

    def presses_to_hide(self, client, codes):
        """{(keyboard path, code)} of *codes* that may be hidden now: once per
        press, and only on the keyboard the press came from (a hotkey whose
        press was hidden before needs a new press); never Esc."""
        return {(client.origin[c], c) for c in codes
                if c != KEY_ESC and c in client.hideable and c in client.origin
                and client.origin[c] in self.keyboards
                and c in self.keyboards[client.origin[c]].down}

    def hide_on(self, keyboard, codes, is_origin):
        """Hide those of *codes* down on *keyboard*: True when it's held now,
        False when it isn't (nothing to hide there, or it couldn't be held),
        None when a key couldn't be hidden (and it isn't held).

        The desktop has seen these keys go down: tell it they went up, so it
        doesn't auto-repeat them, keep the keyboard, then mark them down
        again, which only we see, so the real release still reaches us (let_up
        goes by the kernel's state, not what's been read, in case a release is
        on its way). The press's own keyboard (*is_origin*) is kept; another
        of its group only has its modifiers hidden. A listen-only keyboard is
        never held or written to."""
        if keyboard.listen_only:
            return False            # holds none of them (listen_only_holding)
        if keyboard.mouse and not is_origin:
            return self.never_hold_mouse(keyboard, codes)
        released = self.let_up(keyboard, codes)
        if released is None:
            return None
        if not (is_origin or released):
            return False
        # The desktop now believes these are up.
        keyboard.trusted.difference_update(released)
        if not keyboard.grab(True):
            # Not held, so the desktop would see the keys go down again and
            # repeat them: leave them up for it.
            log(f"couldn't hold keyboard {keyboard.name!r}")
            self.settle(keyboard, ())
            return False
        down_again = [c for c in released if self.inject(keyboard, c, 1)]
        self.settle(keyboard, down_again)
        if len(down_again) < len(released):
            # One left up in the kernel would come up unheard, the keyboard
            # held to HIDE_MAX_S: give it back, believe the kernel, refuse.
            log(f"couldn't mark the keys down again on {keyboard.name!r}")
            self.let_go([keyboard])
            if keyboard.path in self.keyboards:
                self.resync(keyboard)
            return None
        return True

    def never_hold_mouse(self, keyboard, codes):
        """hide_on's answer for *keyboard*, a mouse too, holding none of the
        hotkey's presses: False when it holds none of *codes* (modifiers)
        either, else None, so the hide is refused whole. A Logitech mouse
        is read as a keyboard (check_keyboard), and with Shift on one of
        its buttons it would be held, frozen, until the hotkey comes up.
        Judged by the kernel's state, not what's been read: a Shift press
        queued behind its clicks counts."""
        down = keyboard.kernel_down()
        if down is not None and not down & set(codes):
            return False
        log(f"not holding {keyboard.name!r}: a mouse too, maybe holding a modifier")
        return None

    def let_up(self, keyboard, codes):
        """Tell the desktop that those of *codes* down on *keyboard* went up;
        the ones that were, or None when one couldn't be. The desktop then
        still sees it down: a Right Ctrl it holds would turn TuxTalk's typing
        into shortcuts, so nothing may be called hidden. What did go up is
        left up for it."""
        really_down = keyboard.kernel_down()
        if really_down is None:
            return None                 # nothing sent yet: nothing to take back
        wanted = [c for c in codes if c in really_down]
        released = [c for c in wanted if self.inject(keyboard, c, 0)]
        if len(released) == len(wanted):
            return released
        log(f"couldn't let the keys go up on {keyboard.name!r}")
        keyboard.trusted.difference_update(released)
        self.settle(keyboard, ())
        return None

    def end_hide(self, why=None):
        if self.hide is None:
            return
        keyboards, self.hide = self.hide["keyboards"], None
        if why:
            log(f"gave the keyboard back: {why}")
        self.let_go(keyboards)

    def let_go(self, keyboards):
        """Give *keyboards* back to the desktop, closing one that won't let go.
        What came while it was held is read first, as such: a modifier
        pressed then, which the desktop never saw, isn't trusted."""
        for keyboard in keyboards:
            if keyboard.path not in self.keyboards or not keyboard.grabbed:
                continue
            self.drain(keyboard)
            if keyboard.path in self.keyboards and not keyboard.grab(False):
                # Closing the device always ends the kernel's grab; the
                # keyboard is opened afresh on the next scan.
                log(f"couldn't let go of {keyboard.name!r}; closing it")
                self.drop_keyboard(keyboard)

    def check_hide(self):
        """The safety net: a keyboard is never kept once the hotkey is let go."""
        hide = self.hide
        if hide is None:
            return
        if time.monotonic() - hide["since"] > HIDE_MAX_S:
            self.end_hide("held too long")
        elif read_active_uid(self.seat_file) != hide["uid"]:
            self.end_hide("someone else is at the screen")
        elif not any(path in self.keyboards and code in self.keyboards[path].down
                     for path, code in hide["presses"]):
            self.end_hide()             # let go: TuxTalk's own UNHIDE is on its way

    # -- talking ----------------------------------------------------------------------

    def accept(self):
        """Let in everyone waiting, so connections from other users (turned
        away at once) can't keep the queue full and TuxTalk out."""
        for _ in range(ACCEPTS_PER_PASS):
            try:
                sock, _ = self.listener.accept()
            except OSError:
                return
            self.admit(sock)

    def admit(self, sock):
        """Keep a new connection from the user at the screen; close any other."""
        _pid, uid, _gid = struct.unpack("3i", sock.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        active = read_active_uid(self.seat_file)
        if uid != active:
            sock.close()                    # only the user at the screen
            return
        if len(self.clients) >= MAX_CLIENTS:
            # Make room rather than turn the user at the screen away, so
            # nothing can hold every place and keep TuxTalk out: someone no
            # longer at the screen goes first, then a connection that never
            # said hello, then the newest. TuxTalk connects at login and
            # stays, so it's the oldest: a program opening connection after
            # connection only pushes out its own. Not the one holding a keyboard.
            keeper = self.hide["client"] if self.hide else None
            others = sorted((c for c in self.clients.values() if c is not keeper),
                            key=lambda c: (c.uid == active, c.hello, -c.connected_at))
            if not others:
                sock.close()
                return
            self.drop_client(others[0])
        sock.setblocking(False)
        self.clients[sock.fileno()] = Client(sock, uid)

    def drop_client(self, client):
        if self.hide is not None and self.hide["client"] is client:
            self.end_hide("TuxTalk went away")
        self.clients.pop(client.sock.fileno(), None)
        with contextlib.suppress(OSError):
            client.sock.close()

    def send(self, client, line):
        try:
            client.send(line)
        except (ConnectionError, OSError):
            self.drop_client(client)

    def on_client_data(self, client):
        """Read what *client* sent and answer each whole line; drop a client
        that hung up or sends lines longer than MAX_LINE."""
        try:
            data = client.sock.recv(4096)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self.drop_client(client)
            return
        client.inbuf += data
        while b"\n" in client.inbuf:
            line, client.inbuf = client.inbuf.split(b"\n", 1)
            if len(line) > MAX_LINE:
                self.drop_client(client)
                return
            reply = self.handle(client, line.decode("ascii", "replace").strip())
            if reply:
                self.send(client, reply)
            if client.sock.fileno() not in self.clients:
                return
        if len(client.inbuf) > MAX_LINE:
            self.drop_client(client)

    def handle(self, client, line):
        """The answer to one line from *client*, or None (see the protocol at
        the top)."""
        words = line.split()
        if not words:
            return None
        command, args = words[0].upper(), words[1:]
        if command == "HELLO":
            if args[:1] != [str(PROTOCOL)]:
                return f"ERR this helper speaks protocol {PROTOCOL}"
            client.hello = True
            return f"HELLO {PROTOCOL} {VERSION} keyboards={len(self.keyboards)}"
        if not client.hello:
            return "ERR say HELLO first"
        if command == "WATCH":
            return self.watch(client, args)
        if command == "HIDE":
            try:
                codes = [int(a) for a in args[:MAX_SPECS]]
            except ValueError:
                return "ERR not key codes"
            return self.start_hide(client, codes)
        if command == "UNHIDE":
            if self.hide is not None and self.hide["client"] is client:
                self.end_hide()
            return "OK UNHIDE"
        return "ERR unknown command"

    def watch(self, client, specs):
        """WATCH *specs*: what *client* is told about from now on. The specs
        that are allowed apply even when others are refused, but a line with
        too many keys or shortcuts is refused whole and the last WATCH stays."""
        if len(specs) > MAX_SPECS:
            return "ERR too many keys"
        bare, combos, refused = set(), {}, []
        for spec in specs:
            try:
                parsed = parse_spec(spec)
            except ValueError as e:
                refused.append(str(e))
                continue
            if parsed[0] == "bare":
                bare.add(parsed[1])
            else:
                combos.setdefault(parsed[1], []).append(parsed[2])
        if sum(map(len, combos.values())) > MAX_COMBOS:
            return f"ERR no more than {MAX_COMBOS} shortcuts"
        if any(LEFT_CTRL in letters for sets in combos.values() for letters in sets):
            bare.add(KEY_LEFTCTRL)      # its own press and release (the rule at the top)
        client.bare, client.combos = bare, combos
        return f"ERR {'; '.join(refused)}" if refused else "OK WATCH"

    # -- the loop ---------------------------------------------------------------------

    def read_keyboard(self, keyboard):
        """Handle the events waiting on *keyboard*; how many bytes were read."""
        try:
            data = os.read(keyboard.fd, EVENT.size * READ_EVENTS)
        except BlockingIOError:
            return 0
        except OSError as e:
            # Gone (unplugged), or failing in a way the next scan may mend:
            # either way, stop reading it rather than spin on it.
            if e.errno not in (errno.ENODEV, errno.EIO, errno.EBADF):
                log(f"couldn't read keyboard {keyboard.name!r}: {e}")
                # By its descriptor: the path may hold a new device by now.
                identity = node_identity(keyboard.fd)
                if identity is not None:
                    self.unreadable[keyboard.path] = (
                        identity, time.monotonic() + UNREADABLE_RETRY_S)
                self.drop_keyboard(keyboard, "let go")
                return 0
            self.drop_keyboard(keyboard)
            return 0
        for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
            sec, usec, kind, code, value = EVENT.unpack_from(data, offset)
            if kind == EV_SYN and code == SYN_DROPPED:
                keyboard.dropping = True    # the kernel lost events
            elif keyboard.dropping:
                if kind == EV_SYN and code == SYN_REPORT:
                    keyboard.dropping = False
                    self.resync(keyboard)
            elif kind == EV_KEY:
                self.on_key(keyboard, code, value, sec + usec / 1e6)
        return len(data)

    def drain(self, keyboard):
        """Read *keyboard* until nothing is waiting, or DRAIN_READS times: a
        program flooding its own virtual keyboard mustn't hold the loop.
        False when it stopped with events still waiting."""
        for _ in range(DRAIN_READS):
            if keyboard.path not in self.keyboards or not self.read_keyboard(keyboard):
                return True
        return False

    def drop_silent_clients(self):
        """Connections that never said hello don't get to hold a place."""
        deadline = time.monotonic() - HELLO_DEADLINE_S
        for client in list(self.clients.values()):
            if not client.hello and client.connected_at < deadline:
                self.drop_client(client)

    def run_once(self, timeout=0.5):
        """One pass of the loop: scan now and then, wait up to *timeout* for
        something to read or room to write, handle it, then the deadlines.
        A signal doesn't end the wait early (PEP 475): stop() only sets a
        flag, read before the next pass."""
        now = time.monotonic()
        if now >= self.next_scan:
            self.scan()
            self.next_scan = now + RESCAN_S
        readers = [self.listener] + [k.fd for k in self.keyboards.values()] \
            + [c.sock for c in self.clients.values()]
        writers = [c.sock for c in self.clients.values() if c.outbuf]
        ready, writable, _ = select.select(readers, writers, [], timeout)
        self.on_readable(ready)
        self.flush_clients(writable)
        self.drop_silent_clients()
        self.check_hide()

    def on_readable(self, ready):
        """Handle what select() found waiting: a connection, a keyboard's
        events or a client's line. Each is looked up again, since handling
        one can drop another."""
        by_fd = {k.fd: k for k in self.keyboards.values()}
        for item in ready:
            if item is self.listener:
                self.accept()
            elif isinstance(item, int):
                if item in by_fd and by_fd[item].path in self.keyboards:
                    self.read_keyboard(by_fd[item])
            else:
                client = self.client_of(item)
                if client is not None:
                    self.on_client_data(client)

    def client_of(self, sock):
        """The client on *sock*, or None once it's been dropped (closed: -1)."""
        return self.clients.get(sock.fileno()) if sock.fileno() >= 0 else None

    def flush_clients(self, writable):
        """Send what waits for the clients whose sockets have room again."""
        for sock in writable:
            client = self.client_of(sock)
            if client is not None:
                try:
                    client.flush()
                except OSError:
                    self.drop_client(client)

    def run(self):
        self.listen()
        log(f"version {VERSION} listening on {self.socket_path}")
        try:
            while self.running:
                self.run_once()
        finally:
            self.end_hide()
            for keyboard in list(self.keyboards.values()):
                keyboard.close()
            with contextlib.suppress(OSError):
                os.unlink(self.socket_path)


def main():
    """Run the service until SIGTERM or SIGINT (tuxtalk-hotkeys.service).
    It takes no arguments: anything else on its command line is a mistake,
    and starting anyway would take the socket from the running service."""
    if sys.argv[1:]:
        print("usage: tuxtalk-hotkeys (no arguments: tuxtalk-hotkeys.service runs it)",
              file=sys.stderr)
        return 2
    helper = Helper()

    def stop(*_args):
        helper.running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    helper.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
