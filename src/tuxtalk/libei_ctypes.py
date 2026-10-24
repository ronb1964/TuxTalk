"""libei access via ctypes — the sandbox-friendly way to emulate keystrokes.

Fedora (and the GNOME Flatpak runtime) ship libei.so.1 but no `Ei` GObject-
Introspection typelib, so we drive the C library directly with ctypes.

libei ctypes rules:
- set restype/argtypes on every non-variadic call or 64-bit pointers truncate;
- ei_seat_bind_capabilities is variadic — pass explicit ctypes objects.
"""
import ctypes as C
import os
import select
import time
import unicodedata

from .key_commands import KEY_CODES
from .logger import setup_logger

_log = setup_logger(__name__)

# --- libei enums (from upstream libei.h) ---
EI_EVENT_SEAT_ADDED = 3
EI_EVENT_DEVICE_ADDED = 5
EI_EVENT_DEVICE_PAUSED = 7
EI_EVENT_DEVICE_RESUMED = 8
EI_DEVICE_CAP_KEYBOARD = 1 << 2  # = 4

# --- evdev keycodes (linux/input-event-codes.h) ---
KEY_LEFTSHIFT = 42
KEY_LEFTCTRL = 29
KEY_BACKSPACE = 14
KEY_A = 30
KEY_SPACE = 57
KEY_ENTER = 28
# Names voice_navigation and key_commands use -> key codes, for press_chords;
# letters at their places on a US keyboard, like all typing here.
_CHORD_KEYS = {**KEY_CODES, "A": KEY_A, "E": 18, "U": 22}

# Soft-line-break marker used across TuxTalk (§SHIFT_ENTER§): type Shift+Enter,
# not plain Enter, so it doesn't submit in chat apps.
SHIFT_ENTER_MARKER = "\xa7SHIFT_ENTER\xa7"

_LETTERS = {
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
    "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
    "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
}
_DIGITS = {"1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
           "6": 7, "7": 8, "8": 9, "9": 10, "0": 11}
# Unshifted punctuation (US layout) + space.
_PUNCT = {
    "-": 12, "=": 13, "[": 26, "]": 27, ";": 39, "'": 40, "`": 41, "\\": 43,
    ",": 51, ".": 52, "/": 53, " ": KEY_SPACE, "\t": 15,
}
# Shifted characters: char -> the base keycode you hold Shift with.
_SHIFTED = {
    "!": 2, "@": 3, "#": 4, "$": 5, "%": 6, "^": 7, "&": 8, "*": 9, "(": 10,
    ")": 11, "_": 12, "+": 13, "{": 26, "}": 27, ":": 39, '"': 40, "~": 41,
    "|": 43, "<": 51, ">": 52, "?": 53,
}

# char -> base evdev keycode, for the printable ASCII a US layout types.
CHAR_TO_KEYCODE: dict[str, int] = {}
CHAR_TO_KEYCODE.update(_LETTERS)
CHAR_TO_KEYCODE.update(_DIGITS)
CHAR_TO_KEYCODE.update(_PUNCT)
CHAR_TO_KEYCODE.update(_SHIFTED)
for _c, _k in list(_LETTERS.items()):
    CHAR_TO_KEYCODE[_c.upper()] = _k  # uppercase shares the lowercase keycode

# Characters that require holding Shift while the base key is pressed.
SHIFT_CHARS: set[str] = set(_SHIFTED) | {c.upper() for c in _LETTERS}

# Typographic characters dictation produces (smart quotes are on by default,
# and "..." becomes an ellipsis), typed as their plain look-alikes.
_PLAIN_LOOKALIKE = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u2026": "...",
}


