"""Ask the compositor which window has focus: Sway, i3, Hyprland, niri, COSMIC
and X11.

Paste sends Ctrl+Shift+V to terminals (plain Ctrl+V is readline's
quoted-insert there and shows nothing), and some Electron apps get typed
instead of pasted. Both need the focused window's class; typing that waited,
and an automatic send, need to know which window it is too. GNOME's
extension and KDE's KWin script push both to the tray; these compositors
have nothing to push them, but each answers the question over a local
socket, so the dictation service asks them when nobody has reported it
(focused_window.py).

  Sway / i3   i3 IPC on $SWAYSOCK or $I3SOCK: GET_TREE, then the focused node.
              A native Wayland window has an app_id; an XWayland one has the
              X11 class instead.
  Hyprland    $XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock,
              request "j/activewindow" (as hyprctl sends it); Hyprland replies
              and hangs up. It serves this socket synchronously and freezes
              while a connection stays open, so every request here is one
              short connection with a short timeout.
  niri        $NIRI_SOCKET, one line of JSON each way: "FocusedWindow".
  COSMIC      the Wayland socket itself: the window list and which window is
              "activated" (below).
  X11         XFCE, MATE, Cinnamon, LXQt, Openbox and the rest: the window
              manager names the focused window in the root window's
              _NET_ACTIVE_WINDOW (EWMH), and WM_CLASS gives its class. Only in
              an X11 session: under Wayland the service has a DISPLAY too
              (XWayland, for the recording indicator), but XWayland only knows
              the X11 apps and could name a window that doesn't have focus.
              Asked through XCB, not Xlib: Xlib reports a vanished window
              through a process-wide error handler whose default exits the
              program, and replacing it would fight GTK's own (the recording
              indicator runs GTK on X11 in this same process). XCB returns
              errors per request.

It's a hint for picking the paste shortcut, so anything going wrong means
"don't know" (None), quickly, and never an error.
"""
import ctypes
import json
import os
import socket
import struct
import threading

from . import desktop_detect
from .logger import FailureNotes, setup_logger
from .single_instance import session_runtime_dir

logger = setup_logger(__name__)
_failures = FailureNotes()      # asked before every typed chunk: each failure logged once

TIMEOUT = 0.25      # seconds; these answer in about a millisecond

_I3_MAGIC = b"i3-ipc"
_I3_GET_TREE = 4


