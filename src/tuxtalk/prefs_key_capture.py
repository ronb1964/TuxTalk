"""Preferences' "Other key or combination…": press the keys to use for a
dictation key, beyond the ones the dropdowns list (Alt+D, Ctrl+Shift+K, F13).

What can be picked is dictation_keys' to say (refusal, warning); this window
only reads the keys pressed and shows the answer.
"""
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


def key_name(keyval: int, state: int) -> str | None:
    """The dictation key name for a key press: "Alt+D", "F13", or the key's
    own name for one TuxTalk will refuse ("A"); None for a modifier pressed
    on its own, which only starts a combo, and for the window's own keys."""
    name = Gdk.keyval_name(keyval) or ""
    if name in _MODIFIER_NAMES or name in _WINDOW_KEYS:
        return None
    if name == "Scroll_Lock":
        name = "Scroll Lock"
    elif len(name) == 1 and name.isalpha():
        name = name.upper()
    mods = [mod for mod, mask in _MASKS if state & mask]
    return "+".join([*mods, name])


def ask(parent, role: str, other: str, voice_editing: bool) -> str | None:
    """Ask for the *role* dictation key ("hold" or "toggle") by pressing it;
    the name picked, or None when cancelled. *other* is the other dictation
    key, *voice_editing* whether Shift with the key is a voice edit."""
    from .keyboard_layout import left_alt_types
    dialog = Gtk.Dialog(title=f"Your {role} key", transient_for=parent, modal=True)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
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
    alt_types = left_alt_types()

    def on_key(_widget, event):
        name = key_name(event.keyval, event.state)
        if name is None:
            return False                # a modifier so far, or the window's own key
        picked[:] = [dictation_keys.canonical(name)]
        shown.set_text(picked[0])
        why = dictation_keys.refusal(picked[0], other, voice_editing, alt_types)
        note.set_text(why or dictation_keys.warning(picked[0]) or "")
        use.set_sensitive(why is None)
        return True                     # the key is only being picked
    dialog.connect("key-press-event", on_key)
    dialog.show_all()
    try:
        return picked[0] if dialog.run() == Gtk.ResponseType.OK and picked else None
    finally:
        dialog.destroy()
