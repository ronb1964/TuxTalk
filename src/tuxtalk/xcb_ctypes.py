"""libxcb, the X server's own client library, through ctypes: the few
calls compositor_focus (which window has focus on X11), x11_hotkeys (the
hotkeys held back on X11) and x11_activate (bringing a window forward)
make. It's on every X11 system (Xlib itself is built on it), so this needs
no new dependency. XCB rather than Xlib: Xlib
reports an error through a process-wide handler whose default exits the
program, and replacing it would fight GTK's own, which the recording
indicator runs on X11 in the same process as compositor_focus. XCB returns
errors per request.
"""
import ctypes


class Cookie(ctypes.Structure):
    _fields_ = [("sequence", ctypes.c_uint)]


class _ScreenIterator(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("rem", ctypes.c_int), ("index", ctypes.c_int)]


class _AtomReply(ctypes.Structure):
    _fields_ = [("response_type", ctypes.c_uint8), ("pad0", ctypes.c_uint8),
                ("sequence", ctypes.c_uint16), ("length", ctypes.c_uint32),
                ("atom", ctypes.c_uint32)]


class _Error(ctypes.Structure):
    _fields_ = [("response_type", ctypes.c_uint8), ("error_code", ctypes.c_uint8)]


class _GeometryReply(ctypes.Structure):
    _fields_ = [("response_type", ctypes.c_uint8), ("depth", ctypes.c_uint8),
                ("sequence", ctypes.c_uint16), ("length", ctypes.c_uint32),
                ("root", ctypes.c_uint32), ("x", ctypes.c_int16), ("y", ctypes.c_int16),
                ("width", ctypes.c_uint16), ("height", ctypes.c_uint16),
                ("border_width", ctypes.c_uint16)]


class _TranslateReply(ctypes.Structure):
    _fields_ = [("response_type", ctypes.c_uint8), ("same_screen", ctypes.c_uint8),
                ("sequence", ctypes.c_uint16), ("length", ctypes.c_uint32),
                ("child", ctypes.c_uint32), ("dst_x", ctypes.c_int16),
                ("dst_y", ctypes.c_int16)]


class _MonitorInfo(ctypes.Structure):
    """xcb_randr_monitor_info_t, without the outputs that follow it."""
    _fields_ = [("name", ctypes.c_uint32), ("primary", ctypes.c_uint8),
                ("automatic", ctypes.c_uint8), ("n_output", ctypes.c_uint16),
                ("x", ctypes.c_int16), ("y", ctypes.c_int16),
                ("width", ctypes.c_uint16), ("height", ctypes.c_uint16),
                ("width_mm", ctypes.c_uint32), ("height_mm", ctypes.c_uint32)]


class _MonitorIterator(ctypes.Structure):
    _fields_ = [("data", ctypes.POINTER(_MonitorInfo)), ("rem", ctypes.c_int),
                ("index", ctypes.c_int)]


_xcb = None


def xcb_lib():
    """libxcb with the few signatures TuxTalk uses, loaded once."""
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
        lib.xcb_intern_atom.restype = Cookie
        lib.xcb_intern_atom.argtypes = [P, U8, U16, ctypes.c_char_p]
        lib.xcb_intern_atom_reply.restype = ctypes.POINTER(_AtomReply)
        lib.xcb_intern_atom_reply.argtypes = [P, Cookie, ctypes.POINTER(P)]
        lib.xcb_get_property.restype = Cookie
        lib.xcb_get_property.argtypes = [P, U8, U32, U32, U32, U32, U32]
        lib.xcb_get_property_reply.restype = P
        lib.xcb_get_property_reply.argtypes = [P, Cookie, ctypes.POINTER(P)]
        lib.xcb_get_property_value.restype = P
        lib.xcb_get_property_value.argtypes = [P]
        lib.xcb_get_property_value_length.argtypes = [P]
        lib.xcb_grab_key_checked.restype = Cookie
        lib.xcb_grab_key_checked.argtypes = [P, U8, U32, U16, U8, U8, U8]
        lib.xcb_ungrab_key.restype = Cookie
        lib.xcb_ungrab_key.argtypes = [P, U8, U32, U16]
        lib.xcb_request_check.restype = ctypes.POINTER(_Error)
        lib.xcb_request_check.argtypes = [P, Cookie]
        lib.xcb_ungrab_keyboard.restype = Cookie
        lib.xcb_ungrab_keyboard.argtypes = [P, U32]
        lib.xcb_wait_for_event.restype = P
        lib.xcb_wait_for_event.argtypes = [P]
        lib.xcb_flush.argtypes = [P]
        lib.xcb_send_event_checked.restype = Cookie
        lib.xcb_send_event_checked.argtypes = [P, U8, U32, U32, ctypes.c_char_p]
        lib.xcb_get_extension_data.restype = P
        lib.xcb_get_extension_data.argtypes = [P, P]
        lib.xcb_get_geometry.restype = Cookie
        lib.xcb_get_geometry.argtypes = [P, U32]
        lib.xcb_get_geometry_reply.restype = ctypes.POINTER(_GeometryReply)
        lib.xcb_get_geometry_reply.argtypes = [P, Cookie, ctypes.POINTER(P)]
        lib.xcb_translate_coordinates.restype = Cookie
        lib.xcb_translate_coordinates.argtypes = [P, U32, U32, ctypes.c_int16, ctypes.c_int16]
        lib.xcb_translate_coordinates_reply.restype = ctypes.POINTER(_TranslateReply)
        lib.xcb_translate_coordinates_reply.argtypes = [P, Cookie, ctypes.POINTER(P)]
        _xcb = lib
    return _xcb