def _exchange(path, request, read_reply):
    """Connect to *path*, send *request*, return read_reply(sock); always closes."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(TIMEOUT)
        sock.connect(path)
        sock.sendall(request)
        return read_reply(sock)


def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("the compositor hung up mid-reply")
        data += chunk
    return data


def _recv_until_closed(sock):
    chunks = []
    while chunk := sock.recv(65536):
        chunks.append(chunk)
    return b"".join(chunks)


def _recv_line(sock):
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    return data


# --- Sway and i3 ---------------------------------------------------------------------

def _i3_reply(sock):
    head = _recv_exact(sock, 14)
    if head[:6] != _I3_MAGIC:
        raise ValueError("not an i3 IPC reply")
    length, _kind = struct.unpack("<II", head[6:])
    return json.loads(_recv_exact(sock, length))


def _find_focused(node):
    if node.get("focused"):
        return node
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        found = _find_focused(child)
        if found:
            return found
    return None


def _from_i3(path):
    tree = _exchange(path, _I3_MAGIC + struct.pack("<II", 0, _I3_GET_TREE), _i3_reply)
    node = _find_focused(tree)
    if not node or node.get("type") not in ("con", "floating_con"):
        return None, None       # a workspace or output has focus, not a window
    return (node.get("app_id") or (node.get("window_properties") or {}).get("class"),
            node.get("id"))


# --- Hyprland ------------------------------------------------------------------------

def hyprland_socket(signature):
    """The command socket of the Hyprland session *signature*."""
    return os.path.join(session_runtime_dir(), "hypr", signature, ".socket.sock")


def _from_hyprland(signature):
    window = json.loads(_exchange(hyprland_socket(signature), b"j/activewindow",
                                  _recv_until_closed) or b"{}")
    return window.get("class") or window.get("initialClass"), window.get("address")


# --- niri ----------------------------------------------------------------------------

def _from_niri(path):
    reply = json.loads(_exchange(path, b'"FocusedWindow"\n', _recv_line))
    window = (reply.get("Ok") or {}).get("FocusedWindow") or {}
    return window.get("app_id"), window.get("id")


# --- COSMIC --------------------------------------------------------------------------
# No IPC socket and no command to ask, but cosmic-comp offers every client the
# window list over Wayland itself: ext_foreign_toplevel_list_v1 names each
# window's app_id, and zcosmic_toplevel_info_v1 (version 2 and up) adds its
# state, "activated" for the focused one. That's what COSMIC's own dock uses.
# The wire format is simple enough to speak directly on the socket, as the other
# compositors' are above, so no Wayland binding is needed. Opcodes are each
# message's position among its interface's requests or events in the protocol
# XML (cosmic-protocols, wayland-protocols).
#
# Unlike the others, this isn't asked afresh each time: cosmic-comp sends the
# states on a later pass, about 0.3 s after they're asked for (cosmic-comp
# 2026-10, Pop!_OS 24.04), which every paste would wait for. One connection
# stays open instead and follows focus as it changes.

_WL_DISPLAY = 1
_WL_DISPLAY_SYNC, _WL_DISPLAY_GET_REGISTRY = 0, 1           # requests
_WL_DISPLAY_ERROR = 0                                       # event
_WL_REGISTRY_BIND = 0                                       # request
_WL_REGISTRY_GLOBAL = 0                                     # event
_WL_CALLBACK_DONE = 0                                       # event
_EXT_LIST_TOPLEVEL = 0                                      # event
_EXT_HANDLE_DESTROY = 0                                     # request
_EXT_HANDLE_CLOSED, _EXT_HANDLE_APP_ID = 0, 3               # events
_COSMIC_INFO_GET_COSMIC_TOPLEVEL = 1                        # request, since 2
_COSMIC_INFO_DONE = 2                                       # event, since 2
_COSMIC_HANDLE_DESTROY = 0                                  # request
_COSMIC_HANDLE_STATE = 8                                    # event
_COSMIC_STATE_ACTIVATED = 2
_EXT_LIST = "ext_foreign_toplevel_list_v1"
_COSMIC_INFO = "zcosmic_toplevel_info_v1"

# How long the first question waits for the window states to arrive.
COSMIC_FIRST_ANSWER = 1.0


def _wl_string(text):
    raw = text.encode() + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _wl_read_string(body, offset=0):
    """(the string at *offset*, the offset after it)."""
    length = struct.unpack_from("<I", body, offset)[0]
    text = body[offset + 4:offset + 4 + length - 1].decode("utf-8", "replace")
    return text, offset + 4 + length + (-length % 4)


class _Wayland:
    """A Wayland connection at the wire level: requests out, events in."""

    def __init__(self, sock):
        self.sock = sock
        self.next_id = 2            # 1 is wl_display
        self.unread = b""

    def new_id(self):
        self.next_id += 1
        return self.next_id - 1

    def request(self, obj, opcode, payload=b""):
        self.sock.sendall(struct.pack("<IHH", obj, opcode, 8 + len(payload)) + payload)

    def roundtrip(self, on_event):
        """Call on_event(object, opcode, body) for every event until the
        compositor has answered everything sent so far."""
        callback = self.new_id()
        self.request(_WL_DISPLAY, _WL_DISPLAY_SYNC, struct.pack("<I", callback))
        self.read_until(callback, _WL_CALLBACK_DONE, on_event)

    def read_until(self, obj, opcode, on_event):
        """Call on_event(object, opcode, body) for every event until *obj*
        sends event *opcode* (never, for obj None: until the connection ends)."""
        while True:
            while len(self.unread) < 8 or len(self.unread) < self._size():
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise ConnectionError("the compositor hung up")
                self.unread += chunk
            size = self._size()
            if size < 8:
                raise ConnectionError(f"a message of {size} bytes")   # would never end
            sender, event = struct.unpack_from("<IH", self.unread)
            body, self.unread = self.unread[8:size], self.unread[size:]
            if (sender, event) == (obj, opcode):
                return
            if sender == _WL_DISPLAY and event == _WL_DISPLAY_ERROR:
                raise ConnectionError(f"protocol error: {_wl_read_string(body, 8)[0]}")
            on_event(sender, event, body)

    def _size(self):
        return struct.unpack_from("<H", self.unread, 6)[0]


class _CosmicWindows(threading.Thread):
    """Follows COSMIC's windows on its own connection: their app_ids, and
    which one is activated."""

    def __init__(self, path):
        super().__init__(daemon=True, name="tt-cosmic-focus")
        self.path = path
        self.app_ids = {}           # each window's ext handle -> its app_id
        self.windows = {}           # cosmic handle -> ext handle
        self.active = None          # the activated window's ext handle
        self.ready = threading.Event()  # the first states are in
        self.not_offered = False    # this cosmic-comp has no window list to follow

    def focused(self):
        return self.app_ids.get(self.active)

    def run(self):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(TIMEOUT)
                sock.connect(self.path)
                self._follow(_Wayland(sock))
        except Exception as e:      # whatever ends it, the focus is unknown from here on
            logger.info(f"Stopped following COSMIC's windows: {e}")
        finally:
            self.active = None
            self.ready.set()        # nobody waits for an answer that won't come

    def _follow(self, wl):
        """Bind cosmic-comp's window list on *wl*, take in the windows open
        now, then follow their changes until the connection ends. Raises
        ConnectionError when the list isn't offered."""
        registry = wl.new_id()
        wl.request(_WL_DISPLAY, _WL_DISPLAY_GET_REGISTRY, struct.pack("<I", registry))
        offered = {}                # interface -> (global name, version)

        def on_global(obj, opcode, body):
            if obj == registry and opcode == _WL_REGISTRY_GLOBAL:
                interface, end = _wl_read_string(body, 4)
                offered[interface] = (struct.unpack_from("<I", body)[0],
                                      struct.unpack_from("<I", body, end)[0])
        wl.roundtrip(on_global)
        if _EXT_LIST not in offered or offered.get(_COSMIC_INFO, (0, 0))[1] < 2:
            self.not_offered = True
            raise ConnectionError("cosmic-comp doesn't offer its window list")

        def bind(interface, version):
            new = wl.new_id()
            wl.request(registry, _WL_REGISTRY_BIND, struct.pack("<I", offered[interface][0])
                       + _wl_string(interface) + struct.pack("<II", version, new))
            return new
        self.toplevel_list, self.info = bind(_EXT_LIST, 1), bind(_COSMIC_INFO, 2)
        self.wl = wl
        wl.roundtrip(self._on_event)
        if not self.app_ids:
            self.ready.set()        # no windows, so no states will come
        wl.sock.settimeout(None)    # from here on it waits for changes
        wl.read_until(None, None, self._on_event)

    def _on_event(self, obj, opcode, body):
        """Keep the app ids and the active window up to date from one Wayland
        event: a window opening, naming its app, closing or changing state."""
        if obj == self.toplevel_list and opcode == _EXT_LIST_TOPLEVEL:
            handle = struct.unpack_from("<I", body)[0]
            self.app_ids[handle] = None
            window = self.wl.new_id()
            self.windows[window] = handle
            self.wl.request(self.info, _COSMIC_INFO_GET_COSMIC_TOPLEVEL,
                            struct.pack("<II", window, handle))
        elif obj in self.app_ids and opcode == _EXT_HANDLE_APP_ID:
            self.app_ids[obj] = _wl_read_string(body)[0]
        elif obj in self.app_ids and opcode == _EXT_HANDLE_CLOSED:
            window = next(w for w, h in self.windows.items() if h == obj)
            self.wl.request(window, _COSMIC_HANDLE_DESTROY)
            self.wl.request(obj, _EXT_HANDLE_DESTROY)
            del self.windows[window], self.app_ids[obj]
            if self.active == obj:
                self.active = None
        elif obj in self.windows and opcode == _COSMIC_HANDLE_STATE:
            length = struct.unpack_from("<I", body)[0]
            states = struct.unpack_from(f"={length // 4}I", body, 4)   # native byte order
            if _COSMIC_STATE_ACTIVATED in states:
                self.active = self.windows[obj]
            elif self.active == self.windows[obj]:
                self.active = None
        elif obj == self.info and opcode == _COSMIC_INFO_DONE:
            self.ready.set()


