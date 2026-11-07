"""Key presses as the keyboard makes them, for tests of what Tab, the arrows
and Page Down do in a window on the hidden test display: GTK hands each one
to the widget with the focus, key bindings and focus moves included, which a
key-press-event emitted on one widget skips."""
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402


def settle():
    """Let GTK finish what the last event started (a focus move, a scroll)."""
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def press(window, keyval, state=0):
    """Press and release *keyval* (with the modifiers in *state*) in *window*."""
    seat = Gdk.Display.get_default().get_default_seat()
    keymap = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
    code = keymap.get_entries_for_keyval(keyval)[1][0].keycode
    for kind in (Gdk.EventType.KEY_PRESS, Gdk.EventType.KEY_RELEASE):
        event = Gdk.Event.new(kind)
        event.window, event.send_event = window.get_window(), True
        event.keyval, event.state, event.hardware_keycode = keyval, state, code
        event.set_device(seat.get_keyboard())
        Gtk.main_do_event(event)
        settle()
