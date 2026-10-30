"""TuxTalk's menu as a window: everything the tray menu has.

Opening TuxTalk from the app list while it already runs shows this window.
On plain GNOME (Fedora, Silverblue, Debian...) there is no system tray, so
without TuxTalk's GNOME extension this window is the only way to reach
Preferences, Transcribe a File and the rest. On other desktops it's a
convenience: the same menu, wherever the tray icon is.

The tray (tray.py) owns it and runs every action; this only draws buttons.
Items and order follow the tray menu (and the GNOME extension's).
"""

import logging

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .logger import FailureNotes  # noqa: E402


logger = logging.getLogger(__name__)

WINDOW_TITLE = "TuxTalk"


def no_tray_note(extension_installed):
    """The note at the bottom when the desktop shows no tray icons."""
    if extension_installed:
        return ("TuxTalk's GNOME extension is installed. After you log out and "
                "back in, TuxTalk's menu is in the top bar.")
    return ("This desktop has no system tray, so this window is TuxTalk's menu. "
            "Open TuxTalk from your apps to bring it back.")


class MenuWindow:
    """One window; the tray keeps it and brings it to the front again."""

    def __init__(self, tray, no_tray=False, extension_installed=False, offer_extension=False):
        self.tray = tray
        self._updating = False      # set while the window moves the switch itself
        self._popup = None          # the open ▸ menu, kept so it isn't collected

        win = Gtk.Window(title=WINDOW_TITLE)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_resizable(False)
        win.set_default_size(360, -1)
        win.connect("destroy", self._on_destroy)
        self.window = win

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(14)
        box.set_margin_end(14)
        win.add(box)

        # Dictation on/off, with the model and device under it.
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.status_label = Gtk.Label(xalign=0)
        self.engine_label = Gtk.Label(xalign=0)
        self.engine_label.get_style_context().add_class("dim-label")
        text.pack_start(self.status_label, False, False, 0)
        text.pack_start(self.engine_label, False, False, 0)
        row.pack_start(text, True, True, 0)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.set_tooltip_text("Turn the dictation service on or off")
        self.switch.set_focus_on_click(False)
        self.switch.connect("notify::active", self._on_switch)
        row.pack_end(self.switch, False, False, 0)
        row.set_margin_bottom(8)
        box.pack_start(row, False, False, 0)

        t = tray
        for item in [
            ("Recent Dictations", self._popup_history),
            ("Fix a Word...", lambda _b: t.show_fix_word_dialog(0)),
            ("Transcribe a File...", t.open_transcribe_window),
            None,
            ("Performance", self._popup_performance),
            None,
            ("Preferences...", t.open_preferences),
            ("Voice Commands...", t.show_voice_commands),
            ("Your Stats...", t.open_preferences_stats),
            ("Help...", t.show_help),
            ("About TuxTalk...", t.show_about_dialog),
            ("Check for Updates...", t.check_for_updates_clicked),
            ("Restart Service", t.restart_service),
            None,
            ("Quit TuxTalk", t.quit_app),
        ]:
            if item is None:
                sep = Gtk.Separator()
                sep.set_margin_top(4)
                sep.set_margin_bottom(4)
                box.pack_start(sep, False, False, 0)
                continue
            label, action = item
            box.pack_start(self._button(label, action), False, False, 0)

        if no_tray:
            note = Gtk.Label(label=no_tray_note(extension_installed), xalign=0)
            note.set_line_wrap(True)
            note.set_max_width_chars(44)
            note.set_margin_top(10)
            note.get_style_context().add_class("dim-label")
            box.pack_start(note, False, False, 0)
            if offer_extension and not extension_installed:
                add = Gtk.Button(label="Add TuxTalk to the Top Bar...")
                add.set_tooltip_text("Opens Preferences → Advanced, where "
                                     "Install Extension puts TuxTalk's menu in GNOME's top bar")
                add.set_halign(Gtk.Align.START)
                add.set_margin_top(6)
                add.connect("clicked", lambda _b: t._launch_preferences(tab="advanced"))
                box.pack_start(add, False, False, 0)

        self._failures = FailureNotes()     # refresh runs every second: logged once
        self.refresh()
        # Keep the switch and the model line current, as the tray menu is.
        self._timer = GLib.timeout_add_seconds(1, self.refresh)
        win.show_all()
        # No button starts out focused (GTK would pick one and box it).
        win.set_focus(None)
        win.present()

    # ----- building blocks -----

    def _button(self, label, action):
        """A flat, left-aligned row like a menu item. Items that open a
        submenu get a ▸ and show it beside the button."""
        submenu = action in (self._popup_history, self._popup_performance)
        button = Gtk.Button(label=f"{label}  ▸" if submenu else label)
        button.set_relief(Gtk.ReliefStyle.NONE)
        # Like a menu item, a click doesn't take keyboard focus: with focus,
        # Breeze kept a box around the last clicked item after its popup
        # closed. Tab still reaches every button.
        button.set_focus_on_click(False)
        child = button.get_child()
        if isinstance(child, Gtk.Label):
            child.set_xalign(0)
        button.connect("clicked", self._run, action)
        return button

    def _run(self, button, action):
        try:
            action(button)
        except Exception as e:
            logger.error(f"Menu window action failed: {e}", exc_info=True)

    def _show_popup(self, button, menu):
        if self._popup is not None:
            self._popup.destroy()       # each opening builds a new one
        menu.show_all()
        menu.attach_to_widget(button, None)
        self._popup = menu
        menu.popup_at_widget(button, Gdk.Gravity.NORTH_EAST, Gdk.Gravity.NORTH_WEST, None)

    def _popup_history(self, button):
        menu = Gtk.Menu()
        self.tray.menu.fill_history(menu)
        self._show_popup(button, menu)

    def _popup_performance(self, button):
        """The tray menu's Performance presets, as a popup beside *button*."""
        menu = Gtk.Menu()
        self.tray.menu.fill_performance(menu)
        self._show_popup(button, menu)

    # ----- keeping it current -----

    def refresh(self):
        """Switch and status from the tray; runs every second while open."""
        try:
            running = bool(self.tray.is_service_running())
            self._updating = True
            self.switch.set_active(running)
            self._updating = False
            self.status_label.set_markup(
                "<b>Dictation is on</b>" if running else "<b>Dictation is off</b>")
            self.engine_label.set_text(self.tray.menu.engine_item.get_label())
        except Exception as e:      # the timer keeps going: the next second may work
            self._updating = False
            if self._failures.failed("refresh", e):
                logger.warning(f"Menu window refresh failed: {e}")
            return True
        if self._failures.worked("refresh"):
            logger.info("Menu window refresh works again")
        return True

    def _on_switch(self, switch, _pspec):
        if self._updating:
            return
        if switch.get_active():
            self.tray.start_service(None)
        else:
            self.tray.stop_service(None)

    def present(self):
        self.refresh()
        self.window.present()

    def _on_destroy(self, _win):
        GLib.source_remove(self._timer)
        if getattr(self.tray, "_menu_window", None) is self:
            self.tray._menu_window = None