_cosmic = None
_cosmic_lock = threading.Lock()


def _from_cosmic(path):
    """The activated window's app_id, from the connection that follows them,
    made the first time (and again if cosmic-comp went away). A cosmic-comp
    without the window list is asked once, not again at every paste."""
    global _cosmic
    with _cosmic_lock:
        if _cosmic is None or not (_cosmic.is_alive() or _cosmic.not_offered):
            _cosmic = _CosmicWindows(path)
            _cosmic.start()
        windows = _cosmic
    if not windows.ready.wait(COSMIC_FIRST_ANSWER):
        windows.ready.set()         # waited once; later questions take what's known
    return windows.focused()


def _wayland_socket(env):
    name = env.get("WAYLAND_DISPLAY") or "wayland-0"
    return os.path.join(session_runtime_dir(env), name)


# --- X11 ----------------------------------------------------------------------------
# libxcb through ctypes. It's on every X11 system (Xlib itself is built on it),
# so this needs no new dependency. x11_hotkeys uses it too.

_XCB_ATOM_WINDOW = 33           # predefined atoms (xproto)
_XCB_ATOM_WM_CLASS = 67
_XCB_GET_PROPERTY_TYPE_ANY = 0


class _Cookie(ctypes.Structure):
    _fields_ = [("sequence", ctypes.c_uint)]


