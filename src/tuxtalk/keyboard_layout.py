"""
Type text correctly on any keyboard layout.

TuxTalk's virtual keyboard (virtual_keyboard.py) sends key positions, and the
compositor reads them with the user's real layout. Sending the US position
for every letter, as ydotool's `type` did, turns "my keyboard is lazy" into
"mz kezboard is layz" on a Swiss German (QWERTZ) keyboard, moves punctuation
and can't type umlauts at all (see issue #8). So this module works out the
real keys: it loads the active layout into libxkbcommon (the library
compositors use to turn key presses into characters) and finds, for each
character, the key plus Shift / AltGr that produces it. A character the
layout has no key for, like a capital Ä on a Swiss keyboard, is typed as its
accent's dead key followed by the letter.

A layout that can't be read is taken to be US. If some character can't be
typed on the active layout at all (an emoji, Latin text on a Ukrainian
layout), the caller pastes that dictation instead, which works on any layout.

The keymap is built from the layout, its variant and the desktop's XKB
options, which can turn Right Alt into AltGr on a US layout
(lv3:ralt_switch), into Compose (compose:ralt) or leave it plain Alt
(lv3:ralt_alt). Without them, AltGr characters would be typed with a Right
Alt that types none, and the hotkey dropdown would misjudge Right Alt.

Finding the active layout and its options depends on the desktop:
  KDE        org.kde.keyboard over D-Bus (the active one of the configured
             list); options from the X server on X11, and on Wayland from
             kxkbrc through kreadconfig, as KWin applies them: only with
             ResetOldOptions on
  GNOME      gsettings org.gnome.desktop.input-sources (mru-sources first,
             options in xkb-options)
  COSMIC     the layout and options COSMIC Settings saves for cosmic-comp
             (the first layout of the list: which one is on isn't published)
  Hyprland   `j/devices` on its socket (the main keyboard, with its options)
  Sway       GET_INPUTS on its socket; names are descriptions, mapped to codes
             with libxkbregistry. It reports no options: libxkbcommon's
             defaults are used, as Sway does when its config sets none
  X11        setxkbmap -query for the list and options, XkbGetState for which
             layout is on
  otherwise  the system default (xorg.conf.d / /etc/default/keyboard)
Options None means "the desktop passes none": libxkbcommon then takes
XKB_DEFAULT_OPTIONS. "" means no options. Options that can't be read are
None, and the layout found stays.
Anything that fails just means "unknown", and unknown means the US path.
"""
import ctypes as C
import ctypes.util
import functools
import json
import os
import re
import shutil
import subprocess
import unicodedata

from .logger import setup_logger

logger = setup_logger(__name__)

# --- libxkbcommon ------------------------------------------------------------------

_xkb = None


class _RuleNames(C.Structure):
    _fields_ = [(n, C.c_char_p) for n in ("rules", "model", "layout", "variant", "options")]


def _lib():
    """libxkbcommon with the signatures used here, loaded once."""
    global _xkb
    if _xkb is None:
        x = C.CDLL("libxkbcommon.so.0")
        P, U32 = C.c_void_p, C.c_uint32
        x.xkb_context_new.restype = P
        x.xkb_context_new.argtypes = [C.c_int]
        x.xkb_context_unref.argtypes = [P]
        x.xkb_keymap_new_from_names.restype = P
        x.xkb_keymap_new_from_names.argtypes = [P, C.POINTER(_RuleNames), C.c_int]
        x.xkb_keymap_min_keycode.argtypes = [P]
        x.xkb_keymap_max_keycode.argtypes = [P]
        x.xkb_keymap_num_levels_for_key.argtypes = [P, U32, U32]
        x.xkb_keymap_key_get_syms_by_level.argtypes = [P, U32, U32, U32, C.POINTER(C.POINTER(U32))]
        x.xkb_keymap_key_get_mods_for_level.argtypes = [P, U32, U32, U32, C.POINTER(U32), C.c_size_t]
        x.xkb_keymap_key_get_mods_for_level.restype = C.c_size_t
        x.xkb_keymap_mod_get_index.argtypes = [P, C.c_char_p]
        x.xkb_keymap_mod_get_index.restype = U32
        x.xkb_utf32_to_keysym.argtypes = [U32]
        x.xkb_utf32_to_keysym.restype = U32
        x.xkb_keysym_to_utf32.argtypes = [U32]
        x.xkb_keysym_to_utf32.restype = U32
        _xkb = x
    return _xkb


