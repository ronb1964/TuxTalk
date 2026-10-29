"""The tray's menu (a mixin of tray.DictationTray): building it in the
order the GNOME extension's menu has too, keeping its Dictation item, its
engine line and its Performance presets current, and the Recent Dictations
submenu, refilled whenever the dictation history changes."""
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .logger import setup_logger  # noqa: E402
from .tray_items import DictationItem, relabel, themed_icon  # noqa: E402

logger = setup_logger(__name__)

# CSS styling for the tray popup menu (solid dark background for readability)
_MENU_CSS = b"""
menu {
    background-color: #2d2d2d;
    border: 1px solid #1a1a1a;
    border-radius: 8px;
    padding: 4px 0;
}
menuitem {
    padding: 6px 12px;
    color: #ffffff;
}
menuitem:hover {
    background-color: #404040;
}
menuitem:disabled {
    color: #888888;
}
menuitem label {
    color: inherit;
}
separator {
    background-color: #404040;
    margin: 4px 8px;
}
"""


class TrayMenu:
    """DictationTray's menu. It uses the tray's toggle_service,
    is_service_running, dbus_service, indicator, show_fix_word_dialog, the
    Performance menu's methods (tray_presets.PresetMenu) and the actions
    each item calls."""

    def build_menu(self):
        """Build the complete GTK tray menu.

        Menu order is kept in sync with the GNOME extension (see extension.js).
        """
        menu = Gtk.Menu()

        # Apply dark-theme CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(_MENU_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.service_toggle = DictationItem(self.toggle_service)
        self.engine_item = Gtk.MenuItem(label="Loading the speech model...")
        self.engine_item.set_sensitive(False)

        # Submenus
        self.performance_menu_item = self._build_performance_submenu()
        history_menu_item = self._build_history_submenu()
        # Top level, right under Recent Dictations, so it is easy to find.
        fix_word_item = Gtk.MenuItem(label="Fix a Word...")
        fix_word_item.connect("activate", lambda _w: self.show_fix_word_dialog(0))
        transcribe_item = Gtk.MenuItem(label="Transcribe a File...")
        transcribe_item.connect("activate", self.open_transcribe_window)

        # For when something's wrong, so it sits with Help, not at the top.
        restart_item = Gtk.MenuItem(label="Restart Service")
        restart_item.connect("activate", self.restart_service)

        # Action items. Preferences alone carries a gear so it is easy to spot.
        prefs_item = self._gear_menu_item("Preferences...")
        voice_cmds_item = Gtk.MenuItem(label="Voice Commands...")
        stats_item = Gtk.MenuItem(label="Your Stats...")
        help_item = Gtk.MenuItem(label="Help...")
        about_item = Gtk.MenuItem(label="About TuxTalk...")
        updates_item = Gtk.MenuItem(label="Check for Updates...")
        quit_item = Gtk.MenuItem(label="Quit TuxTalk")
        prefs_item.connect("activate", self.open_preferences)
        voice_cmds_item.connect("activate", self.show_voice_commands)
        stats_item.connect("activate", self.open_preferences_stats)
        help_item.connect("activate", self.show_help)
        about_item.connect("activate", self.show_about_dialog)
        updates_item.connect("activate", self.check_for_updates_clicked)
        quit_item.connect("activate", self.quit_app)

        # Assemble in exact same order as GNOME extension
        for item in [
            self.service_toggle,
            history_menu_item,
            fix_word_item,
            transcribe_item,
            Gtk.SeparatorMenuItem(),
            self.engine_item,
            self.performance_menu_item,
            Gtk.SeparatorMenuItem(),
            prefs_item, voice_cmds_item, stats_item, help_item, about_item, updates_item,
            restart_item,
            Gtk.SeparatorMenuItem(),
            quit_item,
        ]:
            menu.append(item)
        menu.show_all()

        self._presets_shown = None      # new radio items: none is synced yet
        self.update_menu_display()
        return menu

    @staticmethod
    def _gear_menu_item(label):
        """A menu item with the desktop's settings gear in front of it.

        The tray menu is drawn by the desktop, not by GTK: AppIndicator sends
        only the label and an icon NAME, so bold or colour can't survive, but
        an icon does. "emblem-system-symbolic" is the round gear in both
        Breeze (KDE) and Adwaita (GNOME, Mint); KDE's "configure" is sliders.
        """
        name = themed_icon("emblem-system-symbolic", "preferences-system-symbolic")
        if name is None:
            return Gtk.MenuItem(label=label)
        item = Gtk.ImageMenuItem(label=label)
        item.set_image(Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU))
        item.set_always_show_image(True)
        return item

    def refresh_menu(self):
        """Rebuild the tray menu (after onboarding has changed the setup)."""
        self.indicator.set_menu(self.build_menu())

    def update_menu_display(self, is_running=None):
        """Show in the menu whether dictation is on, what runs, and which
        Performance preset is in use. Only what changed is touched: the
        status poll calls this every second, and the desktop is sent each
        widget change (dbusmenu). The presets are worked out again only when
        the settings changed, or the dot was left on another one.

        Args:
            is_running: Pre-computed service state (avoids redundant checks).
                        If None, checks service status directly.
        """
        if is_running is None:
            is_running = self.is_service_running()
        # Against the item itself: a click flips it before the service has
        # started, and one that fails to start must be shown off again.
        if self.service_toggle.get_active() != is_running:
            self.service_toggle.set_active(is_running)
        try:
            from .config import load_config
            from .recommend import engine_line
            cfg = load_config()
            running = None
            if is_running and getattr(self, "dbus_service", None):
                running = self.dbus_service.running_engine
            relabel(self.engine_item, engine_line(cfg.model, cfg.device, running))
            if (cfg, self._dotted_preset()) != self._presets_shown:
                self._sync_preset_radios()
                self._presets_shown = (cfg, self._dotted_preset())
        except Exception as e:
            logger.error(f"Failed to update the model and device line: {e}")
            relabel(self.engine_item, "Speech model: unknown")

    def _build_history_submenu(self):
        """Build the Recent Dictations submenu and keep it current.

        The tray menu is built once, so the submenu is refilled whenever the
        dictation service updates the history file, watched with a GIO file
        monitor (inotify; the file lives on tmpfs, see history.py).
        """
        from gi.repository import Gio
        from .history import _history_path

        self.history_menu = Gtk.Menu()
        item = Gtk.MenuItem(label="Recent Dictations")
        item.set_submenu(self.history_menu)
        self._refresh_history_menu()

        if not getattr(self, "_history_monitor", None):
            try:
                self._history_monitor = Gio.File.new_for_path(
                    _history_path()).monitor_file(Gio.FileMonitorFlags.NONE, None)
                self._history_monitor.connect(
                    "changed", lambda *_: GLib.idle_add(self._refresh_history_menu))
            except Exception as e:
                logger.warning(f"Could not watch dictation history: {e}")
        return item

    def _refresh_history_menu(self):
        """Refill the Recent Dictations submenu from history.py."""
        self._fill_history_menu(self.history_menu)
        return False  # one-shot when scheduled through GLib.idle_add

    def _fill_history_menu(self, menu):
        """Fill *menu* with the recent dictations: the tray's submenu, and the
        menu window's popup (menu_window.py)."""
        from .history import get_entries, preview

        for child in menu.get_children():
            menu.remove(child)

        entries = get_entries()
        if not entries:
            empty = Gtk.MenuItem(label="No dictations yet")
            empty.set_sensitive(False)
            menu.append(empty)
        else:
            hint = Gtk.MenuItem(label="Hover to read one, click it to copy, then press Ctrl+V")
            hint.set_sensitive(False)
            menu.append(hint)
            from .config import load_config
            from .history import paste_last_hint
            paste_hint = paste_last_hint(getattr(load_config(), "paste_last_hotkey", ""))
            if paste_hint:
                hint2 = Gtk.MenuItem(label=paste_hint)
                hint2.set_sensitive(False)
                menu.append(hint2)
            for index, text in enumerate(entries):
                # Each entry opens a submenu with the full text on hover. A
                # tooltip would be simpler, but KDE draws this menu itself from
                # a D-Bus description that has no tooltips, so it would never
                # show. Submenus work on every desktop.
                entry = Gtk.MenuItem(label=preview(text))
                entry.set_submenu(self._build_history_entry_submenu(text, index))
                menu.append(entry)
            menu.append(Gtk.SeparatorMenuItem())
            clear_item = Gtk.MenuItem(label="Clear History")
            clear_item.connect("activate", lambda _w: self._clear_history())
            menu.append(clear_item)

        menu.show_all()

    def _build_history_entry_submenu(self, text, index):
        """The hover view of one recent dictation: its full text, then Copy
        and Fix a Word (which opens on this dictation, *index* in history).

        The text lines are left clickable (copying too) rather than greyed
        out, because disabled items are drawn dimmed and are hard to read.
        """
        from .history import full_text_lines

        submenu = Gtk.Menu()
        for line in full_text_lines(text):
            line_item = Gtk.MenuItem(label=line)
            line_item.connect("activate", lambda _w, t=text: self._copy_history_entry(t))
            submenu.append(line_item)
        submenu.append(Gtk.SeparatorMenuItem())
        copy_item = Gtk.MenuItem(label="Copy")
        copy_item.connect("activate", lambda _w, t=text: self._copy_history_entry(t))
        submenu.append(copy_item)
        fix_item = Gtk.MenuItem(label="Fix a Word...")
        fix_item.connect("activate", lambda _w, i=index: self.show_fix_word_dialog(i))
        submenu.append(fix_item)
        return submenu

    def _copy_history_entry(self, text):
        """Put a recent dictation on the clipboard (see clipboard.py)."""
        from .clipboard import copy_text
        if copy_text(text):
            logger.info(f"Copied a recent dictation to the clipboard ({len(text)} chars)")

    def _clear_history(self):
        from .history import clear
        clear()
        self._refresh_history_menu()
