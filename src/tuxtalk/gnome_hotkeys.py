"""The hotkeys in GNOME's accelerator syntax, for the GNOME Shell extension.

Same leak as on KDE (see kwin_hotkeys): the service reads the keyboard below
the desktop, so GNOME Shell passes F8 on to the focused app as well, and a
terminal prints "~". Only code running inside GNOME Shell can stop that, and
the D-Bus route into it (org.gnome.Shell.GrabAccelerator) is restricted to
GNOME's own services. So the service sends this list to the tray
(NotifyClaimedHotkeys), the tray relays it (HotkeysChanged, and GetStatus),
and the extension grabs the keys with global.display.grab_accelerator while
the service runs. A grabbed key never reaches the focused app; evdev still
sees it, so recording is unchanged.

Which keys may be claimed is decided once, in kwin_hotkeys.wanted_shortcuts,
so the two desktops can't drift apart: F-keys, Scroll Lock and modifier
combos, never a bare letter.
"""

from .kwin_hotkeys import QT_KEYSYMS, wanted_shortcuts

# Qt modifier names (what wanted_shortcuts produces) -> GTK accelerator syntax.
_GNOME_MODIFIERS = {"Ctrl": "<Control>", "Shift": "<Shift>", "Alt": "<Alt>", "Meta": "<Super>"}


def accelerator(qt_sequence):
    """'Ctrl+Alt+V' -> '<Control><Alt>v'; 'F8' -> 'F8'; 'ScrollLock' -> 'Scroll_Lock'."""
    *mods, key = qt_sequence.split("+")
    # GTK accelerators name letters by their lowercase keysym.
    key = key.lower() if len(key) == 1 else QT_KEYSYMS.get(key, key)
    return "".join(_GNOME_MODIFIERS[m] for m in mods) + key


def accelerators(cfg):
    """Every hotkey in *cfg* the extension should hold back, as accelerators."""
    return [accelerator(key) for _name, _text, key in wanted_shortcuts(cfg)]
