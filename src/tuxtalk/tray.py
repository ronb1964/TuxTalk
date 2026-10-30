"""The tray process: the tray icon, watching the dictation service, the
windows the menu opens, the D-Bus calls the GNOME extension and Preferences
make, and the process's start (main). The menu itself is tray_menu.py's,
starting and stopping the service tray_service.py's."""
import os
import signal
# CRITICAL: Disable HuggingFace XET downloads BEFORE any imports
# XET bypasses tqdm_class progress tracking, breaking our download progress UI
os.environ["HF_HUB_DISABLE_XET"] = "1"

import gi
gi.require_version("Gtk", "3.0")
# Ayatana is the maintained successor to libappindicator and is what the
# .deb, .rpm and Flatpak ship. The old "AppIndicator3" typelib needs
# libappindicator3.so.1, which Fedora does not provide at all. Imported under
# the old name so the call sites below read the same.
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, GLib
from gi.repository import AyatanaAppIndicator3 as AppIndicator3
import threading
import sys
import warnings
from .logger import setup_logger
from . import menu_cache, startup_notices, tray_instance, ui_style, update_offers, update_ui
from .tray_menu import TrayMenu, copy_dictation
from .tray_presets import Presets
from .tray_service import ServiceLifecycle

# The tray menu's icons need Gtk.ImageMenuItem: AppIndicator sends only its
# image to the desktop. It is deprecated but stays in GTK3, so its warnings
# are noise in every terminal launch and pasted bug report.
warnings.filterwarnings("ignore", message=r"Gtk\.ImageMenuItem\.", category=DeprecationWarning)
logger = setup_logger(__name__)

# D-Bus service import (optional - only needed for GNOME extension)
try:
    from .dbus_service import TuxTalkDBusService
    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False
    logger.warning("D-Bus service not available - GNOME extension integration disabled")