class _ScreenIterator(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("rem", ctypes.c_int), ("index", ctypes.c_int)]


class _AtomReply(ctypes.Structure):
    _fields_ = [("response_type", ctypes.c_uint8), ("pad0", ctypes.c_uint8),
                ("sequence", ctypes.c_uint16), ("length", ctypes.c_uint32),
                ("atom", ctypes.c_uint32)]


class _Error(ctypes.Structure):
    _fields_ = [("response_type", ctypes.c_uint8), ("error_code", ctypes.c_uint8)]


_xcb = None


def xcb_lib():
    """libxcb with the few signatures used here and in x11_hotkeys, loaded once."""
    global _xcb
    if _xcb is None:
        lib = ctypes.CDLL("libxcb.so.1")
        P, U8, U16, U32 = ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32
        lib.xcb_connect.restype = P
        lib.xcb_connect.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
        lib.xcb_connection_has_error.argtypes = [P]
        lib.xcb_disconnect.argtypes = [P]
        lib.xcb_get_setup.restype = P
        lib.xcb_get_setup.argtypes = [P]
        lib.xcb_setup_roots_iterator.restype = _ScreenIterator
        lib.xcb_setup_roots_iterator.argtypes = [P]
        lib.xcb_screen_next.argtypes = [ctypes.POINTER(_ScreenIterator)]
        lib.xcb_intern_atom.restype = _Cookie
        lib.xcb_intern_atom.argtypes = [P, U8, U16, ctypes.c_char_p]
        lib.xcb_intern_atom_reply.restype = ctypes.POINTER(_AtomReply)
        lib.xcb_intern_atom_reply.argtypes = [P, _Cookie, ctypes.POINTER(P)]
        lib.xcb_get_property.restype = _Cookie
        lib.xcb_get_property.argtypes = [P, U8, U32, U32, U32, U32, U32]
        lib.xcb_get_property_reply.restype = P
        lib.xcb_get_property_reply.argtypes = [P, _Cookie, ctypes.POINTER(P)]
        lib.xcb_get_property_value.restype = P
        lib.xcb_get_property_value.argtypes = [P]
        lib.xcb_get_property_value_length.argtypes = [P]
        lib.xcb_grab_key_checked.restype = _Cookie
        lib.xcb_grab_key_checked.argtypes = [P, U8, U32, U16, U8, U8, U8]
        lib.xcb_ungrab_key.restype = _Cookie
        lib.xcb_ungrab_key.argtypes = [P, U8, U32, U16]
        lib.xcb_request_check.restype = ctypes.POINTER(_Error)
        lib.xcb_request_check.argtypes = [P, _Cookie]
        lib.xcb_ungrab_keyboard.restype = _Cookie
        lib.xcb_ungrab_keyboard.argtypes = [P, U32]
        lib.xcb_wait_for_event.restype = P
        lib.xcb_wait_for_event.argtypes = [P]
        lib.xcb_flush.argtypes = [P]
        _xcb = lib
    return _xcb