_EVDEV_OFFSET = 8               # xkb keycode = evdev keycode + 8
_KEY_LEFTSHIFT = 42             # evdev
_KEY_RIGHTALT = 100             # evdev; AltGr on almost every layout
_KEY_LVL3 = 84                  # evdev; the keymaps' virtual AltGr key (<LVL3>)
_KEY_102ND = 86                 # evdev; the <> key of ISO keyboards
_KEY_F12 = 88                   # evdev; past it: the keypad's and extra keys
_X11_MAX_KEYCODE = 255          # xkb; X11 and XWayland carry no key past it
_ISO_LEVEL3_SHIFT = 0xfe03      # keysym AltGr produces
_KEYPAD_KEYSYMS = range(0xff80, 0xffbe)     # KP_Space .. KP_Equal
_INVALID_MOD = 0xffffffff

# Combining marks (what NFD splits an accented letter into) and the dead key
# that types each one on layouts without a key for the accented letter.
_DEAD_KEYS = {
    "̀": 0xfe50,  # grave
    "́": 0xfe51,  # acute
    "̂": 0xfe52,  # circumflex
    "̃": 0xfe53,  # tilde
    "̄": 0xfe54,  # macron
    "̆": 0xfe55,  # breve
    "̇": 0xfe56,  # dot above
    "̈": 0xfe57,  # diaeresis (umlaut)
    "̊": 0xfe58,  # ring
    "̋": 0xfe59,  # double acute
    "̌": 0xfe5a,  # caron
    "̧": 0xfe5b,  # cedilla
    "̨": 0xfe5c,  # ogonek
}


def _cost(entry):
    """How much a key is avoided: one for Shift, one for AltGr, and more for
    a key not every keyboard has. Three for the extra key beside the left
    Shift (ISO keyboards' <>): a desktop set to a US model may map nothing to
    it, and "<" would go missing where Shift+comma works. Two for the keypad's
    and extra keys past F12, which keymaps give $ ( ) on with no Shift; less
    than <>, so a Japanese | stays Shift+Yen, not AltGr on a <> key Japanese
    keyboards don't have."""
    keycode, shift, altgr = entry
    if keycode == _KEY_102ND:
        rare = 3
    elif keycode > _KEY_F12:
        rare = 2
    else:
        rare = 0
    return shift + altgr + rare


def _altgr_key(keys):
    """Which of *keys* (evdev codes typing AltGr) to hold for it, or None.
    Right Alt if it is AltGr, as it is on real keyboards. Layouts also map
    the virtual <LVL3> key to it, which works in the keymap but not on every
    compositor, so a real key an option made AltGr (lv3:switch puts it on
    Right Ctrl) comes before that."""
    if _KEY_RIGHTALT in keys:
        return _KEY_RIGHTALT
    real = [k for k in keys if k != _KEY_LVL3]
    return (real or keys or [None])[0]