def load_libei() -> C.CDLL:
    """Load libei.so.1 with all the argtypes/restypes set. Raises OSError if the
    library is not present."""
    for name in ("libei.so.1", "libei.so"):
        try:
            ei = C.CDLL(name)
            break
        except OSError:
            ei = None
    if ei is None:
        raise OSError("libei.so.1 not found")
    ei.ei_new_sender.restype = C.c_void_p
    ei.ei_new_sender.argtypes = [C.c_void_p]
    ei.ei_setup_backend_fd.restype = C.c_int
    ei.ei_setup_backend_fd.argtypes = [C.c_void_p, C.c_int]
    ei.ei_dispatch.argtypes = [C.c_void_p]
    ei.ei_get_event.restype = C.c_void_p
    ei.ei_get_event.argtypes = [C.c_void_p]
    ei.ei_get_fd.restype = C.c_int
    ei.ei_get_fd.argtypes = [C.c_void_p]
    ei.ei_now.restype = C.c_uint64
    ei.ei_now.argtypes = [C.c_void_p]
    ei.ei_event_get_type.restype = C.c_int
    ei.ei_event_get_type.argtypes = [C.c_void_p]
    ei.ei_event_get_seat.restype = C.c_void_p
    ei.ei_event_get_seat.argtypes = [C.c_void_p]
    ei.ei_event_get_device.restype = C.c_void_p
    ei.ei_event_get_device.argtypes = [C.c_void_p]
    ei.ei_event_unref.restype = C.c_void_p
    ei.ei_event_unref.argtypes = [C.c_void_p]
    # ei_seat_bind_capabilities is variadic (sentinel NULL) -> no argtypes.
    ei.ei_device_start_emulating.argtypes = [C.c_void_p, C.c_uint32]
    ei.ei_device_keyboard_key.argtypes = [C.c_void_p, C.c_uint32, C.c_bool]
    ei.ei_device_frame.argtypes = [C.c_void_p, C.c_uint64]
    ei.ei_device_stop_emulating.argtypes = [C.c_void_p]
    ei.ei_unref.restype = C.c_void_p
    ei.ei_unref.argtypes = [C.c_void_p]
    try:  # device ref/unref keep the device alive while a session uses it (optional)
        ei.ei_device_ref.restype = C.c_void_p
        ei.ei_device_ref.argtypes = [C.c_void_p]
        ei.ei_device_unref.restype = C.c_void_p
        ei.ei_device_unref.argtypes = [C.c_void_p]
    except AttributeError:
        pass
    return ei


def typeable_text(text: str) -> str:
    """*text* as this keyboard path will type it: typographic characters
    become their plain look-alikes and accented letters lose the accent
    ("café" -> "cafe"). The caller records this form for undo, since "…" is
    three keystrokes. Characters with no ASCII equivalent are left as they
    are, and plan_keys refuses them."""
    out = []
    for ch in text:
        if ch in CHAR_TO_KEYCODE or ch in "\n\t":
            out.append(ch)
        elif ch in _PLAIN_LOOKALIKE:
            out.append(_PLAIN_LOOKALIKE[ch])
        else:
            # Canonical decomposition only, so "²" stays "²" rather than
            # silently becoming "2".
            base = "".join(c for c in unicodedata.normalize("NFD", ch)
                           if not unicodedata.combining(c))
            out.append(base if base and all(c in CHAR_TO_KEYCODE for c in base) else ch)
    return "".join(out)


def plan_keys(text: str):
    """[(keycode, shift)] that type *text*, or None if any character can't be
    typed. Planned before anything is sent, so a dictation is typed whole or
    not at all: a silently skipped character would leave undo deleting more
    than was typed."""
    text = typeable_text(text)
    keys = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith(SHIFT_ENTER_MARKER, i):
            keys.append((KEY_ENTER, True))          # soft line break
            i += len(SHIFT_ENTER_MARKER)
            continue
        ch = text[i]
        if ch == "\n":
            keys.append((KEY_ENTER, False))         # hard newline
        else:
            kc = CHAR_TO_KEYCODE.get(ch)
            if kc is None:
                return None
            keys.append((kc, ch in SHIFT_CHARS))
        i += 1
    return keys


