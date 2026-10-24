"""Preferences' Test Hotkeys window: press each dictation key and see it light
up, with dictation held while the window is open.

Instead of stopping the dictation service (which releases the evdev grab and
causes XWayland phantom key-repeat floods), the service is put into "hotkey
test mode" with a flag file this window keeps fresh while it is open. In
that mode the grab stays active and hotkey presses are reported back as
D-Bus HotkeyPressed signals instead of starting or stopping a recording.
The flag goes stale by itself if this process dies, so dictation can't be
left switched off.
"""
import contextlib
import os

import dbus
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from .logger import setup_logger
from .ui_style import show_message

logger = setup_logger(__name__)

_NOT_TESTED = '<span color="#999999">Not tested</span>'
_WORKING = '<span color="#4CAF50">✓ Working!</span>'


def run_hotkey_test(parent, config):
    """The Test Hotkeys window over *parent*, for the hotkeys in *config*."""
    # The service tests against the SAVED hotkeys, but this window labels its
    # rows from the dropdowns. Changed without Apply, the wrong row would
    # light up, so the change is applied first.
    from .config import load_config, unapplied_hotkeys
    if unapplied_hotkeys(config, load_config().__dict__):
        show_message(parent,
                     Gtk.MessageType.INFO,
                     "Apply your hotkey change first",
                     "You changed a hotkey but haven't applied it yet. Click Apply, "
                     "then test — otherwise the test checks your previous hotkeys and "
                     "the wrong row will light up.")
        return
    dialog, mark_tested = _test_window(parent, config.get("hotkey", "F8"),
                                       config.get("toggle_hotkey", "F9"))
    with _hearing_hotkeys(mark_tested), _test_mode():
        dialog.show_all()
        dialog.run()
        dialog.destroy()


def _test_window(parent, hold_key, toggle_key):
    """The window, and a function that marks "hold" or "toggle" as working."""
    dialog = Gtk.Dialog(title="Test Hotkeys", transient_for=parent)
    dialog.set_default_size(450, 280)
    dialog.set_modal(True)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)
    content = dialog.get_content_area()
    content.set_margin_top(20)
    content.set_margin_bottom(20)
    content.set_margin_start(25)
    content.set_margin_end(25)
    content.set_spacing(15)

    instructions = Gtk.Label(xalign=0)
    instructions.set_markup('<span size="large"><b>Test Your Hotkeys</b></span>\n\n'
                            'Press each hotkey to verify it works:')
    content.pack_start(instructions, False, False, 0)
    status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    status_box.set_margin_top(10)
    rows = {}
    for name, key, what in (("hold", hold_key, "Hold-to-talk"),
                            ("toggle", toggle_key, "Tap-to-toggle")):
        label = Gtk.Label(xalign=0)
        label.set_markup(f"<b>{key}</b> ({what}): {_NOT_TESTED}")
        status_box.pack_start(label, False, False, 0)
        rows[name] = (label, f"<b>{key}</b> ({what}): ")
    content.pack_start(status_box, False, False, 0)

    info_label = Gtk.Label(xalign=0)
    info_label.set_markup('<i>Dictation is paused while testing.\nClose this dialog to resume.</i>')
    info_label.set_line_wrap(True)
    info_label.set_margin_top(10)
    content.pack_start(info_label, False, False, 0)
    close_button = Gtk.Button(label="Close")
    close_button.connect("clicked", lambda _w: dialog.destroy())
    dialog.add_action_widget(close_button, Gtk.ResponseType.CLOSE)

    def mark_tested(key_name):
        if key_name in rows:
            label, head = rows[key_name]
            label.set_markup(head + _WORKING)
    return dialog, mark_tested


@contextlib.contextmanager
def _hearing_hotkeys(on_key):
    """While inside, the service's HotkeyPressed signals ("hold", "toggle",
    sent through the tray's D-Bus service) go to *on_key*."""
    from .dbus_service import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH

    def heard(key_name):
        logger.debug(f"Hotkey test: TuxTalk heard {key_name}")
        on_key(key_name)
    match = dbus.SessionBus().add_signal_receiver(
        heard, signal_name="HotkeyPressed", dbus_interface=DBUS_INTERFACE,
        bus_name=DBUS_NAME, path=DBUS_PATH)
    try:
        yield
    finally:
        try:
            match.remove()
        except Exception as e:
            logger.info(f"Hotkey test: the D-Bus listener was already gone ({e})")


@contextlib.contextmanager
def _test_mode():
    """Hotkey test mode, for as long as the flag is fresh: kept fresh while
    inside, and taken away after, whatever happened (service_launcher)."""
    from .service_launcher import HOTKEY_TEST_REFRESH_S, hotkey_test_flag_path
    flag = hotkey_test_flag_path()

    def refresh():
        try:
            os.makedirs(os.path.dirname(flag), exist_ok=True)
            with open(flag, "a"):
                pass
            os.utime(flag)
        except OSError as e:
            logger.warning(f"Hotkey test: could not refresh the test flag: {e}")
        return True     # keep the timer running

    refresh()
    timer = GLib.timeout_add_seconds(HOTKEY_TEST_REFRESH_S, refresh)
    try:
        yield
    finally:
        GLib.source_remove(timer)
        try:
            os.remove(flag)
        except OSError as e:
            logger.info(f"Hotkey test: the test flag wasn't removed ({e})")