class DictationTray(ServiceLifecycle, Presets):
    """TuxTalk's tray: the icon and its menu, the dictation service it starts
    and polls every second, and what the menu, the GNOME extension, the
    command line and the menu window ask of it."""

    def __init__(self):
        """Make the icon and its menu, offer the D-Bus service and start the
        status poll; the dictation service is started by main()."""
        # Before any tray window is built, so About and the update results
        # match Preferences and onboarding.
        ui_style.prefer_dark_theme()

        self.indicator = AppIndicator3.Indicator.new(
            "talktype",
            "microphone-sensitivity-muted",  # start with muted icon
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )

        # Check if we should show the GTK tray
        # In production: hide GTK tray if GNOME extension is enabled
        # In dev mode (DEV_MODE=1): always show GTK tray for testing
        self._should_show_tray = self._check_tray_visibility()

        if self._should_show_tray:
            self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            logger.info("GTK tray enabled")
        else:
            self.indicator.set_status(AppIndicator3.IndicatorStatus.PASSIVE)
            logger.info("GTK tray hidden (GNOME extension is active)")

        self.indicator.set_title("TuxTalk")  # Set the app name

        # Track subprocess windows
        self.preferences_process = None
        self._menu_window = None    # menu_window.MenuWindow while it's open
        self._uninstalling = False  # the uninstall's question is up, or it's running
        self._quitting = False      # Quit began: waiting for the service to go
        # Counts the user's decisions about the service (Stop, Quit, Restart,
        # uninstall); a launch scheduled under an older one is dropped.
        self._service_gen = 0
        self._install_thread = None     # an update going in (Download & Install)
        self._restarting = False    # Restart Service waits for the old service
        self._restart_pending = False   # another restart asked for meanwhile

        # Cached PID of the dictation service (for fast /proc-based status checks)
        self._service_pid = None

        # Track onboarding state - service should not start during onboarding
        self.onboarding_in_progress = False

        # tuxtalk --toggle/--start/--stop (control.py): the PID of the service
        # that said it's ready, and a recording asked for before then.
        from .control import WaitingStart
        self._ready_pid = None
        self._waiting_start = WaitingStart()

        # Initialize D-Bus service (optional - for GNOME extension integration)
        self.dbus_service = None
        self._init_dbus_service()

        # The icon and the poll's change detection start from the same
        # answer: from two, a service that started in between would leave
        # the icon on "Stopped" until its next change.
        self._last_service_state = self.is_service_running()
        self.update_icon_status(self._last_service_state)
        self._applying_preset = False   # a Performance preset's setup is running
        self.menu = TrayMenu(self)
        self.update_menu_display(self._last_service_state)
        self.indicator.set_menu(self.menu.widget)
        if not self._last_service_state:
            # A service that crashed in an earlier session can leave KWin or
            # COSMIC holding the hotkeys back from every app.
            self._release_desktop_hotkeys()

        # Check service status every second to keep the icon and menu current
        GLib.timeout_add_seconds(1, self.update_status_and_menu)

        # After updating: the recommended setup, and the hotkey helper or
        # Parakeet Ultra this TuxTalk brings.
        update_offers.start(self.restart_service)

    def _check_tray_visibility(self) -> bool:
        """
        Determine if GTK tray should be visible.

        Returns:
            bool: True if GTK tray should be shown, False if it should be hidden
        """
        # Check for dev mode first - always show in dev mode
        if os.environ.get('DEV_MODE') == '1':
            logger.info("DEV_MODE=1 detected - showing GTK tray for testing")
            return True

        # Check if GNOME extension is enabled
        try:
            from . import extension_helper
            if extension_helper.is_extension_enabled():
                logger.info("GNOME extension is enabled - hiding GTK tray")
                return False
        except Exception as e:      # unknown: the tray icon shows, as without GNOME
            logger.info(f"Could not check extension status: {e}")

        # Default: show GTK tray (extension not enabled or not GNOME)
        return True

    def _init_dbus_service(self):
        """Initialize D-Bus service for GNOME extension integration (optional)."""
        if not DBUS_AVAILABLE:
            return

        try:
            from .tray_dbus_app import TrayAppInstance
            app_instance = TrayAppInstance(self)
            self.dbus_service = TuxTalkDBusService(app_instance)
            logger.info("D-Bus service initialized for GNOME extension integration")
        except Exception as e:
            logger.warning(f"Failed to initialize D-Bus service: {e}")
            self.dbus_service = None

        # On KDE nothing else fills the focused-window cache the way the GNOME
        # extension does, and paste needs it to recognise a terminal. Load the
        # KWin reporter now that the D-Bus service exists to receive it.
        self._start_kwin_focus_provider()

    def _start_kwin_focus_provider(self):
        """Load the KWin focus reporter on KDE. Never fatal."""
        self._kwin_focus_started = False
        try:
            from . import kwin_focus

            if not kwin_focus.should_provide():
                return
            self._kwin_focus_started = kwin_focus.start(kwin_focus.session_bus())
        except Exception as e:
            logger.warning(f"Could not start the KWin focus reporter: {e}")

    def _stop_kwin_focus_provider(self):
        """Unload the KWin focus reporter. Never fatal — runs on the quit path."""
        if not getattr(self, "_kwin_focus_started", False):
            return
        try:
            from . import kwin_focus

            kwin_focus.stop(kwin_focus.session_bus())
        except Exception as e:      # the quit goes on; the next start unloads it first
            logger.info(f"Could not stop the KWin focus reporter: {e}")

    def is_service_running(self):
        """Check if the dictation service is active via /proc (no subprocess spawn).

        Uses a cached PID for fast lookups. Falls back to scanning /proc if
        the PID is unknown (e.g., service was started before the tray).
        """
        # Fast path: check cached PID directly via /proc
        pid = getattr(self, '_service_pid', None)
        if pid:
            if self._check_pid_is_service(pid):
                return True
            # PID no longer valid — clear it
            self._service_pid = None

        # Slow path: scan /proc to find an existing service process
        # (handles case where service started before tray, e.g., at boot)
        from .service_launcher import find_service_pids
        pids = find_service_pids()
        if pids:
            self._service_pid = pids[0]
            return True
        return False

    @staticmethod
    def _check_pid_is_service(pid):
        """Check if a given PID is a TuxTalk dictation service process.

        The PID cached here is the target of toggle_recording's SIGUSR1, so a
        false match isn't cosmetic: it signals an unrelated process. The rule
        lives in service_launcher so this, Quit and the hotkey test all agree.
        """
        from .service_launcher import is_service_pid
        return is_service_pid(pid)

    def update_icon_status(self, is_running):
        """The icon for the service's state: running or stopped."""
        if is_running:
            self.indicator.set_icon_full("microphone-sensitivity-high", "TuxTalk: Active")
        else:
            self.indicator.set_icon_full("microphone-sensitivity-muted", "TuxTalk: Stopped")

    def _emit_service_state_after_check(self):
        """Send the service's real state to the GNOME extension.

        The extension's switch flips as soon as it is clicked. When nothing
        changed (a start that failed), update_status_and_menu stays silent,
        so this is what puts the switch back."""
        if not self.dbus_service:
            return False

        # Check actual service state
        actual_state = self.is_service_running()

        # Emit the actual state (not expected, in case it didn't start/stop)
        try:
            self.dbus_service.emit_service_state(actual_state)
            logger.debug(f"Emitted D-Bus service state: {actual_state}")
        except Exception as e:
            logger.error(f"Failed to emit D-Bus service state: {e}")

        return False  # Don't repeat

    # ----- Service management helpers -----

    def update_status_and_menu(self, repeat=True):
        """The status poll, every second: the icon and the menu follow the
        service, and a start or stop is announced over D-Bus. Only what
        changed is touched, since the desktop is sent every change.

        Args:
            repeat: If True (default), keeps the GLib timer running.
                    Pass False for a one-shot update.
        """
        old_state = self._last_service_state
        new_state = self.is_service_running()
        self.update_menu_display(is_running=new_state)
        if new_state != old_state:
            logger.info(f"Service state changed: {old_state} -> {new_state}")
            self.update_icon_status(is_running=new_state)
            if not new_state:
                self._ready_pid = None
                # Normal stops release the hotkeys themselves; a crash or a
                # SIGKILL doesn't, and F8 would stay swallowed with dictation off.
                self._release_desktop_hotkeys()
                if self.dbus_service:
                    self.dbus_service.clear_claimed_hotkeys()
                    self.dbus_service.clear_running_engine()
                    self.dbus_service.clear_recording_state()
            if self.dbus_service:
                try:
                    self.dbus_service.emit_service_state(new_state)
                    logger.debug(f"Emitted D-Bus service state: {new_state}")
                except Exception as e:
                    logger.error(f"Failed to emit D-Bus service state: {e}")

        self._last_service_state = new_state
        return repeat

    def update_menu_display(self, is_running=None):
        """Show in the menu whether dictation is on, what runs and which
        preset is in use; *is_running* when it's known already."""
        if is_running is None:
            is_running = self.is_service_running()
        running = self.dbus_service.running_engine if is_running and self.dbus_service else None
        self.menu.show_state(is_running, running)

    def refresh_menu(self):
        """Rebuild the tray menu (after onboarding has changed the setup)."""
        menu = self.menu.rebuild()
        self.update_menu_display()
        self.indicator.set_menu(menu)

    # ----- tuxtalk --toggle / --start / --stop (control.py) -----

    def _ready_service_pid(self):
        """The service's PID once it has said it's ready, else None. Checked
        against /proc: a report from a service since stopped counts for nothing.
        A tray started after the service missed the report; the service's
        ready file covers that."""
        from .service_launcher import read_ready_pid
        for pid in (getattr(self, "_ready_pid", None), read_ready_pid()):
            if pid and self._check_pid_is_service(pid):
                return pid
        return None

    def control(self, action):
        """Toggle, start or stop recording for the command line; a status
        (control.MESSAGES) for it to print. Before the service is ready the
        request waits for it, and the service is started if it isn't running."""
        from . import control
        if self.onboarding_in_progress:
            return "setup"
        if self._quitting:
            return "quitting"
        pid = self._ready_service_pid()
        if pid is None:
            status = self._waiting_start.apply(action)
            logger.info(f"Command line --{action} while the service isn't ready: {status}")
            if status == "waiting" and not self.is_service_running():
                self.start_service(None)
            return status
        self._waiting_start.take()      # nothing waits once the service is ready
        app = getattr(self.dbus_service, "app", None)
        send, status = control.act_now(action, bool(getattr(app, "is_recording", False)))
        logger.info(f"Command line --{action}: {status}")
        if send:
            self._signal_toggle(pid)
        return status

    def service_ready(self, pid):
        """The service loaded its model and acts on toggles from now on."""
        self._ready_pid = pid
        logger.info(f"Dictation service is ready (PID {pid})")
        if self._waiting_start.take():
            logger.info("Starting the recording the command line asked for")
            self._signal_toggle(pid)

    @staticmethod
    def _signal_toggle(pid):
        """One toggle for the service: SIGUSR1 (app._handle_sigusr1)."""
        try:
            os.kill(pid, signal.SIGUSR1)
        except (ProcessLookupError, PermissionError) as e:
            logger.warning(f"Could not reach the dictation service: {e}")

    def _release_desktop_hotkeys(self):
        """Give the hotkeys back to the desktop if no service is holding them.

        Rechecked here because a service started in the meantime has claimed
        them for itself, and releasing would undo that.
        """
        if self.is_service_running():
            return
        from .service_launcher import release_desktop_hotkeys
        release_desktop_hotkeys()

    def toggle_service(self, widget):
        """The Dictation item was clicked: start or stop the service. The
        status poll's set_active() only redraws it, so this is a click."""
        if widget.get_active():
            # Turn service ON
            self.start_service(None)
        else:
            # Turn service OFF
            self.stop_service(None)

    def _emit_model_changed(self, model_name: str):
        """Tell the GNOME extension that a model/device change has committed.

        This process owns the D-Bus name the extension listens on, so the signal
        has to originate here. Best-effort: the config is already saved by the
        time this runs, so a bus problem must not fail the change itself.
        """
        if not self.dbus_service:
            return
        try:
            self.dbus_service.emit_model_changed(model_name)
            logger.info(f"Announced model change to extension: {model_name}")
        except Exception as e:
            logger.error(f"Failed to emit model change: {e}")

    def set_model(self, model_name: str):
        """Change the speech model. Reachable over D-Bus as SetModel.

        Follows the same sequence as applying a performance preset: persist,
        tell the extension, restart.
        """
        from .config import VALID_MODELS, load_config, save_config

        if model_name not in VALID_MODELS:
            logger.error(
                f"SetModel: refusing unknown model {model_name!r}. "
                f"Valid: {', '.join(sorted(VALID_MODELS))}"
            )
            return

        try:
            cfg = load_config()
            if cfg.model == model_name:
                logger.info(f"SetModel: already using {model_name}")
                return
            cfg.model = model_name
            save_config(cfg)
            logger.info(f"SetModel: switched to {model_name}")

            self.update_menu_display()
            # The tray owns the D-Bus name the extension listens on, so the
            # change notification has to be emitted from here.
            self._emit_model_changed(model_name)
            self.restart_service(None)
        except Exception as e:
            logger.error(f"SetModel failed: {e}", exc_info=True)

    def _launch_preferences(self, tab=None):
        """Launch the preferences window subprocess.

        Args:
            tab: Optional tab name to open directly (e.g. "updates").
        """
        # Already open: bring it forward instead of opening a second one.
        # Doing nothing here looked like Preferences failed to open when the
        # window was hidden behind others.
        if self.preferences_process and self.preferences_process.poll() is None:
            from . import prefs_ipc
            pid = prefs_ipc.running_pid()
            if pid and prefs_ipc.bring_to_front(pid, tab):
                logger.info("Preferences already open; brought it to the front")
                return
            # Still starting, or closing: a new one brings the starting window
            # forward on *tab*, or opens once the closing one has gone
            # (prefs._acquire_prefs_singleton). Doing nothing lost the click.

        tab_desc = f" ({tab} tab)" if tab else ""

        from .service_launcher import launch_preferences
        self.preferences_process = launch_preferences(tab)
        logger.info(f"Opened preferences{tab_desc}")

    def open_preferences(self, _):
        """Launch preferences window."""
        try:
            self._launch_preferences()
        except Exception as e:
            logger.error(f"Failed to open preferences: {e}")

    def open_preferences_stats(self, _):
        """Open Preferences on the Stats tab (tray menu "Your Stats...")."""
        try:
            self._launch_preferences(tab="stats")
        except Exception as e:
            logger.error(f"Failed to open preferences stats tab: {e}")

    def open_preferences_updates(self, _):
        """Launch preferences window directly to Updates tab."""
        try:
            self._launch_preferences(tab="updates")
        except Exception as e:
            logger.error(f"Failed to open preferences updates tab: {e}")

    def open_transcribe_window(self, _=None):
        """Open Transcribe a File (transcribe_window.py), its own process."""
        try:
            from .service_launcher import launch_transcribe_window
            launch_transcribe_window()
        except Exception as e:
            logger.error(f"Failed to open Transcribe a File: {e}")
        return False

    def show_menu_window(self, _=None):
        """TuxTalk's menu as a window (menu_window.py): what opening TuxTalk
        again from the app list shows. On plain GNOME without the extension
        there's no tray, so this is the only menu there is."""
        try:
            if self._menu_window is not None:
                self._menu_window.present()
                return False
            from . import desktop_detect, extension_helper
            from .menu_window import MenuWindow
            extension_on = extension_helper.is_extension_enabled()
            no_tray = not extension_on and not desktop_detect.tray_host_available()
            self._menu_window = MenuWindow(
                self, no_tray=no_tray,
                extension_installed=extension_helper.is_extension_installed(),
                offer_extension=desktop_detect.is_gnome())
            logger.info("Opened the menu window" + (" (no system tray here)" if no_tray else ""))
        except Exception as e:
            logger.error(f"Failed to open the menu window: {e}", exc_info=True)
        return False  # one-shot when scheduled through GLib.idle_add

    def open_window(self, name):
        """Open a window by its command-line name (tuxtalk --open NAME, and
        the launcher's right-click shortcuts; see control.OPEN_METHODS)."""
        if self._quitting:
            return False
        actions = {
            "menu": self.show_menu_window,
            "preferences": lambda: self.open_preferences(None),
            "transcribe": self.open_transcribe_window,
            "fix-word": lambda: self.show_fix_word_dialog(0),
            "help": lambda: self.show_help(None),
        }
        action = actions.get(name)
        if action is None:
            logger.info(f"Ignoring unknown window {name!r}")
        else:
            logger.info(f"Opening {name} (asked from the command line or the launcher)")
            action()
        return False  # one-shot when scheduled through GLib

    def show_menu_window_if_no_tray(self):
        """After first-run setup: with no tray and no extension running, the
        user would see no menu at all, so show the menu window once."""
        if self._quitting:
            return False
        try:
            from . import desktop_detect, extension_helper
            if extension_helper.is_extension_enabled() or desktop_detect.tray_host_available():
                return False
            logger.info("No system tray and no extension running: showing the menu window")
            self.show_menu_window()
        except Exception as e:      # unknown: no menu window forced on a desktop with a tray
            logger.info(f"Could not check for a system tray: {e}")
        return False

    def show_help(self, _):
        """Show help dialog with TuxTalk features and instructions."""
        from .help_dialog import show_help_dialog
        show_help_dialog()

    def show_voice_commands(self, _):
        """Show voice commands quick reference popup."""
        from .voice_commands_dialog import show_voice_commands_dialog
        show_voice_commands_dialog()

    def show_about_dialog(self, _):
        """Open About TuxTalk (about_dialog.py)."""
        from .about_dialog import show_about_dialog
        show_about_dialog()

    def check_for_updates_clicked(self, _):
        """Check for Updates in the menu (update_ui.check_from_menu)."""
        update_ui.check_from_menu(self._show_update_result_dialog, lambda: self._quitting)

    def _show_update_result_dialog(self, result):
        """What an update check found, with Download & Install where this
        install can take the update itself (update_ui.show_update_result)."""
        update_ui.show_update_result(result, self._download_update,
                                     self._download_and_install_package)

    def _download_update(self, release):
        """Download & Install for a self-managed AppImage, then restart onto
        it. Nothing once Quit has begun, and nothing installed if Quit began
        during the download; an install under way finishes (_install_not_cut_off)."""
        if self._quitting:
            return
        update_ui.run_appimage_update(release, self.restart_app,
                                      quitting=lambda: self._quitting,
                                      run_install=self._install_not_cut_off)

    def _download_and_install_package(self, release, install_type):
        """Download the correct .rpm/.deb and install it as root via pkexec,
        then offer to restart. Delegates to the shared update_ui flow."""
        if self._quitting:
            return
        update_ui.run_package_update(release, install_type, self.restart_app,
                                     quitting=lambda: self._quitting)

    def auto_check_for_updates(self):
        """The once-a-day update check at start-up (update_ui)."""
        return update_ui.auto_check_for_updates(self._show_update_result_dialog,
                                                lambda: self._quitting)

    def show_fix_word_dialog(self, index=0):
        """Open Fix a Word on recent dictation *index* (0 = newest).

        Also reached from the GNOME extension over D-Bus (FixWordInDictation).
        The fix is saved as a custom voice command; see vocabulary.py.
        """
        from .fix_word_dialog import show_fix_word_dialog
        show_fix_word_dialog(index, copy_text=copy_dictation)
        return False  # one-shot when scheduled through GLib.idle_add