class LibeiSession:
    """Wraps an EIS connection (fd from the RemoteDesktop portal's ConnectToEIS)
    and types text through it.

    The session owns *fd* from the moment it is made: libei closes it with the
    context. Use it as a context manager (or call close()) so the context and
    the device references are freed."""

    def __init__(self, fd: int):
        try:
            self.ei = load_libei()
            self.ctx = self.ei.ei_new_sender(None)
            if not self.ctx:
                raise RuntimeError("ei_new_sender returned NULL")
        except BaseException:
            os.close(fd)                    # never handed to libei
            raise
        rc = self.ei.ei_setup_backend_fd(self.ctx, fd)
        if rc != 0:
            self.ei.ei_unref(self.ctx)
            self.ctx = None
            raise RuntimeError(f"ei_setup_backend_fd failed rc={rc}")
        self.fd_ei = self.ei.ei_get_fd(self.ctx)
        self.device = None
        # Every device this session took a reference on, released by close()
        # (a PAUSED event clears self.device but not the reference).
        self._refs = []
        # start_emulating takes a sequence number that must be unique/increasing
        # per call, or the compositor ignores every emulation after the first.
        self._seq = 0

    def _handle_event(self, ev):
        """Process one libei event: bind the keyboard on SEAT_ADDED, track the
        device on RESUMED/PAUSED."""
        etype = self.ei.ei_event_get_type(ev)
        if etype == EI_EVENT_SEAT_ADDED:
            seat = self.ei.ei_event_get_seat(ev)
            # variadic call: explicit ctypes objects or the 64-bit seat pointer
            # truncates to 32 bits -> SIGSEGV.
            self.ei.ei_seat_bind_capabilities(
                C.c_void_p(seat), C.c_uint(EI_DEVICE_CAP_KEYBOARD), C.c_void_p(0))
        elif etype == EI_EVENT_DEVICE_RESUMED:
            dev = self.ei.ei_event_get_device(ev)
            if hasattr(self.ei, "ei_device_ref") and dev not in self._refs:
                self.ei.ei_device_ref(dev)  # keep it alive
                self._refs.append(dev)
            self.device = dev
        elif etype == EI_EVENT_DEVICE_PAUSED:
            self.device = None

    def close(self):
        """Free the device references and the libei context (which closes the
        EIS socket). Safe to call twice."""
        if hasattr(self.ei, "ei_device_unref"):
            for dev in self._refs:
                self.ei.ei_device_unref(dev)
        self._refs = []
        self.device = None
        if self.ctx:
            self.ei.ei_unref(self.ctx)
            self.ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _drain(self):
        """Dispatch pending libei traffic and handle every queued event, which
        binds the keyboard and keeps self.device current."""
        self.ei.ei_dispatch(self.ctx)
        while True:
            ev = self.ei.ei_get_event(self.ctx)
            if not ev:
                break
            self._handle_event(ev)
            self.ei.ei_event_unref(ev)

    def pump_until_ready(self, timeout: float = 10.0) -> bool:
        """Block (via select) until the keyboard device is ready to emulate,
        servicing the connection meanwhile. False if *timeout* runs out."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.device is None:
            select.select([self.fd_ei], [], [], 0.5)
            self._drain()
        if self.device is None:
            _log.warning("libei device did not become ready within %.0fs", timeout)
            return False
        return True

    def _emit_key(self, keycode: int, shift: bool):
        """Press (and release) one key, optionally with Shift held."""
        ei, ctx, dev = self.ei, self.ctx, self.device
        if shift:
            ei.ei_device_keyboard_key(dev, KEY_LEFTSHIFT, True)
            ei.ei_device_frame(dev, ei.ei_now(ctx))
        ei.ei_device_keyboard_key(dev, keycode, True)
        ei.ei_device_frame(dev, ei.ei_now(ctx))
        ei.ei_device_keyboard_key(dev, keycode, False)
        ei.ei_device_frame(dev, ei.ei_now(ctx))
        if shift:
            ei.ei_device_keyboard_key(dev, KEY_LEFTSHIFT, False)
            ei.ei_device_frame(dev, ei.ei_now(ctx))

    def _emit_modified(self, modifier_kc: int, keycode: int):
        """Press `keycode` with a modifier (e.g. Ctrl) held, then release both.
        Used for editing combos like Ctrl+A that plain typing can't express."""
        ei, ctx, dev = self.ei, self.ctx, self.device
        ei.ei_device_keyboard_key(dev, modifier_kc, True)
        ei.ei_device_frame(dev, ei.ei_now(ctx))
        ei.ei_device_keyboard_key(dev, keycode, True)
        ei.ei_device_frame(dev, ei.ei_now(ctx))
        ei.ei_device_keyboard_key(dev, keycode, False)
        ei.ei_device_frame(dev, ei.ei_now(ctx))
        ei.ei_device_keyboard_key(dev, modifier_kc, False)
        ei.ei_device_frame(dev, ei.ei_now(ctx))

    def _run_emitting(self, emit) -> bool:
        """Shared wrapper for every emission: service the connection, ensure a
        device is ready, then run `emit()` inside one start/stop-emulating frame
        and flush the socket. Returns False if no device became ready (never
        raises). type_string and the editing key-combos all go through here so
        the device-readiness and flush handling live in exactly one place."""
        # Service the connection and refresh device state right before emitting.
        self._drain()
        if self.device is None and not self.pump_until_ready(timeout=2.0):
            _log.warning("[libei] no device ready after drain")
            return False
        ei, ctx, dev = self.ei, self.ctx, self.device
        self._seq += 1
        ei.ei_device_start_emulating(dev, self._seq)
        emit()
        ei.ei_device_stop_emulating(dev)
        # Keep dispatching briefly so the outgoing frames flush to the socket.
        for _ in range(6):
            ei.ei_dispatch(ctx)
            time.sleep(0.05)
        return True

    def type_string(self, text: str) -> bool:
        """Type `text` via libei. Handles the §SHIFT_ENTER§ soft-break marker
        (Shift+Enter) and plain newlines (Enter). Returns False, typing
        nothing, if a character can't be typed (only a US layout's ASCII can)
        or no device became ready, so the caller shows its usual notice."""
        keys = plan_keys(text)
        if keys is None:
            _log.warning("[libei] text holds characters this keyboard path can't type; "
                         "nothing was typed")
            return False

        def emit():
            for keycode, shift in keys:
                self._emit_key(keycode, shift)

        return self._run_emitting(emit)

    def select_all_delete(self) -> bool:
        """Ctrl+A then Backspace — wipe the focused field. The libei counterpart
        of app._send_select_all_delete, used off-Flatpak, so the 'delete
        everything' voice command works inside the sandbox too."""
        def emit():
            self._emit_modified(KEY_LEFTCTRL, KEY_A)
            self._emit_key(KEY_BACKSPACE, shift=False)

        return self._run_emitting(emit)

    def press_chords(self, chords) -> bool:
        """Press [(modifiers, key)] by name, each modifier held around its key:
        select and move by voice inside the sandbox (voice_navigation.py).
        Key codes are a US layout's, like the rest of this path."""
        def emit():
            ei, ctx, dev = self.ei, self.ctx, self.device

            def key(code, down):
                ei.ei_device_keyboard_key(dev, code, down)
                ei.ei_device_frame(dev, ei.ei_now(ctx))

            for mods, name in chords:
                mod_codes = [_CHORD_KEYS[m] for m in mods]
                for c in mod_codes:
                    key(c, True)
                key(_CHORD_KEYS[name], True)
                key(_CHORD_KEYS[name], False)
                for c in reversed(mod_codes):
                    key(c, False)

        return self._run_emitting(emit)

    def backspaces(self, count: int) -> bool:
        """Send `count` Backspace presses — character-level undo over libei."""
        def emit():
            for _ in range(max(0, count)):
                self._emit_key(KEY_BACKSPACE, shift=False)

        return self._run_emitting(emit)