free = ctypes.CDLL(None).free
free.argtypes = [ctypes.c_void_p]


def _x11_property(lib, conn, window, atom):
    """Raw bytes of *atom* on *window*, or None (an error, or not set)."""
    error = ctypes.c_void_p()
    cookie = lib.xcb_get_property(conn, 0, window, atom, _XCB_GET_PROPERTY_TYPE_ANY, 0, 1024)
    reply = lib.xcb_get_property_reply(conn, cookie, ctypes.byref(error))
    if error.value:
        free(error)
    if not reply:
        return None
    try:
        length = lib.xcb_get_property_value_length(reply)
        return ctypes.string_at(lib.xcb_get_property_value(reply), length) if length > 0 else None
    finally:
        free(reply)


def parse_wm_class(raw):
    """WM_CLASS holds the instance and class names, each ending in a NUL byte.
    Returns the class part, or the instance if that's all there is."""
    parts = [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]
    return parts[-1] if parts else None


def x11_root(lib, conn, screen_number):
    """The root window of screen *screen_number* (from xcb_connect), or None."""
    screens = lib.xcb_setup_roots_iterator(lib.xcb_get_setup(conn))
    for _ in range(screen_number):
        lib.xcb_screen_next(ctypes.byref(screens))
    if not screens.data:
        return None
    return ctypes.c_uint32.from_address(screens.data).value   # xcb_screen_t starts with root


def _from_x11():
    """(class, window) of the window named in _NET_ACTIVE_WINDOW, through
    XCB; None for what isn't known."""
    lib = xcb_lib()
    screen_number = ctypes.c_int(0)
    conn = lib.xcb_connect(None, ctypes.byref(screen_number))   # $DISPLAY
    try:
        if not conn or lib.xcb_connection_has_error(conn):
            return None, None
        root = x11_root(lib, conn, screen_number.value)
        if not root:
            return None, None

        name = b"_NET_ACTIVE_WINDOW"
        error = ctypes.c_void_p()
        atom_reply = lib.xcb_intern_atom_reply(
            conn, lib.xcb_intern_atom(conn, 1, len(name), name), ctypes.byref(error))
        if error.value:
            free(error)
        if not atom_reply:
            return None, None
        active_atom = atom_reply.contents.atom
        free(atom_reply)
        if not active_atom:
            return None, None       # no EWMH window manager running

        raw = _x11_property(lib, conn, root, active_atom)
        if not raw or len(raw) < 4:
            return None, None
        window = struct.unpack("=I", raw[:4])[0]
        if not window:
            return None, None       # nothing has focus (the desktop itself)
        wm_class = _x11_property(lib, conn, window, _XCB_ATOM_WM_CLASS)
        return (parse_wm_class(wm_class) if wm_class else None), window
    finally:
        if conn:
            lib.xcb_disconnect(conn)