class KeyPlanner:
    """The keys that type text on one keymap: a layout, its variant and the
    XKB options ("ch", "", "compose:ralt")."""

    def __init__(self, layout, variant="", options=None):
        """Load the keymap from libxkbcommon and index its keys; ValueError
        for a keymap libxkbcommon can't build. Options None leaves them to
        libxkbcommon's default (XKB_DEFAULT_OPTIONS), as a compositor does
        that is given none; "" is no options, whatever the environment says."""
        x = _lib()
        self.layout, self.variant, self.options = layout, variant or "", options
        self.ctx = x.xkb_context_new(0)
        names = _RuleNames(b"evdev", b"pc105", layout.encode(), self.variant.encode(),
                           None if options is None else options.encode())
        self.keymap = x.xkb_keymap_new_from_names(self.ctx, C.byref(names), 0)
        if not self.keymap:
            x.xkb_context_unref(self.ctx)
            raise ValueError(f"unknown keyboard layout {layout}({variant})"
                             + (f" with options {options}" if options else ""))
        self._keys = {}         # keysym -> (evdev keycode, shift, altgr)
        self._by_char = {}      # character -> the same, for _keysym misses
        # Keysyms a key of an ordinary keyboard types without AltGr. Layouts
        # also put € on KEY_EURO (435), which few keyboards have.
        self._without_altgr = set()
        self._altgr = None
        self._index()

    def _index(self):
        """Map every keysym to its cheapest key (_cost)."""
        x = _lib()
        shift = x.xkb_keymap_mod_get_index(self.keymap, b"Shift")
        altgr = x.xkb_keymap_mod_get_index(self.keymap, b"Mod5")
        shift_bit = 1 << shift if shift != _INVALID_MOD else 0
        altgr_bit = 1 << altgr if altgr != _INVALID_MOD else 0
        allowed = shift_bit | altgr_bit
        altgr_keys = []
        # Keys past X11's last code (KEY_DOLLAR, KEY_EURO) never reach an X11
        # app: "$27.34" was typed as "27.34" in Claude Desktop.
        last = min(x.xkb_keymap_max_keycode(self.keymap), _X11_MAX_KEYCODE)
        for kc in range(x.xkb_keymap_min_keycode(self.keymap), last + 1):
            for level in range(x.xkb_keymap_num_levels_for_key(self.keymap, kc, 0)):
                syms = C.POINTER(C.c_uint32)()
                if x.xkb_keymap_key_get_syms_by_level(self.keymap, kc, 0, level, C.byref(syms)) != 1:
                    continue
                sym = syms[0]
                masks = (C.c_uint32 * 8)()
                for i in range(x.xkb_keymap_key_get_mods_for_level(self.keymap, kc, 0, level, masks, 8)):
                    mask = masks[i]
                    if mask & ~allowed:
                        continue        # needs Caps Lock, Level5 or the like: skip
                    if sym == _ISO_LEVEL3_SHIFT and mask == 0:
                        altgr_keys.append(kc - _EVDEV_OFFSET)
                    self._add(sym, (kc - _EVDEV_OFFSET, bool(mask & shift_bit),
                                    bool(mask & altgr_bit)))
        # A character can have two keysyms (€ is 0x20ac and 0x13a4): Ubuntu
        # 22.04's libxkbcommon names € 0x13a4 while the keymap has 0x20ac, and
        # the German layout's … is a keysym no character lookup names. What a
        # key types finds them. Not the keypad's: a terminal in keypad mode
        # sends escape codes for those.
        for sym, entry in self._keys.items():
            char = x.xkb_keysym_to_utf32(sym)
            if (char and sym not in _KEYPAD_KEYSYMS
                    and (char not in self._by_char or _cost(entry) < _cost(self._by_char[char]))):
                self._by_char[char] = entry
        self._altgr = _altgr_key(altgr_keys)

    def _add(self, sym, entry):
        """Note that key *entry* types keysym *sym*: kept when it's the
        cheapest key for it so far (_cost), and counted for altgr_only when
        it needs no AltGr."""
        if not entry[2] and entry[0] < 0x100:
            self._without_altgr.add(sym)
        if sym not in self._keys or _cost(entry) < _cost(self._keys[sym]):
            self._keys[sym] = entry

    def _press(self, entry, tokens):
        """Add the key events for *entry* to *tokens*; False if AltGr is needed but missing."""
        keycode, shift, altgr = entry
        if altgr and self._altgr is None:
            return False
        held = ([_KEY_LEFTSHIFT] if shift else []) + ([self._altgr] if altgr else [])
        tokens += [(k, 1) for k in held]
        tokens += [(keycode, 1), (keycode, 0)]
        tokens += [(k, 0) for k in reversed(held)]
        return True

    def altgr_only(self, chars):
        """Those of *chars* this layout types with AltGr and no other way, or
        None when its Right Alt isn't AltGr."""
        if self._altgr != _KEY_RIGHTALT:
            return None
        # By character, not keysym: Ubuntu 22.04's libxkbcommon names € by a
        # keysym the keymap doesn't use (see _index). The keypad counts here
        # though _by_char leaves it out: none of the characters asked about
        # is on it.
        x = _lib()
        without = {x.xkb_keysym_to_utf32(sym) for sym in self._without_altgr}
        return [c for c in chars if ord(c) in self._by_char and ord(c) not in without]

    def _keysym(self, char):
        return 0x20 if char == " " else _lib().xkb_utf32_to_keysym(ord(char))

    def plan(self, text):
        """The key events [(evdev keycode, 1 down / 0 up)] that type *text*, or
        None if some character can't be typed on this layout."""
        return self.plan_or_blocker(text)[0]

    def plan_or_blocker(self, text):
        """(tokens, None), or (None, the first character that can't be typed)."""
        tokens = []
        for char in text:
            entry = self._keys.get(self._keysym(char)) or self._by_char.get(ord(char))
            if entry and self._press(entry, tokens):
                continue
            # Not on a key of its own: an accented letter as dead key + letter.
            parts = unicodedata.normalize("NFD", char)
            if len(parts) == 2 and parts[1] in _DEAD_KEYS:
                dead = self._keys.get(_DEAD_KEYS[parts[1]])
                base = self._keys.get(self._keysym(parts[0]))
                if dead and base and self._press(dead, tokens) and self._press(base, tokens):
                    continue
            return None, char
        return tokens, None


