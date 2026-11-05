"""TuxTalk's side of the hotkey helper (hotkey_helper.py).

When the helper is installed, the dictation service doesn't open any keyboard
itself: the user needn't be in the "input" group, and nothing they run gets
to read their keys. HelperKeyboard stands in for the keyboards so that
hotkeys.run, with all its care for hold, toggle, double-tap, Shift edits,
Ctrl+Alt+P and Esc, works unchanged. It answers the handful of evdev calls
that loop makes:

* read()        the hotkey presses and releases the helper reported, and the
                modifiers held with them, as evdev-style key events;
* active_keys() the keys known to be down (only those the helper reported);
* write()       the loop's "tell the desktop the key went up" events. The
                helper does that itself on grab(), so here they're only echoed
                back, the way the kernel echoes a real injected event, so the
                loop's keyboard_grab.is_own_injected_event skips them as before;
* grab() / ungrab()  HIDE / UNHIDE: the helper hides the held hotkey from
                the desktop and keeps the keyboards while it's held. Its
                answer comes later, and may be no (another program holds
                the keyboard): *hidden* says whether it did.

If the helper goes away (restarted, updated), every key it said was down is
released here, so a recording can't hang on a key-up that will never come,
and it reconnects within a second or so.

active_keys() is also asked from the threads that type (keyboard_output.send_keys
waits for a Right Ctrl dictation key to come up), so what the helper said is
taken in under a lock.
"""
import os
import socket
import threading
import time
from collections import deque, namedtuple

from . import modifier_hotkeys
from .hotkey_helper import KEY_SPACE, LEFT_CTRL, PROTOCOL, SOCKET_PATH
from .logger import setup_logger

logger = setup_logger(__name__)

RECONNECT_S = 1.0

EV_KEY, EV_SYN = 1, 0

# What the loop reads: evdev.InputEvent's three fields that it uses.
KeyEvent = namedtuple("KeyEvent", "type code value")

# A command the helper hasn't answered yet; a HIDE notes the keys it hides
# and the Compose press it's for (modifier_hotkeys.compose_press), which an
# answer coming in after the next press must not confirm.
Asked = namedtuple("Asked", "command codes press", defaults=((), None))

# The helper's refusals of a HIDE that never come for a remapper's keyboard:
# the press reached the desktop from a real keyboard as it was. Since 1.13
# they come only once no listen-only keyboard (a remapper's, or one it may
# only read) holds any of the press. Helpers 1.11 and 1.12 give them before
# any listen-only check, but there a remapper's keyboard is a group of its
# own, so they can't come for kanata's either.
REFUSED_ON_A_REAL_KEYBOARD = frozenset({"couldn't hide the keys",
                                        "couldn't hold the keyboard"})

# The protocol's modifier letters, by TuxTalk's modifier names.
_MOD_LETTERS = {"CTRL": "C", "ALT": "A", "SHIFT": "S", "SUPER": "M"}


def socket_path():
    """Where the helper listens. TUXTALK_HOTKEYS_SOCKET is for tests."""
    return os.environ.get("TUXTALK_HOTKEYS_SOCKET", SOCKET_PATH)


def watch_specs(hold_key, toggle_key, esc_key, combos):
    """The WATCH line's specs: the dictation keys and Esc on their own, and
    each (modifier names, key) combo as "CA:25". Space is Left Ctrl+Space,
    asked for as "L:57" so the helper reports it only under Left Ctrl."""
    specs = [f"{LEFT_CTRL}:{code}" if code == KEY_SPACE else str(code)
             for code in (hold_key, toggle_key, esc_key) if code]
    for combo in combos:
        if combo:
            mods, key = combo
            if key:
                letters = "".join(sorted(_MOD_LETTERS[m] for m in mods if m in _MOD_LETTERS))
                specs.append(f"{letters}:{key}" if letters else str(key))
    return specs


def _keyboards_said(words):
    """The count in a hello's "keyboards=<n>", among the words after its
    version, or None when it has none."""
    for word in words:
        key, _, value = word.partition("=")
        if key == "keyboards" and value.isdigit():
            return int(value)
    return None


