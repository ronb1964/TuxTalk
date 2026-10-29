"""The tray's menu: TrayMenu builds it in the order the GNOME extension's
menu has too, owns its widgets, shows on them whether dictation is on, what
runs and which Performance preset is in use, and refills Recent Dictations
whenever the dictation history changes. A click goes to the tray's action."""
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import tray_presets  # noqa: E402
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
    """The tray's menu (*widget*) and the items in it that change. *tray* is
    the DictationTray whose actions the items call."""

    def __init__(self, tray):
        """Build the menu, its look and its watch on the dictation history."""
        self.tray = tray
        # While the menu moves the Performance dot itself, the radio items'
        # "activate" isn't a click.
        self._moving_dot = False
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(_MENU_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.widget = None
        self.service_toggle = None
        self.engine_item = None
        self.history_menu = None
        self.preset_radios = {}         # a preset's id -> its radio item
        self.preset_custom = None
        self._presets_shown = None      # (settings, dot) the presets were last synced for
        self.rebuild()
        self._history_monitor = self._watch_history()

    def rebuild(self):
        """Build the menu afresh (after onboarding has changed the setup) and
        return it. Its order is the GNOME extension's (see extension.js)."""
        tray = self.tray
        menu = Gtk.Menu()
        self.service_toggle = DictationItem(tray.toggle_service)
        self.engine_item = Gtk.MenuItem(label="Loading the speech model...")
        self.engine_item.set_sensitive(False)

        # Submenus
        performance_item = self._performance_submenu()
        history_item = self._history_submenu()
        # Top level, right under Recent Dictations, so it is easy to find.
        fix_word_item = Gtk.MenuItem(label="Fix a Word...")
        fix_word_item.connect("activate", lambda _w: tray.show_fix_word_dialog(0))
        transcribe_item = Gtk.MenuItem(label="Transcribe a File...")
        transcribe_item.connect("activate", tray.open_transcribe_window)

        # For when something's wrong, so it sits with Help, not at the top.
        restart_item = Gtk.MenuItem(label="Restart Service")
        restart_item.connect("activate", tray.restart_service)

        # Action items. Preferences alone carries a gear so it is easy to spot.
        prefs_item = _gear_menu_item("Preferences...")
        voice_cmds_item = Gtk.MenuItem(label="Voice Commands...")
        stats_item = Gtk.MenuItem(label="Your Stats...")
        help_item = Gtk.MenuItem(label="Help...")
        about_item = Gtk.MenuItem(label="About TuxTalk...")
        updates_item = Gtk.MenuItem(label="Check for Updates...")
        quit_item = Gtk.MenuItem(label="Quit TuxTalk")
        prefs_item.connect("activate", tray.open_preferences)
        voice_cmds_item.connect("activate", tray.show_voice_commands)
        stats_item.connect("activate", tray.open_preferences_stats)
        help_item.connect("activate", tray.show_help)
        about_item.connect("activate", tray.show_about_dialog)
        updates_item.connect("activate", tray.check_for_updates_clicked)
        quit_item.connect("activate", tray.quit_app)

        # Assemble in exact same order as GNOME extension
        for item in [
            self.service_toggle,
            history_item,
            fix_word_item,
            transcribe_item,
            Gtk.SeparatorMenuItem(),
            self.engine_item,
            performance_item,
            Gtk.SeparatorMenuItem(),
            prefs_item, voice_cmds_item, stats_item, help_item, about_item, updates_item,
            restart_item,
            Gtk.SeparatorMenuItem(),
            quit_item,
        ]:
            menu.append(item)
        menu.show_all()
        self._presets_shown = None      # new radio items: none is synced yet
        self.widget = menu
        return menu

    def show_state(self, is_running, running_engine=None):
        """Show whether dictation is on (*is_running*), what runs
        (*running_engine*, as the service reported it) and which preset is in
        use. Only what changed is touched: the tray's status poll calls this
        every second, and the desktop is sent each widget change (dbusmenu).
        The presets are worked out again only when the settings changed, or
        the dot was left on another one."""
        # Against the item itself: a click flips it before the service has
        # started, and one that fails to start must be shown off again.
        if self.service_toggle.get_active() != is_running:
            self.service_toggle.set_active(is_running)
        try:
            from .config import load_config
            from .recommend import engine_line
            cfg = load_config()
            relabel(self.engine_item, engine_line(cfg.model, cfg.device, running_engine))
            if (cfg, self._dotted_preset()) != self._presets_shown:
                self._sync_presets()
                self._presets_shown = (cfg, self._dotted_preset())
        except Exception as e:
            logger.error(f"Failed to update the model and device line: {e}")
            relabel(self.engine_item, "Speech model: unknown")

    # ----- Performance -----

    def _performance_submenu(self):
        """The Performance item: this computer's presets (Recommended,
        Lightest, Battery saver), then Custom."""
        submenu = Gtk.Menu()
        self.preset_radios = {}
        preset_group = None
        for preset in tray_presets.current_presets():
            label = f"{preset.label} ({preset.description})"
            radio = (Gtk.RadioMenuItem(label=label) if preset_group is None
                     else Gtk.RadioMenuItem(label=label, group=preset_group))
            preset_group = preset_group or radio
            radio.connect("activate", self._on_preset, preset.id)
            submenu.append(radio)
            self.preset_radios[preset.id] = radio

        # Custom has the dot when the settings match no preset; it opens Preferences.
        submenu.append(Gtk.SeparatorMenuItem())
        self.preset_custom = Gtk.RadioMenuItem(label="Custom (opens Preferences)",
                                               group=preset_group)
        self.preset_custom.connect("activate", self._on_custom)
        submenu.append(self.preset_custom)

        item = Gtk.MenuItem(label="Performance")
        item.set_submenu(submenu)
        return item

    def _on_preset(self, item, preset_id):
        """A preset was clicked: the tray applies it. GTK fires "activate" on
        the radio being unselected too, so only the selected one counts."""
        if not self._moving_dot and item.get_active():
            self.tray.set_performance_preset(preset_id)

    def _on_custom(self, item):
        """Performance > Custom: open Preferences, then put the dot back on the
        setup in use (clicking a radio item moves it)."""
        if self._moving_dot or not item.get_active():
            return
        self.tray.open_preferences(None)
        self.put_dot_back_soon()

    def _sync_presets(self):
        """Refresh the Performance labels and put the dot on the preset in use."""
        for preset in tray_presets.current_presets():
            if preset.id in self.preset_radios:
                relabel(self.preset_radios[preset.id], f"{preset.label} ({preset.description})")
        self.put_dot_back()

    def _dotted_preset(self):
        """Id of the preset the menu's dot is on, or 'custom'."""
        return next((pid for pid, radio in self.preset_radios.items() if radio.get_active()),
                    "custom")

    def put_dot_back(self):
        """Put the Performance dot on the preset that's actually in use. The
        flag that makes the radios ignore this is always cleared, or every
        later click would be ignored."""
        self._moving_dot = True
        try:
            radio = self.preset_radios.get(tray_presets.preset_in_use(), self.preset_custom)
            radio.set_active(True)
        finally:
            self._moving_dot = False

    def put_dot_back_soon(self):
        """Put the dot back on the preset in use, after a moment: KDE ticks
        the clicked item in its own copy of the menu, and a move there and
        back in the same main-loop pass reaches it as no change and leaves
        two dots."""
        def put_back():
            self.put_dot_back()
            return False    # once
        GLib.timeout_add(150, put_back)

    # ----- Recent Dictations -----

    def _history_submenu(self):
        """The Recent Dictations item, its submenu filled from history.py."""
        self.history_menu = Gtk.Menu()
        item = Gtk.MenuItem(label="Recent Dictations")
        item.set_submenu(self.history_menu)
        self.refresh_history()
        return item

    def _watch_history(self):
        """Refill Recent Dictations whenever the dictation service updates
        the history file, watched with a GIO file monitor (inotify; the file
        lives on tmpfs, see history.py). The monitor, or None."""
        from gi.repository import Gio
        from .history import _history_path
        try:
            monitor = Gio.File.new_for_path(_history_path()).monitor_file(
                Gio.FileMonitorFlags.NONE, None)
            monitor.connect("changed", lambda *_: GLib.idle_add(self.refresh_history))
            return monitor
        except Exception as e:
            logger.warning(f"Could not watch dictation history: {e}")
            return None

    def refresh_history(self):
        """Refill the Recent Dictations submenu from history.py."""
        self.fill_history(self.history_menu)
        return False  # one-shot when scheduled through GLib.idle_add

    def fill_history(self, menu):
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
                entry.set_submenu(self._history_entry_submenu(text, index))
                menu.append(entry)
            menu.append(Gtk.SeparatorMenuItem())
            clear_item = Gtk.MenuItem(label="Clear History")
            clear_item.connect("activate", lambda _w: self._clear_history())
            menu.append(clear_item)

        menu.show_all()

    def _history_entry_submenu(self, text, index):
        """The hover view of one recent dictation: its full text, then Copy
        and Fix a Word (which opens on this dictation, *index* in history).

        The text lines are left clickable (copying too) rather than greyed
        out, because disabled items are drawn dimmed and are hard to read.
        """
        from .history import full_text_lines

        submenu = Gtk.Menu()
        for line in full_text_lines(text):
            line_item = Gtk.MenuItem(label=line)
            line_item.connect("activate", lambda _w, t=text: copy_dictation(t))
            submenu.append(line_item)
        submenu.append(Gtk.SeparatorMenuItem())
        copy_item = Gtk.MenuItem(label="Copy")
        copy_item.connect("activate", lambda _w, t=text: copy_dictation(t))
        submenu.append(copy_item)
        fix_item = Gtk.MenuItem(label="Fix a Word...")
        fix_item.connect("activate", lambda _w, i=index: self.tray.show_fix_word_dialog(i))
        submenu.append(fix_item)
        return submenu

    def _clear_history(self):
        """Clear History: forget the recent dictations, and show none."""
        from .history import clear
        clear()
        self.refresh_history()


def copy_dictation(text):
    """Put a recent dictation on the clipboard (see clipboard.py)."""
    from .clipboard import copy_text
    if copy_text(text):
        logger.info(f"Copied a recent dictation to the clipboard ({len(text)} chars)")


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