_planners = {}


def planner_for(names):
    """A cached KeyPlanner for (layout, variant, options); building one
    takes ~20 ms. The options are part of the key: they change the keymap.
    Options libxkbcommon can't build (a custom one whose file is missing)
    are left out rather than losing the layout too."""
    if names not in _planners:
        layout, variant, options = names
        try:
            _planners[names] = KeyPlanner(layout, variant, options)
        except ValueError as e:
            if options == "":
                raise
            # "", not None: None would bring back XKB_DEFAULT_OPTIONS, which
            # may hold the broken option itself.
            logger.warning(f"{e}; typing for {layout}({variant}) without options")
            _planners[names] = KeyPlanner(layout, variant, "")
    return _planners[names]


# --- the active layout ---------------------------------------------------------------

_XORG_KEYBOARD_CONF = "/etc/X11/xorg.conf.d/00-keyboard.conf"
_DEBIAN_KEYBOARD = "/etc/default/keyboard"


def _desktop():
    from .desktop_detect import get_desktop_environment
    return get_desktop_environment()


def _first(csv, index=0):
    """Item *index* of a comma-separated list, or the first item if out of range."""
    items = [s.strip() for s in (csv or "").split(",")]
    return items[index] if 0 <= index < len(items) else (items[0] if items else "")


def _kde_layouts():
    """([(layout, variant)], active index) from KDE's keyboard daemon."""
    import dbus
    obj = dbus.SessionBus().get_object("org.kde.keyboard", "/Layouts")
    iface = dbus.Interface(obj, "org.kde.KeyboardLayouts")
    layouts = [(str(entry[0]), str(entry[1])) for entry in iface.getLayoutsList()]
    return layouts, int(iface.getLayout())


def _kde_options():
    """The XKB options KDE uses; its keyboard daemon's D-Bus interface
    doesn't list them. On X11 the X server holds whatever KDE applied. On
    Wayland they are kxkbrc's, read as KWin reads them (src/xkb.cpp):
    Options only when ResetOldOptions is on, and None (libxkbcommon's
    defaults) when it isn't, since KWin then passes none."""
    if (os.environ.get("XDG_SESSION_TYPE") or "").lower() == "x11":
        return _setxkbmap_fields().get("options", "")
    # kreadconfig answers a bool by its exit code, 0 for true and 1 for false.
    reset = _kreadconfig("--key", "ResetOldOptions", "--type", "bool")
    if reset.returncode not in (0, 1):
        reset.check_returncode()
    if reset.returncode == 1:
        return None
    options = _kreadconfig("--key", "Options")
    options.check_returncode()
    return options.stdout.strip()


def _kreadconfig(*args):
    """kreadconfig run on kxkbrc's Layout group: KConfig's own reading, so
    the admin's /etc/xdg/kxkbrc and entries marked [$i] count as they do for
    KWin. It ships with KDE's config library (Plasma 6 and 5)."""
    tool = shutil.which("kreadconfig6") or shutil.which("kreadconfig5")
    if not tool:
        raise FileNotFoundError("neither kreadconfig6 nor kreadconfig5 is installed")
    return subprocess.run([tool, "--file", "kxkbrc", "--group", "Layout", *args],
                          capture_output=True, text=True, timeout=2)


def _options_or_defaults(read):
    """The XKB options *read* returns, or None (libxkbcommon's defaults)
    when they can't be read: the layout already found is still right, and
    typing for it beats the system's layout."""
    try:
        return read()
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        _note_unreadable_options(f"{type(e).__name__}: {e}")
        return None


@functools.lru_cache(maxsize=None)
def _note_unreadable_options(problem):
    """Say once per problem: the options are read before every typed chunk."""
    logger.warning(f"Could not read the keyboard options ({problem}); using the defaults")


