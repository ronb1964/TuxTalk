"""The first-run hotkey test: press each dictation key and see it work, or
pick another key, before setup goes on to the downloads.

The keys are read through evdev, as the dictation service reads them (or
through the hotkey helper when the keyboards can't be opened), and through
GTK as well, so a key the compositor grabs before GTK sees it still tests
as working. Preferences has its own test (prefs_hotkey_test), run through
the dictation service instead.
"""

import functools
import threading
import time

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from .config import HOTKEY_CHOICES
from .hotkey_labels import layout_label
from .hotkey_test_devices import flush_pending_events, hotkey_test_helper, read_ready
from .hotkey_test_keys import (captured_key_problem, evdev_key_action, evdev_key_name,
                               gtk_key_name, hotkeys_to_save, loggable_key, service_hears)
from .keyboard_devices import (RESCAN_SECONDS, close_devices, devices_with_events,
                               open_new_keyboards)
from .logger import setup_logger

logger = setup_logger(__name__)

_TITLES = {"hold": "Push-to-talk", "toggle": "Toggle mode"}
_CAPTURE_PROMPT = ('<span background="#FFA500" color="white"> Press F1 to F12, Right Ctrl, '
                   'Right Alt or Scroll Lock to set the hotkey... (ESC to cancel) </span>')
_CAPTURE_WORDS = "Press F1 to F12, Right Ctrl, Right Alt or Scroll Lock, or Esc to cancel."

# A GTK key event with Alt (MOD1), Ctrl or Super held is not a dictation key:
# GNOME delivers Alt+F8 (begin-resize binding) to focused windows, and
# stripping the modifiers would make it look like a plain F8 press.
_NO_MODIFIER_MASK = (Gdk.ModifierType.MOD1_MASK |
                     Gdk.ModifierType.CONTROL_MASK |
                     Gdk.ModifierType.SUPER_MASK)
# The modifier Right Ctrl and Right Alt each set themselves, which their
# own release carries.
_OWN_MODIFIER = {"Right Ctrl": Gdk.ModifierType.CONTROL_MASK,
                 "Right Alt": Gdk.ModifierType.MOD1_MASK}


