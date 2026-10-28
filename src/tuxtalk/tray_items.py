"""Pieces of the tray's menu: the Dictation on/off item and the icon-name
lookup the menu's icons share."""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


def themed_icon(*names):
    """The first of these icon names the current icon theme has, or None."""
    theme = Gtk.IconTheme.get_default()
    if theme is None:
        return None
    return next((name for name in names if theme.has_icon(name)), None)


class DictationItem(Gtk.ImageMenuItem):
    """The Dictation on/off item: "Dictation on" with a checkmark, or
    "Dictation off" without. The words say the state, so a screen reader
    reads it on every desktop, where a check item's state is drawn by each
    panel its own way (KDE: a faint box in a column of its own).

    Keeps get_active()/set_active() for the tray's status poll: set_active()
    only redraws; a click flips the state and calls on_toggle(item)."""

    def __init__(self, on_toggle):
        super().__init__(label="Dictation off")
        self._active = False
        self._on_toggle = on_toggle
        self._check_icon = themed_icon("object-select-symbolic", "emblem-ok-symbolic")
        self.set_always_show_image(True)
        self.connect("activate", self._clicked)
        self._redraw()

    def get_active(self):
        return self._active

    def set_active(self, active):
        self._active = bool(active)
        self._redraw()

    def _clicked(self, _item):
        self.set_active(not self._active)
        self._on_toggle(self)

    def _redraw(self):
        self.set_label("Dictation on" if self._active else "Dictation off")
        if self._active and self._check_icon:
            self.set_image(Gtk.Image.new_from_icon_name(self._check_icon, Gtk.IconSize.MENU))
        else:
            self.set_image(Gtk.Image())     # an empty one: the icon column stays blank