free = ctypes.CDLL(None).free
free.argtypes = [ctypes.c_void_p]


def x11_root(lib, conn, screen_number):
    """The root window of screen *screen_number* (from xcb_connect), or None."""
    screens = lib.xcb_setup_roots_iterator(lib.xcb_get_setup(conn))
    for _ in range(screen_number):
        lib.xcb_screen_next(ctypes.byref(screens))
    if not screens.data:
        return None
    return ctypes.c_uint32.from_address(screens.data).value   # xcb_screen_t starts with root


def intern_atom(lib, conn, name: bytes):
    """The atom the X server has for *name*, or None when it has none yet
    (only_if_exists: a property nobody set can't be on any window)."""
    error = ctypes.c_void_p()
    reply = lib.xcb_intern_atom_reply(conn, lib.xcb_intern_atom(conn, 1, len(name), name),
                                      ctypes.byref(error))
    if error.value:
        free(error)
    if not reply:
        return None
    atom = reply.contents.atom
    free(reply)
    return atom or None


_GET_PROPERTY_TYPE_ANY = 0


def get_property(lib, conn, window, atom):
    """Raw bytes of *atom* on *window*, or None (an error, or not set)."""
    error = ctypes.c_void_p()
    cookie = lib.xcb_get_property(conn, 0, window, atom, _GET_PROPERTY_TYPE_ANY, 0, 1024)
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


def geometry(lib, conn, window, root):
    """(x, y, width, height) of *window*, its position in root-window
    coordinates, or None when the X server has no such window."""
    size_error, origin_error = ctypes.c_void_p(), ctypes.c_void_p()
    size = lib.xcb_get_geometry_reply(conn, lib.xcb_get_geometry(conn, window),
                                      ctypes.byref(size_error))
    origin = lib.xcb_translate_coordinates_reply(
        conn, lib.xcb_translate_coordinates(conn, window, root, 0, 0),
        ctypes.byref(origin_error))
    try:
        if not size or not origin:
            return None
        return (origin.contents.dst_x, origin.contents.dst_y,
                size.contents.width, size.contents.height)
    finally:
        for pointer in (size, origin, size_error, origin_error):
            if pointer:
                free(pointer)


_randr = None


def _randr_lib():
    """libxcb-randr with the monitor calls' signatures, loaded once; None
    when it isn't installed."""
    global _randr
    if _randr is None:
        try:
            lib = ctypes.CDLL("libxcb-randr.so.0")
        except OSError:
            return None
        P, U32 = ctypes.c_void_p, ctypes.c_uint32
        lib.xcb_randr_get_monitors.restype = Cookie
        lib.xcb_randr_get_monitors.argtypes = [P, U32, ctypes.c_uint8]
        lib.xcb_randr_get_monitors_reply.restype = P
        lib.xcb_randr_get_monitors_reply.argtypes = [P, Cookie, ctypes.POINTER(P)]
        lib.xcb_randr_get_monitors_monitors_iterator.restype = _MonitorIterator
        lib.xcb_randr_get_monitors_monitors_iterator.argtypes = [P]
        lib.xcb_randr_monitor_info_next.argtypes = [ctypes.POINTER(_MonitorIterator)]
        _randr = lib
    return _randr


def _has_randr(randr, conn) -> bool:
    """Whether the X server has RandR. Asked first: libxcb shuts the whole
    connection down when a request names an extension the server lacks."""
    extension = ctypes.addressof(ctypes.c_char.in_dll(randr, "xcb_randr_id"))
    reply = xcb_lib().xcb_get_extension_data(conn, extension)
    return bool(reply and ctypes.c_uint8.from_address(reply + 8).value)   # .present


def randr_monitors(conn, root):
    """(x, y, width, height) of each active monitor, as RandR 1.5 lists
    them; [] when libxcb-randr or the extension isn't there."""
    randr = _randr_lib()
    if randr is None or not _has_randr(randr, conn):
        return []
    error = ctypes.c_void_p()
    reply = randr.xcb_randr_get_monitors_reply(
        conn, randr.xcb_randr_get_monitors(conn, root, 1), ctypes.byref(error))
    if error.value:
        free(error)
    if not reply:
        return []
    try:
        found = []
        monitors = randr.xcb_randr_get_monitors_monitors_iterator(reply)
        while monitors.rem:
            info = monitors.data.contents
            found.append((info.x, info.y, info.width, info.height))
            randr.xcb_randr_monitor_info_next(ctypes.byref(monitors))
        return found
    finally:
        free(reply)