def _hotkey_test_row(title, key):
    """(row, label, button, status) for one key in the hotkey test: "Push-to-
    talk: F8", a Change Key button a screen reader names by its key, and how
    the key tested."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    label = Gtk.Label(xalign=0)
    label.set_markup(f'<b>{title}:</b> {layout_label(key)}')
    label.set_size_request(200, -1)
    button = Gtk.Button(label="Change Key")
    button.set_size_request(120, -1)
    button.get_accessible().set_name(f"Change the {title.lower()} key")
    status = Gtk.Label(xalign=0)
    status.set_markup('<span color="#999999">Not tested</span>')
    row.pack_start(label, False, False, 0)
    row.pack_start(button, False, False, 0)
    row.pack_start(status, True, True, 0)
    return row, label, button, status


def _key_seen(service_can_read):
    """A tested key's status, as (markup, words for a screen reader). Only
    this dialog saw the key when the dictation service can't read the
    keyboards, or reads them through a hotkey helper too old for the key
    (hotkey_test_keys.service_hears)."""
    if service_can_read:
        return '<span color="#4CAF50">✓ Working!</span>', "Working"
    return '<span color="#FF9800">✓ Seen, not active yet</span>', "Seen, not active yet"


def _hotkey_test_notice(service_can_read):
    """What makes the hotkeys work when the service can't read the keyboard
    yet, or None when it can."""
    if service_can_read:
        return None
    from tuxtalk import uinput_helper
    from tuxtalk import secure_input
    if uinput_helper.input_group_pending_restart():
        return ("TuxTalk can't read the keyboard yet. These keys will work "
                "once you restart your computer to finish the typing setup.")
    if secure_input.can_install()[0]:
        # The hotkey helper: its hotkeys work as soon as it's installed.
        return ("TuxTalk can't read the keyboard yet. These keys will work after "
                "Preferences, Advanced, Fix Typing Permissions.")
    return ("TuxTalk can't read the keyboard yet. These keys will work after "
            "Preferences, Advanced, Fix Typing Permissions, and a restart of "
            "your computer.")


def _evdev():
    """evdev's (InputDevice, ecodes, list_devices), or None without evdev:
    the test then hears the keys through GTK alone."""
    try:
        from evdev import InputDevice, ecodes, list_devices
    except ImportError:
        logger.warning("evdev not available - falling back to GTK key events")
        return None
    logger.info("evdev available for hotkey testing")
    return InputDevice, ecodes, list_devices


def _stop_dictation_service():
    """Stop a dictation service started since the tray ended the one that
    ran (gracefully, before setup): none should be. It would hear F8 and F9
    before the test can."""
    try:
        from tuxtalk.service_launcher import find_service_pids, stop_dictation_service
        if not find_service_pids():
            return
        logger.warning("A dictation service started during setup; stopping it")
        stop_dictation_service(force=True)
    except Exception as e:
        logger.warning(f"Error killing service: {e}")


class HotkeyTestDialog:
    """The test's window and what it has seen: the hold and toggle keys (as
    picked so far), which one Change Key waits for, and the keyboards read.

    Key events come from two readers, evdev on its own thread and GTK on
    the main one; both end in _on_key on GTK's thread."""

    def __init__(self, config):
        """The window for the hotkeys in *config*, F8 and F9 when it has none;
        what gets saved is decided when it closes (hotkeys_to_save)."""
        self.config = config
        self._evdev = _evdev()
        self.keys = {"hold": config.hotkey or "F8", "toggle": config.toggle_hotkey or "F9"}
        self.capturing = None       # "hold" or "toggle" while Change Key waits for a key
        # Presses never mark a key as working, only releases do: the phantom
        # events a released keyboard grab produces are presses only. Events
        # before the dialog is up are ignored.
        self.ready = False
        # What the test reads, opened just before the dialog shows: evdev
        # devices or the hotkey helper. The reader thread owns them once it runs.
        self.keyboards = []
        # Whether evdev reads them, so a keyboard plugged in mid-test is
        # opened too; not when the hotkey helper reads them.
        self._rescan = False
        # What the reader reads now, as GTK's thread last heard it: whether
        # the dictation service would hear a key follows it (service_hears).
        self._reading_now = []
        self._stop = threading.Event()
        # Hardware key codes held down, to ignore GTK's auto-repeat: it sends
        # repeated presses while a key is held, with no "is_repeat" flag.
        self._held_keycodes = set()
        self.rows = {}              # "hold"/"toggle": (label, Change Key button, status)
        self.status_words = {"hold": "Not tested", "toggle": "Not tested"}
        self.dialog = self._build()

    def _build(self):
        """The window: a row for each key, the status lines and Continue."""
        dialog = Gtk.Dialog(title="Test Your Hotkeys")
        dialog.set_default_size(550, 420)
        dialog.set_modal(True)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.set_keep_above(True)

        content = dialog.get_content_area()
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(25)
        content.set_margin_end(25)
        content.set_spacing(15)

        instructions = Gtk.Label()
        instructions.set_markup('<span size="large"><b>Configure Your Hotkeys</b></span>\n\n'
                                'Test both hotkeys to ensure they work on your system:')
        instructions.set_xalign(0)
        content.pack_start(instructions, False, False, 0)

        rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        rows_box.set_margin_top(10)
        for which, title in _TITLES.items():
            row, label, button, status = _hotkey_test_row(title, self.keys[which])
            button.connect("clicked", self._on_change, which)
            self.rows[which] = (label, button, status)
            rows_box.pack_start(row, False, False, 0)
        content.pack_start(rows_box, False, False, 0)

        ready_label = Gtk.Label()
        ready_label.set_markup('<span>Hold each hotkey, then release to confirm it works</span>')
        ready_label.set_xalign(0)
        ready_label.set_halign(Gtk.Align.CENTER)
        ready_label.set_margin_top(2)
        content.pack_start(ready_label, False, False, 0)

        # Shown when the service can't read the keyboard yet (set before show_all).
        self.keyboard_notice = Gtk.Label()
        self.keyboard_notice.set_line_wrap(True)
        self.keyboard_notice.set_xalign(0)
        self.keyboard_notice.set_no_show_all(True)
        content.pack_start(self.keyboard_notice, False, False, 0)

        self.capture_instruction = Gtk.Label()
        self.capture_instruction.set_markup(_CAPTURE_PROMPT)
        self.capture_instruction.set_margin_top(10)
        self.capture_instruction.set_no_show_all(True)
        content.pack_start(self.capture_instruction, False, False, 0)

        # Always connected: they return straight away until self.ready is set.
        dialog.connect("key-press-event", self._on_gtk_press)
        dialog.connect("key-release-event", self._on_gtk_release)

        info_label = Gtk.Label()
        info_label.set_markup(
            '<span><i>💡 Both keys work: hold one to talk, or tap the other to start '
            'and stop. You can change them anytime in Preferences.</i></span>')
        info_label.set_line_wrap(True)
        info_label.set_xalign(0)
        info_label.set_margin_top(15)
        content.pack_start(info_label, False, False, 0)

        conflict_label = Gtk.Label()
        conflict_label.set_markup(
            '<span><i>⚠️ If a key doesn\'t respond, it may be used by another '
            'application.</i></span>')
        conflict_label.set_line_wrap(True)
        conflict_label.set_xalign(0)
        conflict_label.set_opacity(0.85)
        content.pack_start(conflict_label, False, False, 0)

        continue_button = Gtk.Button(label="Continue")
        continue_button.get_style_context().add_class("suggested-action")
        dialog.add_action_widget(continue_button, Gtk.ResponseType.OK)  # keys saved after run()
        return dialog

    def _on_change(self, _button, which):
        """Change Key: the next key pressed becomes the *which* key."""
        self.capturing = which
        self.capture_instruction.show()
        self._announce(which, _CAPTURE_WORDS)
        for _label, button, _status in self.rows.values():
            button.set_sensitive(False)

    def _end_capture(self):
        """Change Key is no longer waiting for a key."""
        which, self.capturing = self.capturing, None
        self.capture_instruction.hide()
        self.capture_instruction.set_markup(_CAPTURE_PROMPT)
        for _label, button, _status in self.rows.values():
            button.set_sensitive(True)
        if which is not None:
            self._announce(which, self.status_words[which])

    def _announce(self, which, words):
        """Tell a screen reader what *which*'s row says now: the status and
        the Change Key prompt are plain labels, which it isn't told about,
        so they go in the row's button description, which it reads out."""
        self.rows[which][1].get_accessible().set_description(words)

    def _show_status(self, which, keyname):
        """Show how *keyname*, *which*'s key, tested."""
        markup, words = _key_seen(service_hears(self._reading_now, keyname))
        self.rows[which][2].set_markup(markup)
        self.status_words[which] = words
        self._announce(which, words)

    def _on_key(self, keyname, action="press"):
        """A key pressed or released, on GTK's thread, where Change Key's
        state is kept too. While Change Key waits, a press picks the key;
        otherwise a release marks it as working, since phantom events from a
        released keyboard grab are presses only."""
        if not self.ready:
            return False
        is_capture = self.capturing is not None
        if loggable_key(keyname) == keyname:
            # Only test keys: without the hotkey helper this hears keys
            # typed in any window, and a line per letter would give away a
            # password's length and timing to the log.
            logger.info(f"Hotkey test: {action} {keyname}, capture={is_capture}")
        if is_capture:
            if action == "press":
                self._capture(keyname)
        elif action == "release":
            self._mark_working(keyname)
        return False    # also run from GLib.idle_add: once

    def _capture(self, keyname):
        """Make *keyname* the key Change Key waits for, unless it can't be one."""
        if keyname == "Escape":
            self._end_capture()
            return
        other = self.keys["toggle" if self.capturing == "hold" else "hold"]
        problem = captured_key_problem(keyname, other)
        if problem:
            # Stay in capture mode so the user can press another key.
            self.capture_instruction.set_markup(
                f'<span background="#E57373" color="white"> '
                f'{GLib.markup_escape_text(problem)} (ESC to cancel) </span>')
            self._announce(self.capturing, f"{problem} Press another key, or Esc to cancel.")
            return
        self.keys[self.capturing] = keyname
        label = self.rows[self.capturing][0]
        label.set_markup(f'<b>{_TITLES[self.capturing]}:</b> {layout_label(keyname)}')
        self._show_status(self.capturing, keyname)
        self._end_capture()

    def _mark_working(self, keyname):
        """Show *keyname*'s row as tested, if it is one of the two keys."""
        for which, key in self.keys.items():
            if keyname == key:
                self._show_status(which, keyname)
                return

    def _on_gtk_press(self, _widget, event):
        """A dictation key for the test, or any key while Change Key waits
        for one. Other keys are the dialog's own, for using it without a
        mouse: Tab moves the focus, Space and Enter press the button."""
        if not self.ready or event.type != Gdk.EventType.KEY_PRESS:
            return True
        keyname = gtk_key_name(Gtk.accelerator_name(event.keyval, 0), event.hardware_keycode)
        is_capture = self.capturing is not None
        if not is_capture and keyname not in HOTKEY_CHOICES:
            return False
        if event.state & _NO_MODIFIER_MASK:
            return True
        if event.hardware_keycode in self._held_keycodes:
            return True
        self._held_keycodes.add(event.hardware_keycode)
        self._on_key(keyname, action="press")
        return True

    def _on_gtk_release(self, _widget, event):
        """A key let go: the physical action that marks a key as working."""
        self._held_keycodes.discard(event.hardware_keycode)   # the next press is a fresh one
        if not self.ready:
            return False
        keyname = gtk_key_name(Gtk.accelerator_name(event.keyval, 0), event.hardware_keycode)
        if event.state & _NO_MODIFIER_MASK & ~_OWN_MODIFIER.get(keyname, 0):
            return False
        self._on_key(keyname, action="release")
        return False

    def _open_keyboards(self):
        """Open the keyboards as the dictation service does, or the hotkey
        helper in their place; without either, say what makes the keys work.
        Opened here rather than in the reader thread, so the very first key
        already shows whether the service will hear it."""
        if self._evdev is not None:
            input_device, ecodes, list_devices = self._evdev
            for dev in open_new_keyboards(self.keyboards, list_devices, input_device):
                logger.info(f"Hotkey test: reading keyboard {dev.name!r}")
            # With the hotkey helper the user isn't in the "input" group, so
            # the keyboards can't be opened here: hear the keys through the
            # helper, as the dictation service does. Without this the test
            # said the keys wouldn't work until a restart.
            helper = None if self.keyboards else hotkey_test_helper(ecodes)
            if helper is not None:
                self.keyboards.append(helper)
            # Without the helper evdev reads them, from none yet if none is
            # plugged in: one plugged in mid-test still counts.
            self._rescan = helper is None
        if not self.keyboards:
            logger.warning("No keyboards found via evdev")
        self._show_reading(list(self.keyboards))

    def _show_reading(self, keyboards):
        """What the reader reads now, on GTK's thread: whether a key counts
        as working follows it, and without a keyboard the notice says what
        makes the keys work."""
        if self._stop.is_set():
            return False    # the window has closed
        self._reading_now = keyboards
        if keyboards:
            self.keyboard_notice.hide()
        else:
            self.keyboard_notice.set_markup(
                f'<span color="#FF9800">'
                f'{GLib.markup_escape_text(_hotkey_test_notice(False))}</span>')
            self.keyboard_notice.show()
        return False        # also run from GLib.idle_add: once

    def _open_plugged_in(self, reading):
        """Open, into *reading*, keyboards plugged in since the last look,
        and tell GTK's thread."""
        input_device, _ecodes, list_devices = self._evdev
        added = open_new_keyboards(reading, list_devices, input_device)
        for dev in added:
            logger.info(f"Hotkey test: reading keyboard {dev.name!r}, plugged in meanwhile")
        if added:
            GLib.idle_add(self._show_reading, list(reading))

    def _keyboard_gone(self, reading, held_modifiers):
        """A keyboard went mid-test: the keys it held will never come up, and
        GTK's thread hears what's left."""
        held_modifiers.clear()
        GLib.idle_add(self._show_reading, list(reading))

    def _reader_loop(self):
        """Read the keyboards through evdev until the test ends, passing each
        key to _on_key on GTK's thread; this hears keys the desktop grabs
        before GTK sees them. A keyboard plugged in meanwhile is read too,
        as the service would. Closes the keyboards when done."""
        ecodes = self._evdev[1]
        reading = list(self.keyboards)  # its own; GTK's thread hears of it (_show_reading)
        logger.info(f"Hotkey test: reading {len(reading)} keyboard(s)")
        # Without this, a prior F8 press (e.g. from using TuxTalk just before
        # onboarding) still in the kernel's queue would show as "Working!".
        flush_pending_events(reading)
        modifier_codes = {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT,
                          ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL,
                          ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA}
        held_modifiers = set()
        on_gone = functools.partial(self._keyboard_gone, reading, held_modifiers)
        last_error = None
        next_scan = time.monotonic() + RESCAN_SECONDS
        while not self._stop.is_set():
            try:
                if self._rescan and time.monotonic() >= next_scan:
                    next_scan = time.monotonic() + RESCAN_SECONDS
                    self._open_plugged_in(reading)
                ready = devices_with_events(reading, 0.1)
                # A keyboard unplugged with Ctrl held must not leave Ctrl held.
                for event in read_ready(reading, ready, on_gone=on_gone):
                    if event.type != ecodes.EV_KEY:
                        continue
                    # Releases count too: they are what marks a key as
                    # working, and GTK never sees a grabbed key.
                    found = evdev_key_action(event, held_modifiers,
                                             modifier_codes, evdev_key_name)
                    if found:
                        keyname, action = found
                        GLib.idle_add(self._on_key, keyname, action)
            except Exception as e:
                # Logged once: the loop tries again every 0.1 s.
                if not self._stop.is_set() and str(e) != last_error:
                    logger.info(f"Hotkey test: reading the keyboards failed: {e}")
                    last_error = str(e)
                time.sleep(0.1)
        close_devices(reading)      # read_ready closed the ones that went

    def _save_keys(self, confirmed):
        """Save the keys the test ended with. Whatever closed the dialog, the
        settings never end up without a hotkey: the service then watches no
        key at all."""
        from tuxtalk.config import save_config
        hold, toggle = hotkeys_to_save(self.config, self.keys["hold"],
                                       self.keys["toggle"], confirmed)
        if (hold, toggle) == (self.config.hotkey, self.config.toggle_hotkey):
            return
        self.config.hotkey, self.config.toggle_hotkey = hold, toggle
        try:
            save_config(self.config)
        except Exception as e:
            # save_config refuses to write when the settings file could
            # not be read; onboarding still moves on.
            logger.error(f"Could not save hotkey choice: {e}")

    def run(self):
        """Show the test until Continue or close, then save the keys. True
        after Continue."""
        from tuxtalk import gnome_keybindings
        # Clear GNOME's Alt+F8/Alt+F7 bindings before show_all(): GNOME may send a
        # synthetic F8 to whichever window has focus while it applies the change,
        # and that must not be this dialog. gnome_keybindings saves the originals
        # to a file first, so a crash can't lose them.
        gnome_saved = gnome_keybindings.disable_for_hotkey_test()
        if gnome_saved:
            time.sleep(0.3)  # let GNOME process the change before we take focus

        # Bound before the try so the finally can always clean it up, however
        # early an exception lands.
        reader = None
        response = Gtk.ResponseType.NONE
        try:
            # Stop the dictation service BEFORE showing the dialog so its evdev
            # keyboard grab is released cleanly. If it went AFTER the dialog got
            # focus, the kernel would release a stuck F8 key state and flood the
            # dialog with phantom key-repeat events via GTK, making F8 appear
            # "Working!" before being pressed.
            _stop_dictation_service()
            time.sleep(0.3)  # Allow kernel/X server to settle keyboard state
            self._open_keyboards()
            self.dialog.show_all()
            if self.keyboards or self._rescan:
                reader = threading.Thread(target=self._reader_loop, daemon=True)
                reader.start()
            self.ready = True
            response = self.dialog.run()
            self.dialog.destroy()
        finally:
            self.ready = False      # keys the reader still has queued change nothing
            # Always: gsettings persists across reboots, so a skipped restore
            # would leave Alt+F8/Alt+F7 dead.
            gnome_keybindings.restore(gnome_saved)
            self._stop.set()
            if reader is not None:
                reader.join(timeout=1.0)
            else:
                close_devices(self.keyboards)    # the reader thread closes them otherwise
            self._save_keys(response == Gtk.ResponseType.OK)
        logger.info("Hotkey test complete")
        return response == Gtk.ResponseType.OK


def show_hotkey_test_dialog():
    """Run the first-run hotkey test for the saved keys; True after Continue."""
    from tuxtalk.config import load_config
    return HotkeyTestDialog(load_config()).run()