def _gsettings(key):
    return subprocess.run(["gsettings", "get", "org.gnome.desktop.input-sources", key],
                          capture_output=True, text=True, timeout=2, check=True).stdout


def _gnome_layout():
    """GNOME's current (layout, variant, options), or None for an input method."""
    for key in ("mru-sources", "sources"):
        found = re.findall(r"\('(\w+)',\s*'([^']*)'\)", _gsettings(key))
        if found:
            kind, name = found[0]
            if kind != "xkb":
                return None     # an input method (IBus); its layout isn't knowable here
            layout, _, variant = name.partition("+")
            # A list like "['lv3:ralt_switch', 'compose:rctrl']", or "@as []",
            # which GNOME Shell passes on as "" (keyboardManager.js).
            options = _options_or_defaults(lambda: ",".join(
                re.findall(r"'([^']*)'", _gsettings("xkb-options"))))
            return layout, variant, options
    return None


def _cosmic_xkb_config():
    from .desktop_detect import config_home
    path = os.path.join(config_home(), "cosmic", "com.system76.CosmicComp", "v1", "xkb_config")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _cosmic_layout():
    """COSMIC's (layout, variant, options), from its RON config:
    (rules: "", model: "pc105", layout: "de,us", variant: ",",
    options: Some("compose:ralt"), ...). Its options: None is None here too,
    since cosmic-comp passes it to libxkbcommon as none (its defaults)."""
    config = _cosmic_xkb_config()
    fields = dict(re.findall(r'\b(layout|variant):\s*"([^"]*)"', config))
    if "," in fields.get("layout", ""):
        _note_several_layouts(fields["layout"])
    options = re.search(r'\boptions:\s*Some\("([^"]*)"\)', config)
    return (_first(fields.get("layout")), _first(fields.get("variant")),
            options.group(1) if options else None)


@functools.lru_cache(maxsize=None)
def _note_several_layouts(layouts):
    """Say once per list that only the first is typed for: COSMIC doesn't
    tell which one is switched on."""
    logger.warning(f"COSMIC has several layouts ({layouts}); TuxTalk types for the "
                   "first, so text typed with another switched on comes out wrong")


def _hyprland_devices():
    from .compositor_focus import _exchange, _recv_until_closed
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    path = os.path.join(runtime, "hypr", os.environ["HYPRLAND_INSTANCE_SIGNATURE"], ".socket.sock")
    return json.loads(_exchange(path, b"j/devices", _recv_until_closed))


def _hyprland_layout():
    keyboards = _hyprland_devices().get("keyboards") or []
    board = next((k for k in keyboards if k.get("main")), keyboards[0] if keyboards else None)
    if not board:
        return None
    index = int(board.get("active_layout_index") or 0)
    # Hyprland always passes its kb_options string, "" included.
    return (_first(board.get("layout"), index), _first(board.get("variant"), index),
            board.get("options") or "")


def _sway_inputs():
    import struct
    from .compositor_focus import _I3_MAGIC, _exchange, _i3_reply
    get_inputs = 100
    return _exchange(os.environ.get("SWAYSOCK") or os.environ["I3SOCK"],
                     _I3_MAGIC + struct.pack("<II", 0, get_inputs), _i3_reply)


# Description -> (layout, variant) for every layout libxkbregistry knows,
# built once: active_layout() runs before every typed chunk, and parsing the
# whole registry takes tens of milliseconds.
_registry_table = None


