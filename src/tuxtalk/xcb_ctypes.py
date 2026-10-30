"""libxcb, the X server's own client library, through ctypes: the few
calls compositor_focus (which window has focus on X11) and x11_hotkeys (the
hotkeys held back on X11) make. It's on every X11 system (Xlib itself is
built on it), so this needs no new dependency. XCB rather than Xlib: Xlib
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
