"""Which keys the first-run hotkey test names, accepts, logs and saves.

The test reads keys two ways, through evdev as the dictation service does
and through GTK for a keyboard evdev can't open, and both must come out with
the names the settings use ("F8", "Right Ctrl"). Nothing here opens a
window, so all of it can be tested without one.
"""
from . import secure_input
from .config import CTRL_SPACE, HOTKEY_CHOICES

# The keys the test may write to the log. Without the hotkey helper it reads
# every keyboard, so a password typed in another window must not get there.
# Scroll Lock is among HOTKEY_CHOICES, the name both readers give it.
_TEST_KEYS = {f"F{n}" for n in range(1, 25)} | {"Escape", "Pause"} | set(HOTKEY_CHOICES)

# GTK's names for the dictation keys that aren't F-keys. ISO_Level3_Shift
# (AltGr) is Right Alt only from that key (gtk_key_name).
_GTK_KEY_NAMES = {"Control_R": "Right Ctrl", "Alt_R": "Right Alt", "Scroll_Lock": "Scroll Lock"}
_RIGHT_ALT_KEYCODE = 100 + 8        # evdev's KEY_RIGHTALT, as X11 and GDK count it


def loggable_key(keyname):
    """Only test keys: without the helper the test hears letters typed in any window."""
    return keyname if keyname in _TEST_KEYS else "another key"


def captured_key_problem(keyname, other_key):
    """Why *keyname* can't become a hotkey, or None if it can: the hotkey
    test takes the keys Preferences offers."""
    if keyname not in HOTKEY_CHOICES:
        return ("That key can't be used for dictation. Pick F1 to F12, Right Ctrl, "
                "Right Alt, Scroll Lock or Left Ctrl+Space.")
    if keyname == other_key:
        return f"{keyname} is already your other hotkey. Pick a different key."
    return None


def hotkeys_to_save(config, hold, toggle, confirmed):
    """The (hold, toggle) keys to save when the hotkey test closes.

    Continue saves what the dialog shows. Closing it any other way keeps keys
    already saved and fills in only blanks: the settings start with no hotkey,
    and blank keys leave the service watching no key at all.
    """
    if not confirmed:
        hold = config.hotkey or hold
        toggle = config.toggle_hotkey or toggle
    if hold not in HOTKEY_CHOICES:
        hold = "F8"
    if toggle not in HOTKEY_CHOICES or toggle == hold:
        toggle = "F9" if hold != "F9" else "F8"
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


def evdev_key_action(event, held_modifiers, modifier_codes, code_to_name, pair=None):
    """(keyname, "press" or "release") for one evdev event, or None to ignore it.

    Tracks Alt, Ctrl and Super in *held_modifiers*, so Alt+F8 doesn't count as
    F8, as on the GTK side. Auto-repeat (value 2) is ignored. Right Ctrl and
    Right Alt are keys of their own too (HOTKEY_CHOICES), when pressed alone.
    With *pair* (a ctrl_space.Presses kept across events), Space counts only
    under Left Ctrl, as Left Ctrl+Space, and letting go of Left Ctrl first is
    its release, as the dictation service reads them.
    """
    if event.code in modifier_codes:
        if event.value == 1:
            held_modifiers.add(event.code)
        elif event.value == 0:
            held_modifiers.discard(event.code)
    if pair is None:
        return _key_action(event, held_modifiers, code_to_name)
    from . import ctrl_space
    actions = [_pair_action(each, held_modifiers, pair) if each.code == ctrl_space.KEY
               else _key_action(each, held_modifiers, code_to_name)
               for each in pair.events(event, held_modifiers, (ctrl_space.KEY,))]
    return next((action for action in actions if action), None)


def _pair_action(event, held_modifiers, pair):
    """Left Ctrl+Space's press or release, from a Space *pair* passed on:
    not with another modifier held too (Ctrl+Alt+Space), which the service
    wouldn't take either, nor that press's release."""
    from . import ctrl_space
    if event.value not in (0, 1):
        return None
    if held_modifiers - {ctrl_space.MODIFIER}:
        pair.forget()
        return None
    return CTRL_SPACE, ("press" if event.value == 1 else "release")


def _key_action(event, held_modifiers, code_to_name):
    """evdev_key_action for any key but the pair's Space."""
    if event.value not in (0, 1) or held_modifiers - {event.code}:
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
    if keyname not in secure_input.KEYS_SINCE:
        return True
    version = next((k.helper_version for k in keyboards if hasattr(k, "helper_version")), None)
    if version is None:
        version = secure_input.running_version()
    return secure_input.hears_key(version, keyname)