def _registry_layouts():
    """{description: (layout, variant)} from libxkbregistry, which holds the
    names every layout picker shows. The first layout with a description wins."""
    global _registry_table
    if _registry_table is not None:
        return _registry_table
    reg = C.CDLL("libxkbregistry.so.0")
    P = C.c_void_p
    reg.rxkb_context_new.restype = P
    reg.rxkb_context_new.argtypes = [C.c_int]
    reg.rxkb_context_parse_default_ruleset.argtypes = [P]
    reg.rxkb_context_unref.restype = P
    reg.rxkb_context_unref.argtypes = [P]
    reg.rxkb_layout_first.restype = P
    reg.rxkb_layout_first.argtypes = [P]
    reg.rxkb_layout_next.restype = P
    reg.rxkb_layout_next.argtypes = [P]
    for fn in ("rxkb_layout_get_name", "rxkb_layout_get_variant", "rxkb_layout_get_description"):
        getattr(reg, fn).restype = C.c_char_p
        getattr(reg, fn).argtypes = [P]
    ctx = reg.rxkb_context_new(0)
    if not ctx:
        return {}
    table = {}
    try:
        if not reg.rxkb_context_parse_default_ruleset(ctx):
            return {}               # not cached: the next call tries again
        layout = reg.rxkb_layout_first(ctx)
        while layout:
            # Copied into Python strings: the layout objects belong to ctx.
            description = (reg.rxkb_layout_get_description(layout) or b"").decode()
            table.setdefault(description, ((reg.rxkb_layout_get_name(layout) or b"").decode(),
                                           (reg.rxkb_layout_get_variant(layout) or b"").decode()))
            layout = reg.rxkb_layout_next(layout)
    finally:
        reg.rxkb_context_unref(ctx)
    _registry_table = table
    return table


def _layout_from_description(description):
    """(layout, variant) for a description like "German (Switzerland)", or None."""
    return _registry_layouts().get(description)


def _sway_layout():
    board = next((i for i in _sway_inputs() if i.get("type") == "keyboard"), None)
    if not board:
        return None
    names = board.get("xkb_layout_names") or []
    index = board.get("xkb_active_layout_index") or 0
    if not names:
        return None
    found = _layout_from_description(names[index if index < len(names) else 0])
    # GET_INPUTS has no options; they are only in Sway's own config, and
    # Sway passes none when it sets none: libxkbcommon's defaults.
    return (*found, None) if found else None


def _setxkbmap_query():
    return subprocess.run(["setxkbmap", "-query"], capture_output=True, text=True, timeout=2,
                          check=True).stdout


def _setxkbmap_fields():
    """{"layout": "us,ua", "options": "compose:ralt", ...}; a field the X
    server has no value for is left out."""
    return dict(re.findall(r"^(\w+):\s*(.*)$", _setxkbmap_query(), re.M))


_XKB_USE_CORE_KBD = 0x0100


def _x11_active_group():
    """Which of the configured layouts is on (its XKB group), or None.

    setxkbmap -query lists every layout ("us,ua") and says nothing about which
    one is active; assuming the first would type Cyrillic with US keys on a
    Ukrainian desktop that lists English first. XkbGetState is the Xlib call
    that knows; the group is the first byte of XkbStateRec.
    """
    x11 = C.CDLL(C.util.find_library("X11") or "libX11.so.6")
    x11.XOpenDisplay.restype = C.c_void_p
    x11.XOpenDisplay.argtypes = [C.c_char_p]
    x11.XkbGetState.argtypes = [C.c_void_p, C.c_uint, C.c_void_p]
    x11.XCloseDisplay.argtypes = [C.c_void_p]
    display = x11.XOpenDisplay(None)
    if not display:
        return None
    try:
        state = (C.c_ubyte * 64)()      # XkbStateRec is 18 bytes; room to spare
        if x11.XkbGetState(display, _XKB_USE_CORE_KBD, state) != 0:
            return None
        return state[0]
    finally:
        x11.XCloseDisplay(display)


def _x11_layout():
    """The X server's (layout, variant, options): the layout and variant of
    the group that is on, out of setxkbmap's lists; the options apply to all."""
    fields = _setxkbmap_fields()
    if not fields.get("layout"):
        return None
    try:
        group = _x11_active_group() or 0
    except Exception as e:      # no libX11 to ask: the first layout listed
        logger.info(f"Could not read the active X11 layout group: {e}")
        group = 0
    variants = [v.strip() for v in (fields.get("variant") or "").split(",")]
    # A layout without a variant of its own has none; the first layout's
    # ("us(dvorak),ua" lists just "dvorak") must not be applied to it.
    variant = variants[group] if group < len(variants) else ""
    # _first falls back to the first layout if the group is out of range.
    return _first(fields["layout"], group), variant, fields.get("options", "")


