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

from .logger import setup_logger

logger = setup_logger(__name__)

SOCKET_PATH = "/run/tuxtalk-hotkeys/hotkeys.sock"
PROTOCOL = 1
RECONNECT_S = 1.0

EV_KEY, EV_SYN = 1, 0

# What the loop reads: evdev.InputEvent's three fields that it uses.
KeyEvent = namedtuple("KeyEvent", "type code value")

# The protocol's modifier letters, by TuxTalk's modifier names.
_MOD_LETTERS = {"CTRL": "C", "ALT": "A", "SHIFT": "S", "SUPER": "M"}


def socket_path():
    """Where the helper listens. TUXTALK_HOTKEYS_SOCKET is for tests."""
    return os.environ.get("TUXTALK_HOTKEYS_SOCKET", SOCKET_PATH)


def watch_specs(hold_key, toggle_key, esc_key, combos):
    """The WATCH line's specs: the dictation keys and Esc on their own, and
    each (modifier names, key) combo as "CA:25"."""
    specs = [str(code) for code in (hold_key, toggle_key, esc_key) if code]
    for combo in combos:
        if combo:
            mods, key = combo
            if key:
                letters = "".join(sorted(_MOD_LETTERS[m] for m in mods if m in _MOD_LETTERS))
                specs.append(f"{letters}:{key}" if letters else str(key))
    return specs


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
        # Commands the helper hasn't answered yet, oldest first: an OK or
        # ERR doesn't say which one it answers.
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

    def _send(self, line):
        """Send one command line; False when the helper is gone."""
        with self._lock:
            if self.sock is None:
                return False
            try:
                self.sock.sendall(line.encode() + b"\n")
                self.asked.append(line.split(" ", 1)[0])
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
        asked = self.asked.popleft() if self.asked else None
        named = args[:1] == [asked]
        if ok and not named:
            logger.warning(f"Hotkey helper answered OK {' '.join(args)} to {asked}")
        if asked == "HIDE":
            # An UNHIDE sent since has undone it already.
            self.hidden = ok and named and "UNHIDE" not in self.asked
        if not ok:
            logger.warning(f"Hotkey helper refused {asked}: {' '.join(args)}")

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
        """Enough for uinput_helper.is_keyboard_device: letter keys, no pointer."""
        from .uinput_helper import LETTER_KEYS
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
        """Ask the helper to hide the keys; *hidden* turns True once it has."""
        with self._lock:
            codes, self.to_hide = self.to_hide or sorted(self.held), []
            self.hidden = False
            if self.sock is None:
                raise OSError("the hotkey helper isn't connected")
            if not self._send("HIDE " + " ".join(map(str, codes))):
                raise OSError("the hotkey helper went away")

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
    """A HelperKeyboard when the helper is running, or None when it isn't
    (TuxTalk then reads the keyboards itself, as before).

    A running helper that doesn't answer at once (restarting, or this
    session isn't the one at the screen yet) is still used: the
    HelperKeyboard keeps trying every second. Falling back for good would
    leave the hotkeys dead until TuxTalk restarts."""
    path = path or socket_path()
    if not os.path.exists(path):
        return None
    keyboard = HelperKeyboard(path)
    if not keyboard.connect():
        logger.info("The hotkey helper didn't answer yet; trying again every second")
        keyboard.reconnect_at = time.monotonic() + RECONNECT_S
    return keyboard


def refused_shortcut(text):
    """True when a shortcut written like "Ctrl+Shift+H" won't be reported by
    the helper: a key that isn't an F-key needs two of Ctrl, Alt and Super.
    (Text only, so Preferences needn't load the dictation service's code.)"""
    parts = [p.strip().upper() for p in (text or "").split("+") if p.strip()]
    if len(parts) < 2:
        return False
    key, mods = parts[-1], set(parts[:-1])
    if key.startswith("F") and key[1:].isdigit():
        return False
    return len(mods & {"CTRL", "ALT", "SUPER"}) < 2


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