class HelperKeyboard:
    """The hotkey helper, seen by hotkeys.run as one keyboard."""

    path = "tuxtalk-hotkeys"
    name = "TuxTalk hotkey helper"

    def __init__(self, path=None, connect_timeout=1.0):
        """Not connected yet: connect() says hello to the helper at *path*
        (socket_path() when None), waiting up to *connect_timeout* s."""
        self.socket_path = path or socket_path()
        self.connect_timeout = connect_timeout
        self.sock = None
        self.inbuf = b""
        self.queue = deque()
        self.held = set()
        self.specs = []
        self.to_hide = []
        self.reconnect_at = 0.0
        self.helper_version = None
        # How many keyboards the helper reads, from its hello; None from a
        # helper before 1.12, which doesn't say.
        self.helper_keyboards = None
        # Commands the helper hasn't answered yet (Asked), oldest first: an
        # OK or ERR doesn't say which one it answers; the order does.
        self.asked = deque()
        # The helper said OK to the last HIDE, and no UNHIDE went since.
        self.hidden = False
        self._lock = threading.RLock()
        # Stands in for the socket in select() while disconnected: never readable.
        self._idle_r, self._idle_w = os.pipe()

    # -- connecting ----------------------------------------------------------------

    def connect(self):
        """Connect and say hello. True if the helper answered."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.connect_timeout)
        try:
            sock.connect(self.socket_path)
            sock.sendall(f"HELLO {PROTOCOL}\n".encode())
            reply = b""
            while b"\n" not in reply:
                chunk = sock.recv(256)
                if not chunk:
                    raise ConnectionError("closed")
                reply += chunk
        except (OSError, ConnectionError):
            sock.close()
            return False
        line, _, rest = reply.partition(b"\n")
        words = line.decode("ascii", "replace").split()
        if words[:2] != ["HELLO", str(PROTOCOL)]:
            sock.close()
            return False
        self.helper_version = words[2] if len(words) > 2 else None
        self.helper_keyboards = _keyboards_said(words[3:])
        sock.setblocking(False)
        self.sock, self.inbuf = sock, rest
        self.asked.clear()
        self.hidden = False
        if self.specs:
            self._send("WATCH " + " ".join(self.specs))
        return True

    def _disconnected(self, why):
        logger.warning(f"Hotkey helper connection lost ({why}); reconnecting")
        try:
            self.sock.close()
        except (OSError, AttributeError):
            pass
        self.sock = None
        self.reconnect_at = time.monotonic() + RECONNECT_S
        self.asked.clear()
        self.hidden = False
        # A key it said was down can't come up now: let it go here.
        for code in sorted(self.held):
            self.queue.append(KeyEvent(EV_KEY, code, 0))
        self.held.clear()

    def _send(self, line, codes=(), press=None):
        """Send one command line, noting *codes* and *press* for its answer
        (Asked); False when the helper is gone."""
        with self._lock:
            if self.sock is None:
                return False
            try:
                self.sock.sendall(line.encode() + b"\n")
                self.asked.append(Asked(line.split(" ", 1)[0], codes, press))
                return True
            except BlockingIOError:
                # A handful of bytes to a local socket: only a stuck helper
                # fills the buffer, and then it's as good as gone.
                self._disconnected("not reading")
            except OSError as e:
                self._disconnected(e)
            return False

    def _pump(self):
        """Take in whatever the helper has said, without waiting."""
        with self._lock:
            if self.sock is None:
                if time.monotonic() >= self.reconnect_at:
                    if self.connect():
                        logger.info("Hotkey helper connected again")
                    else:
                        self.reconnect_at = time.monotonic() + RECONNECT_S
                return
            while True:
                try:
                    data = self.sock.recv(4096)
                except BlockingIOError:
                    break
                except OSError as e:
                    self._disconnected(e)
                    return
                if not data:
                    self._disconnected("closed")
                    return
                self.inbuf += data
            while b"\n" in self.inbuf:
                line, self.inbuf = self.inbuf.split(b"\n", 1)
                self._handle(line.decode("ascii", "replace").split())

    def _handle(self, words):
        """Turn one line from the hotkey helper into key events for the
        service's loop (DOWN with the modifiers held, UP), or into the
        answer to a request (OK, ERR)."""
        if not words:
            return
        kind, args = words[0], words[1:]
        try:
            if kind == "DOWN" and args:
                code = int(args[0])
                held_mods = args[1] if len(args) > 1 else "-"
                mods = [] if held_mods == "-" else [int(m) for m in held_mods.split(",")]
                # Modifiers first, as the keyboard would have sent them, so the
                # loop's held_modifiers has them when the key itself arrives.
                for mod in mods:
                    if mod not in self.held:
                        self.held.add(mod)
                        self.queue.append(KeyEvent(EV_KEY, mod, 1))
                self.held.add(code)
                self.queue.append(KeyEvent(EV_KEY, code, 1))
            elif kind == "UP" and args:
                code = int(args[0])
                if code in self.held:
                    self.held.discard(code)
                    self.queue.append(KeyEvent(EV_KEY, code, 0))
            elif kind in ("OK", "ERR"):
                self._answered(kind == "OK", args)
        except ValueError:
            logger.warning(f"Hotkey helper said something odd: {' '.join(words)!r}")

    def _answered(self, ok, args):
        """An OK or ERR, the answer to the oldest command not answered yet.
        An OK names its command: one that doesn't means the answers are out
        of step, and isn't taken as a hide."""
        asked = self.asked.popleft() if self.asked else Asked(None)
        named = args[:1] == [asked.command]
        if ok and not named:
            logger.warning(f"Hotkey helper answered OK {' '.join(args)} to {asked.command}")
        if asked.command == "HIDE":
            if ok and named:
                # The desktop was told they're up. The answers come in the
                # order asked, so this one is about the keys and the press
                # its HIDE noted, however late it comes.
                modifier_hotkeys.note_compose_hidden(asked.codes, asked.press)
            elif not ok and " ".join(args) in REFUSED_ON_A_REAL_KEYBOARD:
                modifier_hotkeys.note_compose_shown(asked.codes, asked.press)
            # An UNHIDE sent since has undone it already.
            self.hidden = ok and named and all(a.command != "UNHIDE" for a in self.asked)
        if not ok and args[:1] == ["listen-only"]:
            # A remapper's keyboard (kanata), never held: its keys reach the
            # desktop as they are, as that setup means them to.
            logger.info(f"Hotkey helper didn't hide the key: {' '.join(args)}")
        elif not ok:
            logger.warning(f"Hotkey helper refused {asked.command}: {' '.join(args)}")

    # -- what hotkeys.run calls -------------------------------------------------

    def fileno(self):
        sock = self.sock    # once: a typing thread may drop it meanwhile
        return sock.fileno() if sock is not None else self._idle_r

    @property
    def fd(self):
        return self.fileno()

    def needs_service(self):
        """Ready without select(): events taken in while answering another
        call, or a reconnect that's due."""
        return bool(self.queue) or (self.sock is None and time.monotonic() >= self.reconnect_at)

    def read(self):
        with self._lock:
            self._pump()
            if not self.queue:
                raise BlockingIOError
            events = list(self.queue)
            self.queue.clear()
            return iter(events)

    def active_keys(self):
        with self._lock:
            self._pump()
            return sorted(self.held)

    @property
    def connected(self):
        """Whether the helper is answering now; while it isn't, no key is
        heard through it (it's tried again every second)."""
        return self.sock is not None

    def held_now(self):
        """The keys the helper last said are down, without asking it again
        or reconnecting, so nothing waits: None while it isn't connected, or
        while another thread has its connection (a reconnect can take a
        second)."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return None if self.sock is None else frozenset(self.held)
        finally:
            self._lock.release()

    def capabilities(self, verbose=False):
        """Enough for keyboard_devices.is_keyboard_device: letter keys, no pointer."""
        from .hotkey_helper import LETTER_KEYS
        return {EV_KEY: sorted(LETTER_KEYS)}

    def write(self, event_type, code, value):
        with self._lock:
            if event_type != EV_KEY:
                return
            # Echo it, as the kernel would, for keyboard_grab.is_own_injected_event to skip.
            self.queue.append(KeyEvent(EV_KEY, code, value))
            if value == 0:
                self.to_hide.append(code)

    def grab(self):
        """Ask the helper to hide the keys the desktop was just told went up
        (write); *hidden* turns True once it has. None of them, when they
        were all up already: nothing is asked, so a held key nobody asked
        to hide (a Right Ctrl dictation key held with another modifier)
        stays the desktop's."""
        with self._lock:
            codes, self.to_hide = self.to_hide, []
            self.hidden = False
            if self.sock is None:
                raise OSError("the hotkey helper isn't connected")
            if not codes:
                return
            if not self._send("HIDE " + " ".join(map(str, codes)), frozenset(codes),
                              modifier_hotkeys.compose_press()):
                raise OSError("the hotkey helper went away")

    def await_hide(self, press, timeout):
        """Take in what the helper says until it has answered the HIDE sent
        for Compose press *press* (Asked), or for *timeout* s: the key was
        found up before the answer, by the hotkey loop, which holds every
        keyboard grabbed meanwhile, or by typing. Not while the helper is
        gone: no answer will come, and reconnecting can take a second."""
        give_up_at = time.monotonic() + timeout
        while True:
            with self._lock:
                if self.sock is None:
                    return
                self._pump()
                if all(a.press != press or a.command != "HIDE" for a in self.asked):
                    return
            if time.monotonic() >= give_up_at:
                logger.info("Hotkey helper didn't answer in time whether it hid the "
                            "Compose key; its compose is left")
                return
            time.sleep(0.005)

    def ungrab(self):
        with self._lock:
            self.hidden = False
            self._send("UNHIDE")

    def watch(self, specs):
        """Tell the helper which keys to report (again after a settings change)."""
        self.specs = list(specs)
        self._send("WATCH " + " ".join(self.specs))

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        for fd in (self._idle_r, self._idle_w):
            try:
                os.close(fd)
            except OSError:
                pass