def _system_layout():
    """The system-wide default (layout, variant, options), as set at install
    time or by localectl."""
    try:
        with open(_XORG_KEYBOARD_CONF) as f:
            conf = f.read()
        layout = re.search(r'Option\s+"XkbLayout"\s+"([^"]+)"', conf)
        if layout:
            variant = re.search(r'Option\s+"XkbVariant"\s+"([^"]*)"', conf)
            options = re.search(r'Option\s+"XkbOptions"\s+"([^"]*)"', conf)
            return (_first(layout.group(1)), _first(variant.group(1) if variant else ""),
                    options.group(1) if options else "")
    except OSError:
        pass
    try:
        with open(_DEBIAN_KEYBOARD) as f:
            values = dict(re.findall(r'^(\w+)="?([^"\n]*)"?', f.read(), re.M))
        if values.get("XKBLAYOUT"):
            return (_first(values["XKBLAYOUT"]), _first(values.get("XKBVARIANT", "")),
                    values.get("XKBOPTIONS", ""))
    except OSError:
        pass
    return None


def active_layout():
    """(layout, variant, XKB options) of the keyboard in use, or None if unknown."""
    try:
        found = None
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            found = _hyprland_layout()
        elif os.environ.get("SWAYSOCK") or os.environ.get("I3SOCK"):
            found = _sway_layout()
        else:
            desktop = _desktop()
            if desktop == "kde":
                layouts, index = _kde_layouts()
                if 0 <= index < len(layouts):
                    found = (*layouts[index], _options_or_defaults(_kde_options))
            elif desktop == "gnome":
                found = _gnome_layout()
            elif desktop == "cosmic":
                found = _cosmic_layout()
            elif (os.environ.get("XDG_SESSION_TYPE") or "").lower() == "x11":
                found = _x11_layout()
        if found and found[0]:
            return found
    except Exception as e:      # an odd desktop answer: the system default is next
        logger.info(f"Could not read the keyboard layout from the desktop: {e}")
    return _system_layout()


US = ("us", "", None)


def layout_key_events(text):
    """The key events [(evdev keycode, 1 down / 0 up)] that type *text* on the
    active layout, or on US when it can't be read. Raises UntypableText if
    the layout has no way to type some character."""
    layout = active_layout() or US
    try:
        planner = planner_for(layout)
    except ValueError as e:
        # A layout libxkbcommon doesn't know (a custom one, say): typed as
        # US, as ydotool did, rather than not at all.
        logger.warning(f"{e}; typing as US")
        layout, planner = US, planner_for(US)
    tokens, blocker = planner.plan_or_blocker(text)
    if tokens is None:
        raise UntypableText(layout, blocker)
    return tokens


def altgr_characters():
    """When the active keyboard's Right Alt is AltGr, up to two common
    characters it types only with AltGr ("@", "€"), which picking Right Alt
    as a dictation key takes away; None when it isn't AltGr (US, or Germany
    with lv3:ralt_alt or compose:ralt). An empty tuple when it is AltGr but
    types nothing listed here (US with lv3:ralt_switch)."""
    try:
        planner = planner_for(active_layout() or US)
    except (OSError, ValueError) as e:
        logger.debug(f"Could not tell whether Right Alt is AltGr: {e}")
        return None
    found = planner.altgr_only("@€{}[]\\|~")
    return None if found is None else tuple(found[:2])


# Where the letters sit on a US keyboard (evdev key codes).
_US_LETTER_KEYS = dict(zip("abcdefghijklmnopqrstuvwxyz",
                           (30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50,
                            49, 24, 25, 16, 19, 31, 20, 22, 47, 17, 45, 21, 44)))


def shortcut_keycode(letter):
    """The key that is *letter* on the active layout, for a shortcut: one
    TuxTalk presses, or a hotkey letter it listens for, since the desktops
    match their own shortcuts by letter too.

    On Dvorak the US B key types X, so Ctrl+B sent there would cut the
    selection. A layout without Latin letters (Russian) gets the US key, which
    is the key apps match their shortcuts on there."""
    layout = active_layout()
    if layout:
        try:
            entry = planner_for(layout)._keys.get(_lib().xkb_utf32_to_keysym(ord(letter)))
            if entry and not entry[1] and not entry[2]:     # no Shift, no AltGr
                return entry[0]
        except (OSError, ValueError) as e:
            # No libxkbcommon, or a layout it doesn't know: the US key, as
            # typing falls back to US there too.
            logger.debug(f"Could not find {letter!r} on the {layout} layout: {e}")
    return _US_LETTER_KEYS[letter]


class UntypableText(Exception):
    """Some character can't be typed on the active layout.
    args: ((layout, variant, options), the first character that can't be typed)."""
