"""Pieces of the tray's menu: the Dictation on/off item, and the icon-name
lookup and the relabelling the menu's items share."""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


def themed_icon(*names):
    """The first of these icon names the current icon theme has, or None."""
    theme = Gtk.IconTheme.get_default()
    if theme is None:
        return None
    return next((name for name in names if theme.has_icon(name)), None)


def relabel(item, label):
    """Give menu *item* *label* unless it has it already: the desktop is
    sent every change, even to the same words."""
    if item.get_label() != label:
        item.set_label(label)


class DictationItem(Gtk.ImageMenuItem):
    """The Dictation on/off item, named for what a click does: "Turn
    Dictation Off" (with a checkmark, as it's on) or "Turn Dictation On".
    "Dictation off" read as an action as easily as a state. The words carry
    the state, so a screen reader reads it on every desktop, where a check
    item's state is drawn by each panel its own way (KDE: a faint box in a
    column of its own).

    Keeps get_active()/set_active() for the tray's status poll: set_active()
    only redraws; a click flips the state and calls on_toggle(item)."""

    def __init__(self, on_toggle):
        super().__init__(label="Turn Dictation On")
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
        self.set_label("Turn Dictation Off" if self._active else "Turn Dictation On")
        if self._active and self._check_icon:
            self.set_image(Gtk.Image.new_from_icon_name(self._check_icon, Gtk.IconSize.MENU))
        else:
            self.set_image(Gtk.Image())     # an empty one: the icon column stays blank
