"""Which keys the first-run hotkey test names, accepts, logs and saves.

The test reads keys two ways, through evdev as the dictation service does
and through GTK for a keyboard evdev can't open, and both must come out with
the names the settings use ("F8", "Right Ctrl"). Nothing here opens a
window, so all of it can be tested without one.
"""
from . import secure_input
from .config import HOTKEY_CHOICES

# The keys the test may write to the log. Without the hotkey helper it reads
# every keyboard, so a password typed in another window must not get there.
# Scroll Lock is among HOTKEY_CHOICES, the name both readers give it.
_TEST_KEYS = {f"F{n}" for n in range(1, 25)} | {"Escape", "Pause"} | set(HOTKEY_CHOICES)

# GTK's names for the dictation keys that aren't F-keys.
_GTK_KEY_NAMES = {"Control_R": "Right Ctrl", "Alt_R": "Right Alt",
                  "ISO_Level3_Shift": "Right Alt", "Scroll_Lock": "Scroll Lock"}


def loggable_key(keyname):
    """Only test keys: without the helper the test hears letters typed in any window."""
    return keyname if keyname in _TEST_KEYS else "another key"


def captured_key_problem(keyname, other_key):
    """Why *keyname* can't become a hotkey, or None if it can: the hotkey
    test takes the keys Preferences offers."""
    if keyname not in HOTKEY_CHOICES:
        return ("That key can't be used for dictation. Pick F1 to F12, Right Ctrl, "
                "Right Alt or Scroll Lock.")
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
    Backspace; None for any other key."""
    from evdev import ecodes
    for name, key in HOTKEY_CHOICES.items():
        if code == ecodes.ecodes[key]:
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


def gtk_key_name(accelerator_name):
    """The test's name for a key GTK names *accelerator_name*
    (Gtk.accelerator_name): "Control_R" is "Right Ctrl"."""
    return _GTK_KEY_NAMES.get(accelerator_name, accelerator_name)


def evdev_key_action(event, held_modifiers, modifier_codes, code_to_name):
    """(keyname, "press" or "release") for one evdev event, or None to ignore it.

    Tracks Alt, Ctrl and Super in *held_modifiers*, so Alt+F8 doesn't count as
    F8, as on the GTK side. Auto-repeat (value 2) is ignored. Right Ctrl and
    Right Alt are keys of their own too (HOTKEY_CHOICES), when pressed alone.
    """
    if event.code in modifier_codes:
        if event.value == 1:
            held_modifiers.add(event.code)
        elif event.value == 0:
            held_modifiers.discard(event.code)
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
    older install still in the "input" group, say."""
    if not keyboards:
        return False
    if keyname not in secure_input.RIGHT_MODIFIER_KEYS:
        return True
    version = next((k.helper_version for k in keyboards if hasattr(k, "helper_version")), None)
    if version is None:
        version = secure_input.running_version()
    return secure_input.hears_key(version, keyname)