def main():
    """The tray process: what comes before the tray (the command line, the
    single-instance lock, an old install adopted), the tray, then first-run
    setup or the dictation service, and GTK's main loop."""
    from . import desktop_identity
    desktop_identity.apply()        # TuxTalk's icon on its windows, not KDE's generic one
    ui_style.prepare_windows()   # emoji that show, buttons clear of panels and docks
    command, open_window = _handle_command_line()

    # Before anything opens the shared D-Bus connection: one opened without
    # the GLib main loop can't export the tray's D-Bus service later.
    try:
        import dbus.mainloop.glib
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    except ImportError:
        pass
    tray_instance.acquire()

    # An old TalkType Flatpak can't see the lock above and would type every
    # dictation twice. Checked on a thread so a slow flatpak never delays the tray.
    threading.Thread(target=startup_notices.handle_old_talktype_flatpak, daemon=True).start()
    _adopt_old_install()

    # A copy of the settings to roll back to (Preferences → Advanced →
    # Restore Earlier Settings), taken before this version touches them.
    from .settings_backup import snapshot_if_changed
    snapshot_if_changed()

    from .ydotoold_leftovers import retire_ydotoold
    retire_ydotoold()       # what TuxTalk typed through until 0.18
    from .cuda_download import recover_cut_off_install
    recover_cut_off_install()   # before the service loads CUDA
    from .cuda_helper import remove_pytorch_link, remove_unused_cudnn
    remove_unused_cudnn()   # downloaded with CUDA until 0.19
    remove_pytorch_link()   # made with the CUDA preload until 0.19

    tray = DictationTray()
    tray.quit_on_sigterm()

    # "Update Complete!" and the rename notice, once the tray is up.
    GLib.timeout_add(500, startup_notices.show_after_start)

    # A first-run hotkey test that was killed mid-way leaves GNOME's Alt+F8 and
    # Alt+F7 cleared; put them back.
    try:
        from .gnome_keybindings import restore_left_over
        restore_left_over()
    except Exception as e:
        logger.info(f"Could not check GNOME keybindings: {e}")

    try:
        from . import cuda_helper
        if cuda_helper.is_first_run():
            _start_first_run(tray, command)
        else:
            _start_normally(tray, command, open_window)
    except Exception as e:
        # If any error, only auto-start if NOT during onboarding
        logger.error(f"Error in first run setup: {e}")
        if not tray.onboarding_in_progress:
            tray._auto_start_service(1000)

    Gtk.main()


