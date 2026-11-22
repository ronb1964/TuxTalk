"""Which keys the first-run hotkey test names, accepts, logs and saves.

The test reads keys two ways, through evdev as the dictation service does
and through GTK for a keyboard evdev can't open, and both must come out with
the names the settings use ("F8", "Right Ctrl"). Nothing here opens a
window, so all of it can be tested without one.
"""
from . import dictation_keys, secure_input
from .config import CTRL_SPACE, HOTKEY_CHOICES

# The keys the test may write to the log. Without the hotkey helper it reads
# every keyboard, so a password typed in another window must not get there.
# Scroll Lock is among HOTKEY_CHOICES, the name both readers give it.
_TEST_KEYS = {f"F{n}" for n in range(1, 25)} | {"Escape", "Pause"} | set(HOTKEY_CHOICES)

# GTK's names for the dictation keys that aren't F-keys. ISO_Level3_Shift
# (AltGr) is Right Alt only from that key (gtk_key_name).
_GTK_KEY_NAMES = {"Control_R": "Right Ctrl", "Alt_R": "Right Alt", "Scroll_Lock": "Scroll Lock"}
_RIGHT_ALT_KEYCODE = 100 + 8        # evdev's KEY_RIGHTALT, as X11 and GDK count it
_SHIFT_CODES = frozenset({42, 54})   # evdev's Left and Right Shift


def loggable_key(keyname):
    """Only test keys: without the helper the test hears letters typed in any window."""
    return keyname if keyname in _TEST_KEYS else "another key"


def captured_key_problem(keyname, other_key):
    """Why *keyname* can't become a hotkey, or None if it can: the hotkey
    test takes the keys Preferences lists, and F13 to F24 (a combination is
    Preferences' to take, pressed in its own window)."""
    if keyname not in HOTKEY_CHOICES and keyname not in dictation_keys.SINGLE_KEYS:
        return ("That key can't be used for dictation. Pick F1 to F24, Right Ctrl, "
                "Right Alt, Scroll Lock or Left Ctrl+Space.")
    if keyname == other_key:
        return f"{keyname} is already your other hotkey. Pick a different key."
    return None


def hotkeys_to_save(config, hold, toggle, confirmed):
    """The (hold, toggle) keys to save when the hotkey test closes.

    Continue saves what the dialog shows. Closing it any other way keeps keys
    already saved and fills in only blanks: the settings start with no hotkey,
    and blank keys leave the service watching no key at all. A key
    Preferences took pressed (Alt+D) is kept like any other.
    """
    if not confirmed:
        hold = config.hotkey or hold
        toggle = config.toggle_hotkey or toggle
    if not hold or dictation_keys.problem(hold):
        hold = "F8"
    same = dictation_keys.same_last_key
    if not toggle or dictation_keys.problem(toggle) or same(hold, toggle):
        toggle = "F9" if not same(hold, "F9") else "F8"
    return hold, toggle


def evdev_key_name(code):
    """The test's name for evdev key *code*: a dictation key as the settings
    name it, a letter, or GTK's name for Esc, space, Tab, Enter and
    Backspace; None for any other key. Space alone is "space": only with
    Left Ctrl held is it Left Ctrl+Space (evdev_key_action)."""
    from evdev import ecodes
    for name, key in HOTKEY_CHOICES.items():
        if name != CTRL_SPACE and code == ecodes.ecodes[key]:
            return name
    for n in range(13, 25):                 # F13-F24, which no dropdown lists
        if code == ecodes.ecodes[f"KEY_F{n}"]:
            return f"F{n}"
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if code == getattr(ecodes, f"KEY_{c}", None):
            return c.lower()
    special = {
        ecodes.KEY_ESC: "Escape",
        ecodes.KEY_SPACE: "space",
        ecodes.KEY_TAB: "Tab",
        ecodes.KEY_ENTER: "Return",
        ecodes.KEY_BACKSPACE: "BackSpace",
    }
    return special.get(code)