def connect_helper(path=None):
    """A HelperKeyboard when the helper is running, or installed and this
    user can't open a keyboard; else None (TuxTalk then reads the keyboards
    itself, as before).

    A helper that doesn't answer at once (restarting, stopped, or this
    session isn't the one at the screen yet) is still used: the
    HelperKeyboard keeps trying every second, so Update in System check
    brings the hotkeys back without a restart. With the helper installed
    the user is normally out of the input group, and reading the keyboards
    here would leave the hotkeys dead until TuxTalk restarts. Someone still
    in it (they kept the group when switching, or couldn't leave it) reads
    them as before while the helper's socket isn't there."""
    from .uinput_helper import helper_expected
    path = path or socket_path()
    if not helper_expected(path):
        return None
    keyboard = HelperKeyboard(path)
    if not keyboard.connect():
        logger.info("The hotkey helper didn't answer yet; trying again every second")
        keyboard.reconnect_at = time.monotonic() + RECONNECT_S
    return keyboard


def refused_shortcut(text):
    """True when a shortcut written like "Ctrl+Shift+H" won't be reported by
    the helper (refused_combo). One the service can't read either is left
    to the service to report."""
    from .hotkey_keys import parse_hotkey_combo
    combo = parse_hotkey_combo(text)
    return bool(combo and refused_combo(*combo))


def refused_combo(mods, key):
    """Why the helper won't report this (modifier names, key) combo, or None.
    It reports a key only with two of Ctrl, Alt and Super held."""
    from .hotkey_helper import parse_spec
    spec = watch_specs(None, None, None, [(mods, key)])
    if not spec:
        return None
    try:
        parse_spec(spec[0])
        return None
    except ValueError as e:
        return str(e)