def _handle_command_line():
    """tuxtalk --toggle/--start/--stop and --open NAME/--quit (control.py;
    the last two are the launcher's right-click shortcuts). With TuxTalk
    running these ask it and exit; otherwise this process starts TuxTalk and
    they wait for it. (The recording asked for, the window to open), either
    None."""
    from . import control
    command = control.parse_action(sys.argv[1:])
    if command:
        tray_instance.run_command_line(command)
    open_window = control.parse_open(sys.argv[1:])
    if open_window:
        tray_instance.open_from_command_line(open_window)
    return command, open_window


def _adopt_old_install():
    """What the TalkType -> TuxTalk rename did and still has to do: its log
    lines, the old launchers moved and repinned, then the menu entries this
    version needs. Outside a Flatpak, whose launchers its package owns."""
    # The folder move ran at package import, before any logger existed;
    # record what it did now (see rename_migration.py).
    from . import MIGRATION_LOG
    for line in MIGRATION_LOG:
        logger.info(f"Rename to TuxTalk: {line}")
    if os.environ.get("FLATPAK_ID"):
        return
    from . import config, rename_migration
    try:
        for line in rename_migration.adopt_old_install(dev=config.DEV_MODE):
            logger.info(f"Rename to TuxTalk: {line}")
        # A dock or favourites list that pinned the old menu entry would
        # show a blank icon; rename those once (a marker file records it).
        marker = os.path.join(os.path.dirname(config.CONFIG_PATH), ".launchers-renamed")
        for line in rename_migration.repin_launchers(marker):
            logger.info(f"Rename to TuxTalk: {line}")
    except Exception as e:      # never stop the tray from starting
        logger.warning(f"Rename to TuxTalk: could not move the launchers: {e}")
    # After the rename, which treats an existing tuxtalk.desktop as done.
    from .desktop_launcher import add_launcher_actions, create_hidden_desktop_entry
    create_hidden_desktop_entry()   # TuxTalk's icon on first-run windows
    add_launcher_actions()          # right-click shortcuts for older launchers