def gtk_key_name(accelerator_name, hardware_keycode=None, ctrl=False):
    """The test's name for a key GTK names *accelerator_name*
    (Gtk.accelerator_name): "Control_R" is "Right Ctrl". A layout that makes
    Right Alt its AltGr calls it ISO_Level3_Shift, but so do the options
    that put AltGr on Caps Lock or the Menu key: those aren't Right Alt, so
    the key's *hardware_keycode* decides. Space with *ctrl* held is Left
    Ctrl+Space: GTK can't tell which Ctrl, so this fallback reader takes
    either, and only the keyboard reader can call the key working."""
    if accelerator_name == "ISO_Level3_Shift" and hardware_keycode == _RIGHT_ALT_KEYCODE:
        return "Right Alt"
    if accelerator_name == "space" and ctrl:
        return CTRL_SPACE
    return _GTK_KEY_NAMES.get(accelerator_name, accelerator_name)


def evdev_key_action(event, held_modifiers, modifier_codes, code_to_name, pair=None,
                     combo_names=None):
    """(keyname, "press" or "release") for one evdev event, or None to ignore it.

    Tracks the *modifier_codes* in *held_modifiers*, so Alt+F8 doesn't count
    as F8, as on the GTK side; Shift is tracked for combos, and never stops
    a key on its own. Auto-repeat (value 2) is ignored. Right Ctrl and Right
    Alt are keys of their own too (HOTKEY_CHOICES), when pressed alone. With
    *pair* (a combo_keys.Presses kept across events, knowing the combo
    dictation keys), a combo's key counts only under its modifiers, named
    as *combo_names* names its code (Left Ctrl+Space for Space when None),
    and letting go of a modifier first is its release, as the dictation
    service reads them.
    """
    if event.code in modifier_codes:
        if event.value == 1:
            held_modifiers.add(event.code)
        elif event.value == 0:
            held_modifiers.discard(event.code)
    if pair is None:
        return _key_action(event, held_modifiers, code_to_name)
    if combo_names is None:
        from .combo_keys import LEFT_CTRL_SPACE
        combo_names = {LEFT_CTRL_SPACE.code: CTRL_SPACE}
    actions = [_combo_action(each, held_modifiers, pair, combo_names[each.code])
               if each.code in combo_names
               else _key_action(each, held_modifiers, code_to_name)
               for each in pair.events(event, held_modifiers, tuple(combo_names))]
    return next((action for action in actions if action), None)


def _combo_action(event, held_modifiers, pair, name):
    """Combo dictation key *name*'s press or release, from its key that
    *pair* passed on: not with another modifier held too (Ctrl+Alt+Space
    for Left Ctrl+Space), which the service wouldn't take either, nor that
    press's release."""
    if event.value not in (0, 1):
        return None
    if held_modifiers - pair.held_with(event.code) - _SHIFT_CODES - {event.code}:
        pair.forget()
        return None
    return name, ("press" if event.value == 1 else "release")


def _key_action(event, held_modifiers, code_to_name):
    """evdev_key_action for any key but a combo's."""
    if event.value not in (0, 1) or held_modifiers - {event.code} - _SHIFT_CODES:
        return None
    keyname = code_to_name(event.code)
    if not keyname:
        return None
    return keyname, ("press" if event.value == 1 else "release")


def service_hears(keyboards, keyname):
    """Whether the dictation service will hear *keyname*, with *keyboards*
    being what the test could open. A hotkey helper too old for Right Ctrl
    and Right Alt (secure_input.hears_key) won't report those, and the
    service reads the keys through a running helper even where the test
    could open the keyboards itself (input_backends.get_input_backend): an
    older install still in the "input" group, say. An installed helper
    that isn't answering (stopped) hears nothing, though it's in the list."""
    if not keyboards or not all(getattr(k, "connected", True) for k in keyboards):
        return False
    if secure_input.key_since(keyname) is None:
        return True
    version = next((k.helper_version for k in keyboards if hasattr(k, "helper_version")), None)
    if version is None:
        version = secure_input.running_version()
    return secure_input.hears_key(version, keyname)
