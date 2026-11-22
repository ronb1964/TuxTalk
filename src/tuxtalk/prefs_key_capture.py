"""Preferences' "Other key or combination…": press the keys to use for a
dictation key, beyond the ones the dropdowns list (Alt+D, Ctrl+Shift+K, F13).

What can be picked is dictation_keys' to say (refusal, warning); this window
only reads the keys pressed and shows the answer. refusal_for gives the
answer for this computer, so a key picked from a dropdown is held to the
same rules.
"""
import os

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from . import dictation_keys  # noqa: E402

# The modifiers GTK reports with a key, in the order dictation_keys names them.
_MASKS = (("Ctrl", Gdk.ModifierType.CONTROL_MASK), ("Alt", Gdk.ModifierType.MOD1_MASK),
          ("Super", Gdk.ModifierType.SUPER_MASK), ("Shift", Gdk.ModifierType.SHIFT_MASK))
# Keys that are only modifiers: pressed on their own, the combo isn't finished.
_MODIFIER_NAMES = {"Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R",
                   "Shift_L", "Shift_R", "Meta_L", "Meta_R", "ISO_Level3_Shift"}
# Keys the window keeps for itself, so it works from the keyboard alone: Tab
# moves to the buttons, Enter and Space press them, Escape cancels. None of
# them can be a dictation key.
_WINDOW_KEYS = {"Tab", "ISO_Left_Tab", "Return", "KP_Enter", "space", "Escape"}
# xkb keycode = evdev keycode + 8, as GTK reports hardware keycodes.
_EVDEV_OFFSET = 8


def key_name(keyval: int, state: int, hardware_keycode: int = 0) -> str | None:
    """The dictation key name for a key press: "Alt+D", "F13", or the key's
    own name for one TuxTalk will refuse ("A"); None for a modifier pressed
    on its own, which only starts a combo, and for the window's own keys. A
    letter of a layout without Latin letters (Russian) is named by the US
    letter on its key, which is the key the hotkey loop listens for there
    (keyboard_layout.shortcut_keycode)."""
    name = Gdk.keyval_name(keyval) or ""
    if name in _MODIFIER_NAMES or name in _WINDOW_KEYS:
        return None
    if name == "Scroll_Lock":
        name = "Scroll Lock"
    elif len(name) == 1 and name.isalpha():
        name = name.upper()
    elif Gdk.keyval_to_unicode(keyval) and chr(Gdk.keyval_to_unicode(keyval)).isalpha():
        from .key_commands import US_LETTER_KEYS
        by_code = {code: letter.upper() for letter, code in US_LETTER_KEYS.items()}
        name = by_code.get(hardware_keycode - _EVDEV_OFFSET, name)
    mods = [mod for mod, mask in _MASKS if state & mask]
    return "+".join([*mods, name])


def refusal_for(name: str, other: str, config, typing_mods=None) -> str | None:
    """dictation_keys.refusal for *name* on this computer, beside *other*
    (the other dictation key): with *config*'s voice editing and shortcuts,
    and the modifiers that type on the active layout (*typing_mods*, from
    typing_modifiers; read here when None). The layout is asked only for a
    combo: the desktop can take seconds to answer, and GTK waits."""
    shortcuts = (("cheat sheet", config.get("voice_commands_hotkey") or ""),
                 ("paste last dictation", config.get("paste_last_hotkey") or ""))
    if typing_mods is None:
        typing_mods = typing_modifiers() if "+" in name else frozenset()
    return dictation_keys.refusal(name, other, bool(config.get("voice_editing")),
                                  typing_mods, shortcuts)


def typing_modifiers() -> frozenset:
    """The combo modifiers that type characters on the active layout
    (combo_keys.typing_modifiers)."""
    from . import combo_keys
    from .keyboard_layout import level3_keys
    return combo_keys.typing_modifiers(level3_keys())


def _desktop_holds_keys() -> bool:
    """Whether this desktop keeps a dictation key from the focused app
    (hotkeys._claim_desktop_hotkeys): KDE, COSMIC, X11, GNOME with the
    TuxTalk extension, and the Flatpak, whose keys the desktop owns."""
    from . import cosmic_hotkeys, desktop_detect, kwin_hotkeys, x11_hotkeys
    if (os.environ.get("FLATPAK_ID") or kwin_hotkeys.should_claim()
            or cosmic_hotkeys.should_claim() or x11_hotkeys.should_claim()):
        return True
    if not desktop_detect.is_gnome():
        return False
    from .extension_helper import EXTENSION_UUID
    from .gnome_extensions import is_extension_enabled
    return is_extension_enabled(EXTENSION_UUID)


def ask(parent, role: str, other: str, config) -> str | None:
    """Ask for the *role* dictation key ("hold" or "toggle") by pressing it;
    the name picked, or None when cancelled. *other* is the other dictation
    key, *config* the settings it's checked against (refusal_for)."""
    dialog = Gtk.Dialog(title=f"Your {role} key", transient_for=parent, modal=True)
    cancel = dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    use = dialog.add_button("Use This Key", Gtk.ResponseType.OK)
    use.set_sensitive(False)
    box = dialog.get_content_area()
    for side in ("start", "end", "top", "bottom"):
        getattr(box, f"set_margin_{side}")(16)
    box.set_spacing(10)
    box.add(Gtk.Label(label=f"Press the key or keys you want to {role} for dictation,\n"
                            "for example Alt+D or Ctrl+Shift+K. Tab goes to the buttons.",
                      xalign=0))
    shown = Gtk.Label(label="Waiting for keys…")
    shown.get_style_context().add_class("title-2")
    box.add(shown)
    note = Gtk.Label(xalign=0, wrap=True, max_width_chars=55)
    note.get_style_context().add_class("dim-label")
    box.add(note)
    picked = []
    held_back = _desktop_holds_keys()
    typing_mods = typing_modifiers()        # once, not on every key pressed

    def on_key(_widget, event):
        """Show a finished key or combination pressed, and why it can't be
        used or what to know about it; modifiers alone wait for the key."""
        name = key_name(event.keyval, event.state, event.hardware_keycode)
        if name is None:
            return False                # a modifier so far, or the window's own key
        picked[:] = [dictation_keys.canonical(name)]
        shown.set_text(picked[0])
        why = refusal_for(picked[0], other, config, typing_mods)
        said = why or dictation_keys.warning(picked[0], held_back) or ""
        note.set_text(said)
        use.set_sensitive(why is None)
        # GTK3 has no live region: the key and the answer go on both buttons'
        # descriptions, read with whichever has the focus (Use This Key is
        # greyed out after a refusal, so Cancel may be the one).
        for button in (use, cancel):
            button.get_accessible().set_description(f"{picked[0]}. {said}".strip())
        return True                     # the key is only being picked
    dialog.connect("key-press-event", on_key)
    dialog.show_all()
    try:
        return picked[0] if dialog.run() == Gtk.ResponseType.OK and picked else None
    finally:
        dialog.destroy()