def _start_first_run(tray, command):
    """First run: the welcome screens once no dictation service runs, and
    the service only after them."""
    logger.info("First run detected - starting onboarding")
    if command:
        from . import control
        control.report("setup")     # the welcome screens come first
    # Set flag BEFORE scheduling to prevent any auto-start during onboarding
    tray.onboarding_in_progress = True
    tray.onboard_once_service_gone(lambda: _show_first_run_setup(tray))


def _show_first_run_setup(tray):
    """The welcome screens with all setup options, then what their answer
    asks for. An error in onboarding counts as a cancel, so the service
    still starts and the welcome comes back next launch. False, for its
    GLib timer."""
    try:
        from tuxtalk.first_run_setup import show_welcome_and_install
        result = show_welcome_and_install()
    except Exception:
        logger.error("Onboarding failed", exc_info=True)
        result = None
    _finish_first_run(tray, result)
    return False


def _finish_first_run(tray, result):
    """After the welcome screens (*result*, None when they failed): setup
    marked done if it was finished, the menu rebuilt and the service
    started; or quit, on a desktop TuxTalk's hotkey can't work on."""
    # Unsupported desktop (no GlobalShortcuts portal): the user can only
    # quit — the app cannot function here, so exit instead of starting a
    # dead service. First run is left unmarked so a later install on a
    # supported desktop still onboards.
    if result and result.get('quit'):
        logger.info("Onboarding: dictation hotkey unsupported on this desktop - quitting")
        tray.quit_app(None)
        return

    # Only mark first run complete if user completed the wizard
    if result and result.get('continue'):
        from . import cuda_helper
        try:
            cuda_helper.mark_first_run_complete()
        except Exception as e:
            logger.error(f"Failed to mark first run complete: {e}")
        # No tray and no extension running yet: without this
        # the user would see no menu at all.
        GLib.timeout_add(2500, tray.show_menu_window_if_no_tray)
    else:
        logger.info("User cancelled onboarding - will show again next launch")

    # Refresh menu after installations
    tray.refresh_menu()

    # Onboarding complete - NOW allow service to start
    tray.onboarding_in_progress = False
    logger.info("Onboarding complete - starting service")

    # Refresh KDE menu cache so TuxTalk shows in start menu immediately
    # This must run AFTER the .desktop file is created by the welcome dialog.
    # On a thread: the rebuild can take seconds and would freeze the menu.
    threading.Thread(target=menu_cache.refresh_menu_cache, daemon=True).start()

    tray._auto_start_service(500)


def _start_normally(tray, command, open_window):
    """Not the first run: the service starts at once, then the recording
    (*command*) or window (*open_window*) the command line asked for, and
    the daily update check after a delay."""
    logger.info("Not first run - auto-starting service")
    # A login entry from setups before 0.10.1 may still launch an old
    # AppImage from Downloads; point it at the installed copy.
    # Older entries also still carry a stale description.
    try:
        from .autostart import (repair_stale_appimage_autostart,
                                refresh_autostart_comment)
        repair_stale_appimage_autostart()
        refresh_autostart_comment()
    except Exception as e:
        logger.info(f"Could not check the login entry: {e}")
    if command:
        # The auto-start below starts the service; the recording
        # waits until its model is loaded.
        from . import control
        status = tray._waiting_start.apply(command)
        logger.info(f"Started with --{command}: {status}")
        control.report(status)
    tray._auto_start_service(1000)
    if open_window and open_window != "quit":
        # Started by --open (a launcher shortcut) with nothing running.
        GLib.timeout_add(1500, tray.open_window, open_window)
    # Check for updates after a delay (don't interfere with startup)
    GLib.timeout_add(5000, tray.auto_check_for_updates)


if __name__ == "__main__":
    main()