def is_x11_session(env):
    """XDG_SESSION_TYPE when it is set; otherwise an X DISPLAY with no Wayland one."""
    session = (env.get("XDG_SESSION_TYPE") or "").lower()
    if session:
        return session == "x11"
    return bool(env.get("DISPLAY")) and not env.get("WAYLAND_DISPLAY")


def focused_class():
    """The focused window's class or app_id, or None if this isn't one of
    these desktops or it didn't answer."""
    return focused_window()[0] or None


def _ask_focused_window(env):
    """focused_window()'s answer from this desktop's compositor; raises
    what talking to it raised."""
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return _from_hyprland(env["HYPRLAND_INSTANCE_SIGNATURE"])
    if env.get("NIRI_SOCKET"):
        return _from_niri(env["NIRI_SOCKET"])
    path = env.get("SWAYSOCK") or env.get("I3SOCK")
    if path:
        return _from_i3(path)
    if desktop_detect.get_desktop_environment() == "cosmic":
        return _from_cosmic(_wayland_socket(env)), None
    if is_x11_session(env):
        return _from_x11()
    return None, None


def focused_window():
    """(class or app_id, id) of the focused window, None for what isn't
    known. The id is the compositor's own for the window (X11's window,
    Sway's and i3's con id, Hyprland's address, niri's id): it tells two
    windows of one app apart, and stays the same when the window moves.
    COSMIC names only the class here. Asked before every typed chunk, so a
    failure is logged once (_failures)."""
    try:
        found = _ask_focused_window(os.environ)
    except Exception as e:      # an odd answer mustn't stop the typing that asked
        if _failures.failed("window", e):
            logger.info(f"Could not ask the compositor for the focused window: {e}")
        return None, None
    if _failures.worked("window"):
        logger.info("The compositor answers again about the focused window")
    return found


# --- Where the focused window is (window_geometry.py) ----------------------------------
# Sway / i3 and Hyprland say where it is too; niri doesn't (its windows scroll),
# and X11 is asked through GDK in window_geometry.

def _rect_from_i3(path):
    from .window_geometry import Rect
    tree = _exchange(path, _I3_MAGIC + struct.pack("<II", 0, _I3_GET_TREE), _i3_reply)
    node = _find_focused(tree)
    if not node or node.get("type") not in ("con", "floating_con"):
        return None
    r = node.get("rect") or {}
    return Rect(r["x"], r["y"], r["width"], r["height"]) if r.get("width") else None


def _rect_from_hyprland(signature):
    from .window_geometry import Rect
    window = json.loads(_exchange(hyprland_socket(signature), b"j/activewindow",
                                  _recv_until_closed) or b"{}")
    at, size = window.get("at"), window.get("size")
    return Rect(at[0], at[1], size[0], size[1]) if at and size else None


def focused_rect():
    """The focused window's frame (window_geometry.Rect, compositor
    coordinates) on Sway, i3 or Hyprland, or None. A failure is logged once,
    as focused_window's is."""
    env = os.environ
    path = env.get("SWAYSOCK") or env.get("I3SOCK")
    try:
        if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
            rect = _rect_from_hyprland(env["HYPRLAND_INSTANCE_SIGNATURE"])
        else:
            rect = _rect_from_i3(path) if path else None
    except Exception as e:      # an odd answer mustn't stop the window that asked
        if _failures.failed("frame", e):
            logger.info(f"Could not ask the compositor where the focused window is: {e}")
        return None
    if _failures.worked("frame"):
        logger.info("The compositor answers again about where the focused window is")
    return rect
