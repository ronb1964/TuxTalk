"""The tray's side of its D-Bus service: what the GNOME extension, the
dictation service, Preferences and the command line can ask the running
tray to do or tell it (dbus_service.py has the D-Bus methods)."""
from gi.repository import GLib

from . import tray_presets
from .logger import setup_logger

logger = setup_logger(__name__)


class TrayAppInstance:
    """What the tray's D-Bus service (dbus_service.TuxTalkDBusService) asks
    and tells the tray. Each action is handed to GTK's main loop."""
    def __init__(self, tray):
        self.tray = tray
        self.is_recording = False

    def _later(self, action, *args):
        """*action*(*args) on GTK's thread, unless Quit has begun by then: a
        call queued just before Quit mustn't open a window or start a
        service after it."""
        def run():
            if not self.tray._quitting:
                action(*args)
            return False
        GLib.idle_add(run)

    def get_presets(self):
        """(id, label, description) for the GNOME extension's Performance menu."""
        return [(p.id, p.label, p.description) for p in tray_presets.current_presets()]

    def current_preset(self):
        return tray_presets.preset_in_use()

    @property
    def service_running(self):
        """Get current service running state."""
        return self.tray.is_service_running()

    def start_service(self):
        """Start service via tray."""
        self._later(self.tray.start_service, None)

    def stop_service(self):
        """Stop service via tray."""
        self._later(self.tray.stop_service, None)

    def restart_service(self):
        """Restart service via tray."""
        self._later(self.tray.restart_service, None)

    def restart_if_on(self):
        """Restart the service onto new settings, unless Dictation is off."""
        self._later(self.tray.restart_if_on, None)

    def toggle_recording(self):
        """Toggle recording in the service process via SIGUSR1,
        once it's ready: one still starting (just restarted)
        has no handler yet, and the signal would end it."""
        if self.tray._quitting:
            return
        pid = self.tray._ready_service_pid()
        if pid:
            self.tray._signal_toggle(pid)
        else:
            logger.info("Toggle ignored: the dictation service isn't ready")

    @property
    def quitting(self):
        """Quit began: D-Bus actions are refused (dbus_service)."""
        return self.tray._quitting

    def control(self, action):
        """tuxtalk --toggle/--start/--stop; answered at once with a status."""
        return self.tray.control(action)

    def service_ready(self, pid):
        """The service loaded its model (NotifyServiceReady)."""
        self.tray.service_ready(pid)

    def start_recording(self):
        """Start recording if not already recording."""
        if not self.is_recording:
            self.toggle_recording()

    def stop_recording(self):
        """Stop recording if currently recording."""
        if self.is_recording:
            self.toggle_recording()

    def show_preferences(self):
        """Open preferences via tray."""
        self._later(self.tray.open_preferences, None)

    def show_update_result(self, result):
        """Show the persistent update-result dialog via the tray (called from
        the GNOME extension's D-Bus CheckForUpdates so the manual check gives a
        real 'click OK' window, not just the fleeting GNOME notification)."""
        self._later(self.tray._show_update_result_dialog, result)

    def show_help(self):
        """Show help via tray."""
        self._later(self.tray.show_help, None)

    def show_voice_commands(self):
        """Show voice commands quick reference via tray."""
        self._later(self.tray.show_voice_commands, None)

    def show_fix_word(self):
        """Open Fix a Word on the newest dictation (GNOME extension D-Bus)."""
        self._later(self.tray.show_fix_word_dialog, 0)

    def show_transcribe_file(self):
        """Open Transcribe a File (GNOME extension D-Bus)."""
        self._later(self.tray.open_transcribe_window, None)

    def show_menu(self):
        """The menu window: TuxTalk opened again (ShowMenu)."""
        self._later(self.tray.show_menu_window)

    def show_about(self):
        """Show about dialog via tray."""
        self._later(self.tray.show_about_dialog, None)

    def show_preferences_stats(self):
        """Open preferences to the Stats tab via tray."""
        self._later(self.tray.open_preferences, None, "stats")

    def show_preferences_updates(self):
        """Open preferences to Updates tab via tray."""
        self._later(self.tray.open_preferences, None, "updates")

    def set_performance_preset(self, preset_id: str):
        """Apply a performance preset via tray (called from GNOME extension D-Bus)."""
        self._later(self.tray.set_performance_preset, preset_id)

    def quit(self):
        """Quit via tray."""
        self._later(self.tray.quit_app, None)

    def uninstall(self):
        """Uninstall TuxTalk (Preferences, tuxtalk --uninstall)."""
        self._later(self.tray.uninstall)

    def restart_app(self):
        """Restart into a just-installed update (called by
        Preferences, which can't restart the tray itself)."""
        self._later(self.tray.restart_app, None)

    @property
    def config(self):
        """The settings as saved now (load_config falls back to the defaults
        itself when the file can't be read)."""
        from .config import load_config
        return load_config()
