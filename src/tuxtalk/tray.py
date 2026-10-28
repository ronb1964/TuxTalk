"""The tray process: the tray icon and its menu, starting and watching the
dictation service, and the D-Bus calls the GNOME extension and Preferences make."""
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
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib
from .ui_style import message_dialog, show_message
from gi.repository import AyatanaAppIndicator3 as AppIndicator3
import subprocess
import time
import sys
import warnings
from .logger import setup_logger
from . import single_instance, update_offers

# The tray menu's icons need Gtk.ImageMenuItem: AppIndicator sends only its
# image to the desktop. It is deprecated but stays in GTK3, so its warnings
# are noise in every terminal launch and pasted bug report.
warnings.filterwarnings("ignore", message=r"Gtk\.ImageMenuItem\.", category=DeprecationWarning)
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

# D-Bus service import (optional - only needed for GNOME extension)
try:
    from .dbus_service import TuxTalkDBusService
    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False
    logger.warning("D-Bus service not available - GNOME extension integration disabled")

def _acquire_tray_singleton():
    """Take the tray's single-instance lock (single_instance.py), or give way
    to the tray that holds it (_give_way_or_exit)."""
    global _tray_lockfile_handle
    try:
        lockfile = single_instance.open_lock(TRAY_LOCK)
        if not single_instance.claim(lockfile):
            _give_way_or_exit(lockfile)
    except OSError as e:        # no runtime folder, or it's full: not "already running"
        logger.warning(f"Could not take the tray's single-instance lock: {e}")
        return
    _tray_lockfile_handle = lockfile    # closing it, even by garbage collection, lets go


def _give_way_or_exit(lockfile):
    """Another tray holds *lockfile*. A TalkType tray from before the rename
    is stopped, and this returns once its lock is taken; another TuxTalk
    shows its menu, or says it's running, and this one exits."""
    from .service_launcher import pre_rename_tray_pids
    old_trays = pre_rename_tray_pids()
    if old_trays:
        logger.info(f"Stopping the pre-0.15.0 tray (PID {old_trays}) so TuxTalk can start")
        for pid in old_trays:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass  # exited between the scan and the kill
    if not (old_trays and single_instance.claim(lockfile, _OLD_TRAY_EXIT_WAIT_S)):
        print("Another tray instance is already running. Exiting.", file=sys.stderr)
        # Opened again from the app list: the running TuxTalk's menu as a window.
        # Plain GNOME has no tray, so "look for the tray icon" was a dead end.
        if not old_trays:
            from . import control
            try:
                if control.open_in_running("menu", tray_starting=lambda: True):
                    sys.exit(0)
            except Exception as e:
                logger.info(f"Could not ask the running TuxTalk for its menu: {e}")
        _show_already_running(older_talktype=bool(pre_rename_tray_pids()))
        sys.exit(0)


# The single-instance lock's file, open while the tray runs.
_tray_lockfile_handle = None
TRAY_LOCK = "talktype-tray.lock"


def _tray_starting():
    """Whether another tray holds the lock: one still starting up that
    hasn't taken its D-Bus name yet, for control's wait."""
    return single_instance.held_elsewhere(TRAY_LOCK)

# How long an older TalkType tray gets to exit after SIGTERM. It has no handler
# for it, so it is normally gone at once.
_OLD_TRAY_EXIT_WAIT_S = 3.0


def _run_command_line(action):
    """tuxtalk --toggle/--start/--stop with TuxTalk running: exits. Returns
    only when nothing runs and this process should start TuxTalk itself."""
    from . import control
    try:
        status = control.ask_running(action, tray_starting=_tray_starting)
    except Exception as e:
        print(f"Couldn't reach TuxTalk: {e}", file=sys.stderr)
        sys.exit(1)
    if status is not None:
        sys.exit(control.report(status))
    if action == "stop":
        sys.exit(control.report("idle"))
    print("Starting TuxTalk.")


def _open_from_command_line(name):
    """tuxtalk --open NAME / --quit with TuxTalk running: asks it and exits.
    Returns only when nothing runs and this process should start TuxTalk
    (main() opens the window once it's up)."""
    from . import control
    try:
        status = control.open_in_running(name, tray_starting=_tray_starting)
    except Exception as e:
        print(f"Couldn't reach TuxTalk: {e}", file=sys.stderr)
        sys.exit(1)
    if status is True:
        sys.exit(0)
    if status is False and name != "uninstall":     # that one this process can do
        print("The running TuxTalk can't do that. It may be an older version "
              "or still starting; try again in a moment.", file=sys.stderr)
        sys.exit(1)
    if name == "quit":
        print("TuxTalk isn't running.")
        sys.exit(0)
    if name == "uninstall":
        # Nothing to start first: this process does it on its own.
        from . import uninstall
        sys.exit(0 if uninstall.run() else 1)
    print("Starting TuxTalk.")


def already_running_message(older_talktype):
    """The (heading, explanation) shown when another tray holds the lock."""
    if older_talktype:
        return ("An older TalkType is still running",
                "TuxTalk is the new name for TalkType, and only one of them can "
                "run at a time. Click the TalkType microphone icon in the system "
                "tray, choose Quit TalkType, then start TuxTalk again.")
    return ("TuxTalk is already running",
            "Look for its microphone icon in the system tray. Its menu has "
            "Preferences, Help and Quit TuxTalk.")


def _show_already_running(older_talktype):
    """Tell the user why no second tray appears; started from a menu, nothing
    else would show."""
    heading, body = already_running_message(older_talktype)
    logger.info(f"Not starting: {heading}")
    try:
        dialog = message_dialog(
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=heading,
        )
        dialog.format_secondary_text(body)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.set_keep_above(True)
        dialog.run()
        dialog.destroy()
    except Exception as e:      # no display: the stderr line is all there is
        logger.debug(f"Could not show the already-running notice: {e}")

OLD_FLATPAK_NOTICE_TITLE = "Stopped the old TalkType Flatpak"
OLD_FLATPAK_NOTICE_BODY = (
    "It was running next to TuxTalk, its new name, so everything you said "
    "would be typed twice. You can uninstall it from your software center, or "
    "with: flatpak uninstall io.github.ronb1964.TalkType")


def _handle_old_talktype_flatpak(stop=None, notify=None):
    """Stop an old TalkType Flatpak and say so; runs off the main thread, so
    the notice is handed to the main loop."""
    if stop is None:
        from .service_launcher import stop_old_talktype_flatpak as stop
    if notify is None:
        from .notify import notify as _notify

        def notify(title, body):
            def show():
                _notify(title, body)
                return False    # once
            GLib.idle_add(show)
    try:
        if stop():
            notify(OLD_FLATPAK_NOTICE_TITLE, OLD_FLATPAK_NOTICE_BODY)
    except Exception as e:      # never stop the tray from starting
        logger.warning(f"Could not check for the pre-rename Flatpak: {e}")


def about_icon_source(theme_has_icon):
    """Where the About dialog's icon comes from: ("file", path), ("icon",
    theme name) or None. *theme_has_icon* is the icon theme's has_icon."""
    from .autostart import get_icon_path
    from .config import APP_ID
    path = get_icon_path()
    if os.path.isfile(path):
        return ("file", path)
    appdir = os.environ.get("APPDIR")
    if appdir:
        bundled = os.path.join(appdir, f"{APP_ID}.svg")
        if os.path.isfile(bundled):
            return ("file", bundled)
    # deb/rpm install the icon under the app ID; the AppImage's first run
    # installs it as "tuxtalk".
    for name in (APP_ID, "tuxtalk"):
        if theme_has_icon(name):
            return ("icon", name)
    return None


def _about_icon():
    """The TuxTalk icon at 64 px for the About dialog, or None."""
    theme = Gtk.IconTheme.get_default()
    source = about_icon_source(theme.has_icon if theme else (lambda name: False))
    if source is None:
        return None
    kind, value = source
    if kind == "file":
        from gi.repository import GdkPixbuf
        return Gtk.Image.new_from_pixbuf(
            GdkPixbuf.Pixbuf.new_from_file_at_scale(value, 64, 64, True))
    image = Gtk.Image.new_from_icon_name(value, Gtk.IconSize.DIALOG)
    image.set_pixel_size(64)
    return image


def _themed_icon(*names):
    """The first of these icon names the current icon theme has, or None."""
    theme = Gtk.IconTheme.get_default()
    if theme is None:
        return None
    return next((name for name in names if theme.has_icon(name)), None)


class _IconToggleItem(Gtk.ImageMenuItem):
    """An on/off tray menu item showing a checkmark icon instead of a checkbox.

    Keeps Gtk.CheckMenuItem's get_active()/set_active() so callers don't
    change. set_active() only redraws, like the tray's old _updating_toggle
    guard; a click flips the state and calls on_toggle(item).
    """

    def __init__(self, label, on_toggle):
        super().__init__(label=label)
        self._active = False
        self._on_toggle = on_toggle
        self._check_icon = _themed_icon("object-select-symbolic", "emblem-ok-symbolic")
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
        if self._active and self._check_icon:
            self.set_image(Gtk.Image.new_from_icon_name(self._check_icon, Gtk.IconSize.MENU))
        else:
            # An empty image: the desktop leaves the icon column blank
            self.set_image(Gtk.Image())


def preset_notice(model, device, gpu_offered):
    """The notification after choosing a Performance preset.

    It says where the model really runs and, if that's the processor on a
    computer with a usable graphics chip, how to move it there.
    """
    from .recommend import engine_line, engine_status
    text = f"{engine_line(model, device)}."
    if engine_status(model, device)[1].startswith("CPU") and gpu_offered:
        text += " To use your graphics card, set the Device to Vulkan in Preferences."
    return text + "\nRestarting service..."


def recommend_notice_text(setup):
    """The one-time notice for an existing user whose setup isn't the
    recommended one. *setup* is recommend.Setup; its title says what and where."""
    return (f"{setup.title}. Choose Performance → Recommended for this computer in the "
            "TuxTalk menu to switch.")


def maybe_show_recommend_notice():
    """Once, on the first start after updating: tell existing users when the
    recommended setup differs from theirs. Never on first run
    (first run sets recommend_notice_shown) or the Flatpak. Runs as a GLib
    timeout: True (ask again) until the dictation service is up, then False."""
    try:
        from . import recommend
        from .config import load_config, save_config
        from .cuda_helper import is_first_run
        if os.environ.get("FLATPAK_ID") or is_first_run():
            return False
        from .service_launcher import find_service_pids
        if not find_service_pids():
            return True        # the service isn't up yet: ask again next tick
        cfg = load_config()
        if cfg.recommend_notice_shown:
            return False
        better = recommend.differs_from_recommendation(cfg, recommend.detect_hardware(cfg))
        if better is not None:
            from .notify import notify as _notify
            _notify("A better setup for this computer", recommend_notice_text(better))
            logger.info(f"Told the user about the recommended setup: {better.title}")
        cfg.recommend_notice_shown = True
        save_config(cfg)
    except Exception as e:
        logger.debug(f"Recommended-setup notice skipped: {e}")
    return False


class DictationTray:
    def __init__(self):
        # TuxTalk is a dark-themed app. This is a global Gtk setting, so setting
        # it once here, before any tray dialog is built, makes the tray's own
        # windows (About, update results) match Preferences and onboarding.
        try:
            _settings = Gtk.Settings.get_default()
            if _settings is not None:
                _settings.set_property("gtk-application-prefer-dark-theme", True)
        except Exception as e:
            logger.debug(f"Could not set dark theme preference: {e}")

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

        self.update_icon_status()
        self.indicator.set_menu(self.build_menu())

        # Track service state for change detection
        self._last_service_state = self.is_service_running()
        if not self._last_service_state:
            # A service that crashed in an earlier session can leave KWin or
            # COSMIC holding the hotkeys back from every app.
            self._release_desktop_hotkeys()

        # Check service status every second to keep the icon and menu current
        GLib.timeout_add_seconds(1, self.update_status_and_menu)

        # After updating: point existing users at the recommended setup, and
        # offer the hotkey helper or Parakeet Ultra this TuxTalk brings.
        GLib.timeout_add_seconds(15, maybe_show_recommend_notice)
        update_offers.start(self.restart_service)

        # Auto-start will be triggered after welcome dialog on first run
        # or immediately if not first run (handled in main())

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
        except Exception as e:
            logger.debug(f"Could not check extension status: {e}")

        # Default: show GTK tray (extension not enabled or not GNOME)
        return True

    def _init_dbus_service(self):
        """Initialize D-Bus service for GNOME extension integration (optional)."""
        if not DBUS_AVAILABLE:
            return

        try:
            # Create a minimal app instance for D-Bus
            class TrayAppInstance:
                """Minimal app instance for D-Bus integration with tray."""
                def __init__(self, tray):
                    self.tray = tray
                    self.is_recording = False

                def get_presets(self):
                    """(id, label, description) for the GNOME extension's Performance menu."""
                    return [(p.id, p.label, p.description) for p in self.tray._current_presets()]

                def current_preset(self):
                    return self.tray._get_current_preset()

                @property
                def service_running(self):
                    """Get current service running state."""
                    return self.tray.is_service_running()

                def start_service(self):
                    """Start service via tray."""
                    GLib.idle_add(self.tray.start_service, None)

                def stop_service(self):
                    """Stop service via tray."""
                    GLib.idle_add(self.tray.stop_service, None)

                def restart_service(self):
                    """Restart service via tray."""
                    GLib.idle_add(self.tray.restart_service, None)

                def toggle_recording(self):
                    """Toggle recording in the service process via SIGUSR1."""
                    pid = getattr(self.tray, '_service_pid', None)
                    if pid:
                        try:
                            os.kill(pid, signal.SIGUSR1)
                        except (ProcessLookupError, PermissionError):
                            pass

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
                    GLib.idle_add(self.tray.open_preferences, None)

                def show_update_result(self, result):
                    """Show the persistent update-result dialog via the tray (called from
                    the GNOME extension's D-Bus CheckForUpdates so the manual check gives a
                    real 'click OK' window, not just the fleeting GNOME notification)."""
                    GLib.idle_add(self.tray._show_update_result_dialog, result)

                def show_help(self):
                    """Show help via tray."""
                    GLib.idle_add(self.tray.show_help, None)

                def show_voice_commands(self):
                    """Show voice commands quick reference via tray."""
                    GLib.idle_add(self.tray.show_voice_commands, None)

                def show_fix_word(self):
                    """Open Fix a Word on the newest dictation (GNOME extension D-Bus)."""
                    GLib.idle_add(self.tray.show_fix_word_dialog, 0)

                def show_transcribe_file(self):
                    """Open Transcribe a File (GNOME extension D-Bus)."""
                    GLib.idle_add(self.tray.open_transcribe_window, None)

                def show_menu(self):
                    """The menu window: TuxTalk opened again (ShowMenu)."""
                    GLib.idle_add(self.tray.show_menu_window)

                def show_about(self):
                    """Show about dialog via tray."""
                    GLib.idle_add(self.tray.show_about_dialog, None)

                def show_preferences_stats(self):
                    """Open preferences to the Stats tab via tray."""
                    GLib.idle_add(self.tray.open_preferences_stats, None)

                def show_preferences_updates(self):
                    """Open preferences to Updates tab via tray."""
                    GLib.idle_add(self.tray.open_preferences_updates, None)

                def set_performance_preset(self, preset_id: str):
                    """Apply a performance preset via tray (called from GNOME extension D-Bus)."""
                    GLib.idle_add(self.tray.set_performance_preset, preset_id)

                def set_model(self, model_name: str):
                    """Change the model via tray (called from GNOME extension D-Bus SetModel)."""
                    GLib.idle_add(self.tray.set_model, model_name)

                def quit(self):
                    """Quit via tray."""
                    GLib.idle_add(self.tray.quit_app, None)

                def uninstall(self):
                    """Uninstall TuxTalk (Preferences, tuxtalk --uninstall)."""
                    GLib.idle_add(self.tray.uninstall)

                def restart_app(self):
                    """Restart into a just-installed update (called by
                    Preferences, which can't restart the tray itself)."""
                    GLib.idle_add(self.tray.restart_app, None)

                @property
                def config(self):
                    """Get current config."""
                    try:
                        from .config import load_config
                        return load_config()
                    except Exception:
                        # Return minimal config
                        class MinimalConfig:
                            model = 'large-v3'
                            device = 'cpu'
                        return MinimalConfig()

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
        except Exception as e:
            logger.debug(f"Could not stop the KWin focus reporter: {e}")

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

    def _auto_start_service(self, _waited=0):
        """Auto-start the dictation service if not already running.

        *_waited* counts the 100 ms checks spent waiting for a pre-rename
        service to exit; they run as GLib timeouts so the menu stays usable.
        """
        try:
            # NEVER start during onboarding - hotkey test needs to capture keys
            if self.onboarding_in_progress:
                logger.info("Onboarding in progress - refusing to auto-start service")
                return False

            # A TalkType (pre-0.15.0) service left running by a 0.14.x in-app
            # update is old code holding the shared lock: replace it, so the
            # new version's service starts and the tray can control it.
            from .service_launcher import pre_rename_service_pids, stop_dictation_service
            if pre_rename_service_pids() and _waited < 30:
                if _waited == 0:
                    logger.info("Stopping the pre-0.15.0 dictation service left running by the update")
                    stop_dictation_service()
                # SIGTERM returns at once; give it up to 3 s to exit and free
                # the lock, or the check below would still see it running.
                GLib.timeout_add(100, self._auto_start_service, _waited + 1)
                return False

            # Only start if not already running
            if not self.is_service_running():
                logger.info("Auto-starting dictation service...")
                self.start_service(None)
            else:
                logger.info("Dictation service already running, skipping auto-start")
        except Exception as e:
            logger.error(f"Failed to auto-start service: {e}", exc_info=True)
        return False  # Don't repeat this timer

    def update_icon_status(self, is_running=None):
        """Update icon based on service status.

        Args:
            is_running: Pre-computed service state (avoids redundant checks).
                        If None, checks service status directly.
        """
        if is_running is None:
            is_running = self.is_service_running()
        if is_running:
            self.indicator.set_icon_full("microphone-sensitivity-high", "TuxTalk: Active")
        else:
            self.indicator.set_icon_full("microphone-sensitivity-muted", "TuxTalk: Stopped")
        return True  # Continue the timer

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

    def _launch_service(self):
        """Launch the dictation service subprocess (see service_launcher)."""
        # Delegated so the tray and Preferences start the service identically,
        # including its GDK_BACKEND (see service_launcher for why it matters).
        from .service_launcher import launch_dictation_service

        proc = launch_dictation_service()
        if proc is not None:
            self._service_pid = proc.pid

    def _kill_service(self):
        """Kill all running dictation service processes."""
        from .service_launcher import stop_dictation_service
        stop_dictation_service()

    def start_service(self, _):
        """Start the dictation service directly."""
        # CRITICAL: Never start service during onboarding - hotkeys must not be active
        if self.onboarding_in_progress:
            logger.info("Onboarding in progress - refusing to start service")
            return

        try:
            self._launch_service()

            # Emit D-Bus signal if available
            if self.dbus_service:
                GLib.timeout_add_seconds(1, lambda: self._emit_service_state_after_check())
        except Exception as e:
            logger.error(f"Failed to start service: {e}", exc_info=True)
        GLib.timeout_add_seconds(1, lambda: self.update_status_and_menu(repeat=False))

    def stop_service(self, _):
        """Stop the dictation service directly."""
        try:
            self._kill_service()
            logger.info("Stopped dictation service")

            # Emit D-Bus signal if available
            if self.dbus_service:
                GLib.timeout_add_seconds(1, lambda: self._emit_service_state_after_check())
        except Exception as e:
            logger.error(f"Failed to stop service: {e}")
        GLib.timeout_add_seconds(1, lambda: self.update_status_and_menu(repeat=False))

    def restart_service(self, _):
        """Restart the dictation service directly.

        The kill/wait/relaunch runs on a background thread so the 1-second
        wait for processes to die never freezes the tray/menu (this fires on
        every model and performance-preset change).
        """
        # CRITICAL: Never restart service during onboarding
        if self.onboarding_in_progress:
            logger.info("Onboarding in progress - refusing to restart service")
            return

        # Re-entrancy guard: a second restart overlapping this one could kill
        # the freshly-launched service or leave two backends both grabbing the
        # hotkey (double-typing). A request that arrives meanwhile is queued,
        # not dropped: the new service may already have read the old settings.
        # Both this and _finish run on the GTK main thread, so no lock is needed.
        if getattr(self, '_restarting', False):
            logger.info("Restart already in progress - will restart again when it finishes")
            self._restart_pending = True
            return
        self._restarting = True
        self._restart_pending = False

        import threading

        def _do_restart():
            try:
                self._kill_service()
                time.sleep(1)  # Wait for processes to terminate
                GLib.idle_add(self._launch_service)
            except Exception as e:
                logger.error(f"Failed to restart service: {e}", exc_info=True)

            def _finish():
                self._restarting = False
                try:
                    self.update_status_and_menu(repeat=False)
                finally:
                    if getattr(self, '_restart_pending', False):
                        self._restart_pending = False
                        self.restart_service(None)
                return False
            GLib.timeout_add_seconds(2, _finish)

        threading.Thread(target=_do_restart, daemon=True).start()
    
    def update_status_and_menu(self, repeat=True):
        """Update icon and menu display based on service status.

        Args:
            repeat: If True (default), keeps the GLib timer running.
                    Pass False for a one-shot update.
        """
        # Store previous state to detect changes
        old_state = getattr(self, '_last_service_state', None)
        new_state = self.is_service_running()

        # Update UI — pass pre-computed state to avoid redundant checks
        self.update_icon_status(is_running=new_state)
        self.update_menu_display(is_running=new_state)

        # If state changed, emit D-Bus signal
        if old_state is not None and old_state != new_state:
            logger.info(f"Service state changed: {old_state} -> {new_state}")
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
        """Toggle dictation service on/off."""
        # Prevent recursive calls when we programmatically set the toggle
        if hasattr(self, '_updating_toggle') and self._updating_toggle:
            return

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

    def _current_presets(self):
        """The Performance presets for this computer and language (recommend.py)."""
        from . import recommend
        from .config import load_config
        cfg = load_config()
        return recommend.presets(recommend.effective_language(cfg), recommend.detect_hardware(cfg))

    def _get_current_preset(self) -> str:
        """Id of the preset the settings match, or 'custom'."""
        try:
            from . import config, recommend
            return recommend.match_preset(config.load_config(), self._current_presets())
        except Exception as e:
            logger.warning(f"Could not tell which preset is in use: {e}")
            return "custom"

    def set_performance_preset(self, preset_id: str):
        """Apply a Performance preset: Recommended, Lightest or Battery saver.

        Recommended on a graphics card runs the same setup as Preferences
        (confirm and download the Vulkan files, then the speed check) and
        uses the processor if the chip is slower. Cancelling the download
        changes nothing. Ids from an older GNOME extension ("balanced",
        "accurate"...) are ignored."""
        if getattr(self, "_updating_preset", False):
            return
        try:
            preset = next((p for p in self._current_presets() if p.id == preset_id), None)
        except Exception as e:
            logger.error(f"Could not work out the presets: {e}")
            preset = None
        if preset is None:
            logger.info(f"Ignoring unknown performance preset {preset_id!r}")
            self._revert_preset_radio()
            return
        if self._get_current_preset() == preset_id:
            # Already in effect (the GNOME menu sends clicks on the dotted
            # item too): no download check, speed check or restart.
            logger.info(f"Performance preset {preset_id!r} is already in effect")
            return
        try:
            from .config import load_config, save_config
            from .model_helper import is_model_cached_fast, download_model_with_progress
            model, device = preset.model, preset.device
            if device == "vulkan":
                from .vulkan_setup_dialogs import ensure_files, run_speed_check
                if not ensure_files(None, model, confirm=True):
                    self._revert_preset_radio()
                    return
                if not run_speed_check(None, model):
                    # On the processor, use what Recommended means there:
                    # Large-v3 needs a graphics card, so it becomes Small.
                    from . import recommend
                    device = "cpu"
                    model = recommend.recommend(recommend.effective_language(load_config()),
                                                recommend.Hardware(None), model=model).model
            if device == "cpu" and not is_model_cached_fast(model):
                downloaded = download_model_with_progress(model, device="cpu", show_confirmation=True)
                if downloaded is None:
                    self._revert_preset_radio()
                    return
                del downloaded
            cfg = load_config()
            cfg.model, cfg.device = model, device
            for key, value in preset.extras:
                setattr(cfg, key, value)
            if not preset.extras:
                # Leaving Battery saver: put back the settings it changed
                # (2-minute auto-stop) to their defaults, unless the user has
                # since chosen their own in Preferences.
                from .config import Settings
                battery = next((p for p in self._current_presets() if p.id == "battery"), None)
                if battery and all(getattr(cfg, k, None) == v for k, v in battery.extras):
                    # An instance, not the class: some defaults are worked out
                    # for this computer (the timeout is on only with a battery).
                    defaults = Settings()
                    for key, _ in battery.extras:
                        setattr(cfg, key, getattr(defaults, key))
            save_config(cfg)
            from .notify import notify as _notify
            try:
                from . import whisper_vulkan as _wv
                _gpu_offered = _wv.is_offered()
            except Exception:
                _gpu_offered = False
            logger.info(f"Applied performance preset: {preset.label} ({model} on {device})")
            _notify(f"Performance: {preset.label}", preset_notice(cfg.model, cfg.device, _gpu_offered))
            self.update_menu_display()
            self._emit_model_changed(cfg.model)
            self.restart_service(None)
        except Exception as e:
            logger.error(f"Failed to apply performance preset: {e}")

    def _sync_preset_radios(self):
        """Refresh the Performance labels and put the dot on the active preset.
        The busy flag (which makes set_performance_preset ignore the toggles
        this causes) is always cleared, or every later click would be ignored."""
        self._updating_preset = True
        try:
            for _p in self._current_presets():
                if _p.id in self.preset_radios:
                    self.preset_radios[_p.id].set_label(f"{_p.label} ({_p.description})")
            current_preset = self._get_current_preset()
            if current_preset in self.preset_radios:
                self.preset_radios[current_preset].set_active(True)
            elif hasattr(self, 'preset_custom'):
                self.preset_custom.set_active(True)
        finally:
            self._updating_preset = False

    def _revert_preset_radio(self):
        """Put the Performance radio back on the preset that's actually in use."""
        self._updating_preset = True
        try:
            _cur = self._get_current_preset()
            if _cur in self.preset_radios:
                self.preset_radios[_cur].set_active(True)
            elif hasattr(self, 'preset_custom'):
                self.preset_custom.set_active(True)
        except Exception as _re:
            logger.error(f"Failed to revert preset radio: {_re}")
        finally:
            self._updating_preset = False

    def update_menu_display(self, is_running=None):
        """Update menu display with current service status and model.

        Args:
            is_running: Pre-computed service state (avoids redundant checks).
                        If None, checks service status directly.
        """
        if hasattr(self, 'service_toggle'):
            # Update service toggle state
            if is_running is None:
                is_running = self.is_service_running()
            self._updating_toggle = True
            self.service_toggle.set_active(is_running)
            self._updating_toggle = False

        # What runs, in one line
        if hasattr(self, 'engine_item'):
            try:
                from .config import load_config
                from .recommend import engine_line
                cfg = load_config()
                running = None
                if is_running and getattr(self, "dbus_service", None):
                    running = self.dbus_service.running_engine
                self.engine_item.set_label(engine_line(cfg.model, cfg.device, running))

                # Update performance preset radio buttons
                if hasattr(self, 'preset_radios'):
                    self._sync_preset_radios()
            except Exception as e:
                logger.error(f"Failed to update the model and device line: {e}")
                self.engine_item.set_label("Speech model: unknown")
    
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
            else:
                # No pidfile yet: it is still starting and about to appear.
                logger.info("Preferences is still opening")
            return

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
        try:
            from . import desktop_detect, extension_helper
            if extension_helper.is_extension_enabled() or desktop_detect.tray_host_available():
                return False
            logger.info("No system tray and no extension running: showing the menu window")
            self.show_menu_window()
        except Exception as e:
            logger.debug(f"Could not check for a system tray: {e}")
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
        """Show About dialog with app info, version, and changelog."""
        import threading
        from . import __version__
        from . import update_checker

        # Create custom dialog for more flexibility
        dialog = Gtk.Dialog(
            title="About TuxTalk",
            flags=Gtk.DialogFlags.MODAL
        )
        dialog.set_default_size(500, 450)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.set_keep_above(True)

        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(20)
        content.set_margin_bottom(10)

        # App icon
        try:
            icon = _about_icon()
            if icon:
                content.pack_start(icon, False, False, 0)
        except Exception as e:
            logger.debug(f"Could not load icon for About dialog: {e}")

        # App name and version
        title_label = Gtk.Label()
        title_label.set_markup('<span size="x-large"><b>TuxTalk</b></span>')
        content.pack_start(title_label, False, False, 0)

        version_label = Gtk.Label(label=f"Version {__version__}")
        content.pack_start(version_label, False, False, 0)

        # Renamed in 0.15.0 (TALKTYPE is another company's trademark); this
        # line lets people who knew the old name know they have the right app.
        formerly_label = Gtk.Label()
        formerly_label.set_markup('<span size="small"><i>Formerly TalkType</i></span>')
        formerly_label.set_opacity(0.75)
        content.pack_start(formerly_label, False, False, 0)

        desc_label = Gtk.Label(label="Voice dictation for Linux: hold a key, talk, and your words are typed for you.")
        desc_label.set_line_wrap(True)
        content.pack_start(desc_label, False, False, 5)

        # Copyright and author
        copyright_label = Gtk.Label()
        copyright_label.set_markup('<span size="small">© 2024-2026 Ron Brand • MIT License</span>')
        content.pack_start(copyright_label, False, False, 0)

        # CC-BY-4.0 requires crediting NVIDIA for the Parakeet model. The AI
        # cleanup engine and model are downloaded from their own projects, not
        # shipped, but credit them here too.
        from .parakeet_engine import PARAKEET_ATTRIBUTION
        credit_label = Gtk.Label()
        credit_label.set_markup(
            f'<span size="small">{PARAKEET_ATTRIBUTION}\n'
            'AI cleanup uses llama.cpp (MIT) and Qwen2.5 by Alibaba Cloud (Apache 2.0).</span>')
        credit_label.set_justify(Gtk.Justification.CENTER)
        credit_label.set_line_wrap(True)
        credit_label.set_opacity(0.7)
        content.pack_start(credit_label, False, False, 0)

        # What's New section
        whats_new_label = Gtk.Label()
        whats_new_label.set_markup('<b>What\'s New in This Version</b>')
        whats_new_label.set_xalign(0)
        whats_new_label.set_margin_top(15)
        content.pack_start(whats_new_label, False, False, 0)

        # Scrolled text view for release notes
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(150)

        self._about_notes_text = Gtk.TextView()
        self._about_notes_text.set_editable(False)
        self._about_notes_text.set_wrap_mode(Gtk.WrapMode.WORD)
        self._about_notes_text.set_left_margin(10)
        self._about_notes_text.set_right_margin(10)
        self._about_notes_text.set_top_margin(10)
        self._about_notes_text.get_buffer().set_text("Loading release notes...")
        scroll.add(self._about_notes_text)
        content.pack_start(scroll, True, True, 0)

        # Links box
        links_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        links_box.set_halign(Gtk.Align.CENTER)
        links_box.set_margin_top(10)

        # The website, then the code on GitHub
        website_btn = Gtk.LinkButton.new_with_label("https://tuxtalk.app/", "Website")
        links_box.pack_start(website_btn, False, False, 0)

        github_btn = Gtk.LinkButton.new_with_label(
            "https://github.com/ronb1964/TuxTalk",
            "GitHub Repository"
        )
        links_box.pack_start(github_btn, False, False, 0)

        # Full changelog link
        changelog_btn = Gtk.LinkButton.new_with_label(
            update_checker.get_releases_url(),
            "View Full Changelog"
        )
        links_box.pack_start(changelog_btn, False, False, 0)

        content.pack_start(links_box, False, False, 0)

        # Close button
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)

        # Shown with a response callback and present() rather than run(): a
        # nested run() loop started from a tray menu callback can fail to map
        # the window on Wayland.
        dialog.connect("response", lambda d, _r: d.destroy())
        dialog.show_all()
        dialog.present()

        # Fetch release notes in background
        def fetch_notes():
            release = update_checker.fetch_release_by_tag(__version__)
            if release and release.get("body"):
                notes = release["body"]
            else:
                notes = "Release notes not available.\n\nVisit GitHub for the full changelog."
            GLib.idle_add(lambda: self._about_notes_text.get_buffer().set_text(notes))

        thread = threading.Thread(target=fetch_notes, daemon=True)
        thread.start()

    def check_for_updates_clicked(self, _):
        """Check for updates and show results dialog."""
        import threading
        from . import update_checker

        # Create progress dialog
        progress_dialog = message_dialog(
            closable=False,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.NONE,
            text="Checking for Updates..."
        )
        progress_dialog.format_secondary_text("Connecting to GitHub...")
        progress_dialog.set_position(Gtk.WindowPosition.CENTER)
        progress_dialog.set_keep_above(True)
        progress_dialog.show_all()

        result_holder = [None]

        def do_check():
            """Background thread to check for updates."""
            try:
                result_holder[0] = update_checker.check_for_updates()
            except Exception as e:
                logger.error(f"Update check raised: {e}", exc_info=True)
                result_holder[0] = {"success": False, "error": str(e)}
            r = result_holder[0] or {}
            logger.debug(f"Update check done: success={r.get('success')}, "
                         f"update_available={r.get('update_available')}, error={r.get('error')}")
            GLib.idle_add(show_result)

        def show_result():
            """Show the result in the main thread."""
            progress_dialog.destroy()

            result = result_holder[0]
            if not result or not result.get("success"):
                # Error checking for updates
                error_dialog = message_dialog(
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="Update Check Failed"
                )
                error_msg = result.get("error", "Unknown error") if result else "Unknown error"
                error_dialog.format_secondary_text(error_msg)
                error_dialog.set_position(Gtk.WindowPosition.CENTER)
                error_dialog.set_keep_above(True)
                error_dialog.connect("response", lambda d, _r: d.destroy())
                error_dialog.show_all()
                error_dialog.present()
                return

            # Show result dialog
            self._show_update_result_dialog(result)

        # Start background check
        thread = threading.Thread(target=do_check, daemon=True)
        thread.start()

    def _show_update_result_dialog(self, result):
        """Show the result of an update check for the app itself.

        GNOME extension updates are installed from Preferences → Updates &
        Backup instead (see _show_update_notification).
        """
        from . import update_checker

        has_update = result.get("update_available", False)
        current = result.get("current_version", "unknown")
        latest = result.get("latest_version", "unknown")
        release = result.get("release", {})

        # How TuxTalk was installed decides HOW to update. Only a self-managed
        # AppImage can be swapped in place; .deb/.rpm/AUR/Flatpak users must use
        # their package manager, so we show instructions instead of downloading
        # and running an AppImage (which fails, e.g. missing libfuse.so.2).
        install_type = update_checker.get_install_type()
        guidance = update_checker.get_update_guidance(
            install_type, latest, release.get("html_url", ""))

        if not has_update:
            # No AppImage update available
            dialog = message_dialog(
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="You're Up to Date!"
            )
            message = f"<b>TuxTalk {current}</b> is the latest version."
            dialog.format_secondary_markup(message)
            dialog.set_position(Gtk.WindowPosition.CENTER)
            dialog.set_keep_above(True)
            # Non-blocking (destroy on response) rather than run(), as in About.
            dialog.connect("response", lambda d, _r: d.destroy())
            dialog.show_all()
            dialog.present()
            return

        # Updates available - show detailed dialog.
        # NON-modal and NOT keep-above on purpose: the auto-check pops this on
        # launch, and a modal, always-on-top dialog grabs input and floats over
        # the About/Help/Voice windows so they open hidden behind it. The
        # Preferences window works precisely because it is neither.
        dialog = Gtk.Dialog(title="Update Available")
        dialog.set_default_size(450, 350)
        dialog.set_position(Gtk.WindowPosition.CENTER)

        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_start(15)
        content.set_margin_end(15)
        content.set_margin_top(15)
        content.set_margin_bottom(10)

        # Header
        header = Gtk.Label()
        header.set_markup("<big><b>Update Available!</b></big>")
        header.set_halign(Gtk.Align.START)
        content.pack_start(header, False, False, 0)

        # Version info
        version_label = Gtk.Label()
        version_label.set_markup(
            f"<b>TuxTalk:</b> {current} → <b>{latest}</b>"
        )
        version_label.set_halign(Gtk.Align.START)
        content.pack_start(version_label, False, False, 5)

        # For package/AUR/Flatpak installs, explain how to update and show the
        # exact command (selectable so the user can copy it). No auto-download.
        if not guidance["can_auto_update"]:
            how_label = Gtk.Label()
            how_label.set_line_wrap(True)
            how_label.set_halign(Gtk.Align.START)
            how_label.set_markup(guidance["message"])
            content.pack_start(how_label, False, False, 5)
            if guidance.get("command"):
                cmd_entry = Gtk.Entry()
                cmd_entry.set_text(guidance["command"])
                cmd_entry.set_editable(False)
                cmd_entry.get_style_context().add_class("monospace")
                content.pack_start(cmd_entry, False, False, 0)

        # Release notes in scrolled window
        if release.get("body"):
            notes_label = Gtk.Label(label="Release Notes:")
            notes_label.set_halign(Gtk.Align.START)
            notes_label.set_margin_top(10)
            content.pack_start(notes_label, False, False, 0)

            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroll.set_min_content_height(150)

            notes_text = Gtk.TextView()
            notes_text.set_editable(False)
            notes_text.set_wrap_mode(Gtk.WrapMode.WORD)
            notes_text.set_left_margin(10)
            notes_text.set_right_margin(10)
            notes_text.set_top_margin(10)
            notes_text.get_buffer().set_text(release.get("body", ""))
            scroll.add(notes_text)

            content.pack_start(scroll, True, True, 0)

        # Buttons
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)

        if release.get("html_url"):
            view_btn = dialog.add_button("View on GitHub", Gtk.ResponseType.ACCEPT)

        # Route each install type to the right update action:
        #  appimage_swap  -> download the AppImage and swap it in place
        #  pkexec_package -> download the .rpm/.deb and install it as root
        #  manual         -> just point to the GitHub release page
        method = guidance["update_method"]
        if has_update and method == "appimage_swap" and release.get("appimage_url"):
            btn = dialog.add_button("Download & Install", Gtk.ResponseType.YES)
            btn.get_style_context().add_class("suggested-action")
        elif has_update and method == "pkexec_package":
            btn = dialog.add_button("Download & Install", Gtk.ResponseType.APPLY)
            btn.get_style_context().add_class("suggested-action")
        elif has_update and release.get("html_url"):
            view_btn.get_style_context().add_class("suggested-action")

        # Non-blocking rather than run(), as in About: the response callback
        # handles the buttons.
        def _on_update_response(dlg, response):
            if response == Gtk.ResponseType.ACCEPT:
                update_checker.open_release_page(release.get("html_url", ""))
                dlg.destroy()
            elif response == Gtk.ResponseType.YES:
                dlg.destroy()
                self._download_update(release)
            elif response == Gtk.ResponseType.APPLY:
                dlg.destroy()
                self._download_and_install_package(release, install_type)
            else:
                dlg.destroy()
        dialog.connect("response", _on_update_response)
        dialog.show_all()
        dialog.present()

    def _download_update(self, release):
        """Download the update with progress dialog."""
        import threading
        from . import update_checker

        url = release.get("appimage_url")
        filename = release.get("appimage_name", "TuxTalk-update.AppImage")

        if not url:
            error_dialog = message_dialog(
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Download Error"
            )
            error_dialog.format_secondary_text("Could not find download URL.")
            error_dialog.set_position(Gtk.WindowPosition.CENTER)
            error_dialog.set_keep_above(True)
            error_dialog.run()
            error_dialog.destroy()
            return

        # Create progress dialog
        progress_dialog = Gtk.Dialog(
            title="Downloading Update",
            flags=Gtk.DialogFlags.MODAL
        )
        progress_dialog.set_default_size(400, 120)
        progress_dialog.set_position(Gtk.WindowPosition.CENTER)
        progress_dialog.set_keep_above(True)

        content = progress_dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(20)
        content.set_margin_bottom(10)

        status_label = Gtk.Label(label="Starting download...")
        status_label.set_halign(Gtk.Align.START)
        content.pack_start(status_label, False, False, 0)

        progress_bar = Gtk.ProgressBar()
        progress_bar.set_show_text(True)
        content.pack_start(progress_bar, False, False, 10)

        progress_dialog.show_all()

        result_holder = [None]

        def progress_callback(message, percent):
            """Update progress from background thread."""
            GLib.idle_add(lambda: status_label.set_text(message))
            GLib.idle_add(lambda: progress_bar.set_fraction(percent / 100.0))
            GLib.idle_add(lambda: progress_bar.set_text(f"{percent}%"))

        def do_download():
            """Background thread to download update."""
            result_holder[0] = update_checker.download_update(
                url, filename, progress_callback,
                checksums_url=release.get("checksums_url"))
            GLib.idle_add(download_complete)

        def download_complete():
            """Install what was downloaded, behind a status window, off the
            GTK thread: the copy of the AppImage would freeze the window and
            the tray menu for its length."""
            progress_dialog.destroy()
            downloaded_path = result_holder[0]
            if not downloaded_path:
                show_message(None, Gtk.MessageType.ERROR, "Download Failed",
                             "The download could not be completed.\n"
                             "Please try again or download manually from GitHub.",
                             keep_above=True)
                return False
            status_dialog = message_dialog(
                closable=False,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.NONE,
                text="Installing Update..."
            )
            status_dialog.format_secondary_text(
                "TuxTalk will restart automatically with the new version."
            )
            status_dialog.set_position(Gtk.WindowPosition.CENTER)
            status_dialog.set_keep_above(True)
            status_dialog.show_all()

            def install():
                try:
                    success, message = update_checker.install_appimage_update(downloaded_path)
                except Exception as install_error:
                    logger.error(f"Exception during install_appimage_update: {install_error}")
                    success = False
                    message = f"Install failed with exception: {install_error}"
                GLib.idle_add(installed, success, message)

            def installed(success, message):
                status_dialog.destroy()
                logger.info(f"install_appimage_update returned: success={success}, "
                            f"message={message}")
                if success:
                    self.restart_app()
                else:
                    show_message(None, Gtk.MessageType.ERROR, "Update Failed", message,
                                 keep_above=True)
                return False
            threading.Thread(target=install, name="install-update").start()
            return False

        # Start download
        thread = threading.Thread(target=do_download, daemon=True)
        thread.start()

    def _download_and_install_package(self, release, install_type):
        """Download the correct .rpm/.deb and install it as root via pkexec,
        then offer to restart. Delegates to the shared update_ui flow."""
        from . import update_ui
        update_ui.run_package_update(release, install_type,
                                     restart_callback=self.restart_app)

    def auto_check_for_updates(self):
        """
        Automatically check for updates on startup (once per day).

        Silently checks in the background. If an update is found,
        shows a notification that opens the Updates tab when clicked.
        """
        import threading
        from . import update_checker
        from .config import load_config, record_update_check

        config = load_config()

        # Check if auto-check is enabled
        if not config.auto_check_updates:
            logger.debug("Auto-update check disabled in config")
            return False

        # Check if we already checked today
        if not update_checker.should_check_today(config.last_update_check):
            logger.debug("Already checked for updates today")
            return False

        logger.info("Auto-checking for updates...")

        def do_check():
            """Background thread to check for updates."""
            try:
                result = update_checker.check_for_updates()

                # Record the timestamp against a fresh read: `config` was loaded
                # before the network call, which can take ~30 s, and saving it
                # back would revert anything changed in Preferences meanwhile.
                record_update_check(
                    update_checker.get_current_timestamp(), _stale=config)

                if result and result.get("success"):
                    has_update = result.get("update_available", False)
                    has_ext_update = result.get("extension_update", False)

                    if has_update:
                        # Show the SAME actionable dialog the manual check uses
                        # (Download & Install / View on GitHub) — persistent, and
                        # identical across the GTK tray and the GNOME extension.
                        # No surprise Preferences window.
                        GLib.idle_add(lambda: self._show_update_result_dialog(result))
                    elif has_ext_update:
                        # Extension-only update: a quiet notification, no windows.
                        GLib.idle_add(self._show_update_notification)
                    else:
                        logger.info("No updates available")
                else:
                    logger.debug(f"Update check failed: {result.get('error', 'unknown')}")
            except Exception as e:
                logger.error(f"Error in auto-update check: {e}")

        # Run in background thread
        thread = threading.Thread(target=do_check, daemon=True)
        thread.start()
        return False  # Don't repeat GLib timeout

    def _show_update_notification(self):
        """Tell the user a GNOME extension update is out. Check for Updates
        covers only the app, so point to the tab that installs it."""
        from .notify import notify
        notify("GNOME extension update available",
               "Open Preferences → Updates & Backup to install it.")
        logger.info("Showed extension update notification")
        return False

    def restart_app(self, _=None):
        """Cleanly restart the whole app after an update: launch a detached
        instance that waits for us to exit, then quit this one via quit_app
        (which stops the dictation service and releases the D-Bus name + lock),
        so the new instance starts on a clean slate with the new service.
        Both update paths end here: the tray's own Download & Install, and
        Preferences (a separate process) asking over D-Bus.
        Returns False so it can be scheduled with GLib.idle_add."""
        from . import update_checker
        # A self-managed AppImage was just swapped in place at APPIMAGE_PATH;
        # packages are started through their /usr/bin launcher.
        target = (update_checker.APPIMAGE_PATH
                  if update_checker.get_install_type() == "appimage" else None)
        ok, msg = update_checker.restart_via_launcher(target)
        if not ok:
            logger.error(f"Restart failed: {msg}")
            dlg = message_dialog(
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="Update installed"
            )
            dlg.format_secondary_text(
                "TuxTalk couldn't restart by itself. Quit TuxTalk from the tray "
                "icon and open it again to start the new version.")
            dlg.set_position(Gtk.WindowPosition.CENTER)
            dlg.set_keep_above(True)
            dlg.connect("response", lambda d, _r: d.destroy())
            dlg.show_all()
            return False
        logger.info("Restart scheduled; quitting this instance to hand off.")
        self.quit_app(None)
        return False

    def quit_app(self, _):
        """Quit the tray and stop the dictation service."""
        self._shut_down()
        self._leave_main_loop()

    def uninstall(self, _=None):
        """Preferences' Uninstall TuxTalk... and tuxtalk --uninstall: ask,
        remove everything (uninstall.py), then quit."""
        # A second request while the question is up would open another one
        # inside the first's dialog loop, and neither could close properly.
        if self._uninstalling:
            logger.info("Uninstall already asked; ignoring the second request")
            return False
        self._uninstalling = True
        from . import uninstall
        try:
            done = uninstall.run(stop_tray_parts=self._shut_down)
        finally:
            self._uninstalling = False
        if done:
            self._leave_main_loop()
        return False  # one-shot when scheduled through GLib

    def _shut_down(self):
        """Everything Quit stops, short of leaving the main loop."""
        # Before anything else: take our script back out of KWin. Leaving it
        # loaded means it keeps calling a D-Bus name that no longer exists.
        self._stop_kwin_focus_provider()

        try:
            self._kill_service()
            logger.info("Stopped dictation service")
        except Exception as e:
            logger.error(f"Error stopping dictation service: {e}")

        # Close preferences window if open
        if self.preferences_process and self.preferences_process.poll() is None:
            try:
                self.preferences_process.terminate()
                self.preferences_process.wait(timeout=2)
                logger.info("Closed preferences window")
            except Exception as e:
                logger.error(f"Error closing preferences window: {e}")
                try:
                    self.preferences_process.kill()
                except Exception:
                    pass

        # Clean up D-Bus service if it exists
        if self.dbus_service:
            try:
                # Remove from connection to unregister the service
                self.dbus_service.remove_from_connection()
                logger.info("D-Bus service unregistered")
            except Exception as e:
                logger.error(f"Error unregistering D-Bus service: {e}")
            self.dbus_service = None

    def _leave_main_loop(self):
        # Quit the GTK main loop; main() returns and the process exits.
        Gtk.main_quit()

        # Only fires when Quit is picked while a dialog.run() or nested
        # Gtk.main() is open: main_quit() cannot end the outer loop from there,
        # and the tray and its icon would stay until the dialog is closed.
        GLib.timeout_add(100, lambda: os._exit(0))

    def _build_performance_submenu(self):
        """Build the Performance preset submenu (Recommended, Lightest, Battery saver + Custom).

        Stores radio button references on self for later state updates.
        Returns the parent MenuItem that contains the submenu.
        """
        submenu = Gtk.Menu()
        self.preset_radios = {}
        preset_group = None
        for preset in self._current_presets():
            label = f"{preset.label} ({preset.description})"
            radio = (Gtk.RadioMenuItem(label=label) if preset_group is None
                     else Gtk.RadioMenuItem(label=label, group=preset_group))
            preset_group = preset_group or radio
            # GTK fires "activate" on the radio being unselected too; act on the selected one.
            radio.connect("activate", lambda w, pid=preset.id: w.get_active() and self.set_performance_preset(pid))
            submenu.append(radio)
            self.preset_radios[preset.id] = radio

        # Custom has the dot when the settings match no preset; it opens Preferences.
        submenu.append(Gtk.SeparatorMenuItem())
        self.preset_custom = Gtk.RadioMenuItem(label="Custom (opens Preferences)", group=preset_group)
        self.preset_custom.connect("activate", self._on_custom_preset)
        submenu.append(self.preset_custom)

        item = Gtk.MenuItem(label="Performance")
        item.set_submenu(submenu)
        return item

    def _on_custom_preset(self, item):
        """Performance > Custom: open Preferences, then put the dot back on the
        setup in use (clicking a radio item moves it)."""
        # "activate" also comes for the radio being unselected, and for the
        # tray moving the dot itself.
        if getattr(self, "_updating_preset", False) or not item.get_active():
            return
        self.open_preferences(None)

        def put_dot_back():
            self._revert_preset_radio()
            return False    # once

        # Delayed: KDE ticks the clicked item in its own copy of the menu, and
        # a move there and back in the same main-loop pass reaches it as no
        # change and leaves two dots.
        GLib.timeout_add(150, put_dot_back)

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

    def show_fix_word_dialog(self, index=0):
        """Open Fix a Word on recent dictation *index* (0 = newest).

        Also reached from the GNOME extension over D-Bus (FixWordInDictation).
        The fix is saved as a custom voice command; see vocabulary.py.
        """
        from .fix_word_dialog import show_fix_word_dialog
        show_fix_word_dialog(index, copy_text=self._copy_history_entry)
        return False  # one-shot when scheduled through GLib.idle_add

    def _copy_history_entry(self, text):
        """Put a recent dictation on the clipboard (see clipboard.py)."""
        from .clipboard import copy_text
        if copy_text(text):
            logger.info(f"Copied a recent dictation to the clipboard ({len(text)} chars)")

    def _clear_history(self):
        from .history import clear
        clear()
        self._refresh_history_menu()

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

        # Service toggle and status displays
        # A checkmark ICON rather than a Gtk.CheckMenuItem: KDE gives
        # checkboxes their own column beside the icon column, so a real
        # checkbox sat to the left of the Preferences gear with every label
        # pushed further right. With no checkbox the two share one column.
        self.service_toggle = _IconToggleItem("Dictation on/off", self.toggle_service)
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
        name = _themed_icon("emblem-system-symbolic", "preferences-system-symbolic")
        if name is None:
            return Gtk.MenuItem(label=label)
        item = Gtk.ImageMenuItem(label=label)
        item.set_image(Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU))
        item.set_always_show_image(True)
        return item

    def refresh_menu(self):
        """Rebuild the tray menu (after onboarding has changed the setup)."""
        self.indicator.set_menu(self.build_menu())


def _refresh_kde_menu_cache():
    """Rebuild KDE's launcher cache so a new TuxTalk menu entry shows at once."""
    for tool in ("kbuildsycoca6", "kbuildsycoca5"):
        try:
            subprocess.run([tool], capture_output=True, timeout=15)
            logger.info(f"KDE menu cache refreshed ({tool})")
            return
        except FileNotFoundError:
            continue  # not this KDE version, or not KDE at all
        except Exception as e:
            logger.debug(f"Could not refresh KDE menu cache: {e}")
            return


def _show_rename_notice_once():
    """'TalkType is now TuxTalk', once, after this start converted an old
    TalkType install (rename_migration leaves a marker for it). It is cleared
    only once the message has been seen, so quitting first just shows it on
    the next start. Never during dictation: only here, at startup."""
    from . import config, rename_migration
    folder = os.path.dirname(config.CONFIG_PATH)
    if not rename_migration.notice_pending(folder):
        return
    dialog = message_dialog(
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.NONE,
        text="TalkType is now TuxTalk",
    )
    dialog.format_secondary_text("\n\n".join((
        "Same app, new name. Your settings, models, snippets and stats all came "
        "across. Look for TuxTalk in your app menu from now on.",
        "Why the change: another company owns the name TalkType for dictation "
        "software.")))
    dialog.add_button("Got it", Gtk.ResponseType.OK)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)
    dialog.run()
    dialog.destroy()
    rename_migration.notice_shown(folder)
    logger.info("Rename to TuxTalk: showed the one-time notice")


def main():
    from . import desktop_identity, ui_style
    desktop_identity.apply()        # TuxTalk's icon on its windows, not KDE's generic one
    ui_style.prepare_windows()   # emoji that show, buttons clear of panels and docks
    # tuxtalk --toggle/--start/--stop (control.py). With TuxTalk running this
    # asks it and exits; otherwise this process starts TuxTalk and the request
    # waits for the speech model below.
    from . import control
    command = control.parse_action(sys.argv[1:])
    if command:
        _run_command_line(command)
    # tuxtalk --open NAME / --quit (the launcher's right-click shortcuts).
    open_window = control.parse_open(sys.argv[1:])
    if open_window:
        _open_from_command_line(open_window)

    # Before anything opens the shared D-Bus connection: one opened without
    # the GLib main loop can't export the tray's D-Bus service later.
    try:
        import dbus.mainloop.glib
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    except ImportError:
        pass
    _acquire_tray_singleton()

    # An old TalkType Flatpak can't see the lock above and would type every
    # dictation twice. Checked on a thread so a slow flatpak never delays the tray.
    import threading
    threading.Thread(target=_handle_old_talktype_flatpak, daemon=True).start()

    # The TalkType -> TuxTalk folder move ran at package import, before any
    # logger existed; record what it did now (see rename_migration.py).
    from . import MIGRATION_LOG
    for line in MIGRATION_LOG:
        logger.info(f"Rename to TuxTalk: {line}")
    if not os.environ.get("FLATPAK_ID"):
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
        from .update_checker import add_launcher_actions, create_hidden_desktop_entry
        create_hidden_desktop_entry()   # TuxTalk's icon on first-run windows
        add_launcher_actions()          # right-click shortcuts for older launchers

    # A copy of the settings to roll back to (Preferences → Advanced →
    # Restore Earlier Settings), taken before this version touches them.
    from .settings_backup import snapshot_if_changed
    snapshot_if_changed()

    from .uinput_helper import retire_ydotoold
    retire_ydotoold()       # what TuxTalk typed through until 0.18

    tray = DictationTray()

    # Check if we just updated and show notification
    def check_and_show_update_notification():
        try:
            from . import update_checker
            from . import __version__
            previous_version = update_checker.check_just_updated()
            if previous_version:
                # Show a brief notification that update completed
                dialog = message_dialog(
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text="Update Complete!"
                )
                dialog.format_secondary_markup(
                    f"TuxTalk has been updated to <b>v{__version__}</b>\n\n"
                    f"The update was installed automatically and "
                    f"TuxTalk has restarted with the new version."
                )
                dialog.set_position(Gtk.WindowPosition.CENTER)
                dialog.set_keep_above(True)
                dialog.run()
                dialog.destroy()
        except Exception as e:
            logger.debug(f"Error checking for update notification: {e}")
        # After "Update Complete!", never on top of it.
        try:
            _show_rename_notice_once()
        except Exception as e:
            logger.warning(f"Could not show the rename notice: {e}")
        return False  # Don't repeat

    # Schedule update notification check after tray is up
    GLib.timeout_add(500, check_and_show_update_notification)

    # A first-run hotkey test that was killed mid-way leaves GNOME's Alt+F8 and
    # Alt+F7 cleared; put them back.
    try:
        from .gnome_keybindings import restore_left_over
        restore_left_over()
    except Exception as e:
        logger.debug(f"Could not check GNOME keybindings: {e}")

    # Check for first run and show welcome dialog if applicable
    try:
        import tuxtalk.cuda_helper as cuda_helper
        from tuxtalk.welcome_dialog import show_welcome_and_install

        # Only show welcome dialog on first tray launch (not for prefs)
        if cuda_helper.is_first_run():
            logger.info("First run detected - starting onboarding")
            if command:
                control.report("setup")     # the welcome screens come first
            # Set flag BEFORE scheduling to prevent any auto-start during onboarding
            tray.onboarding_in_progress = True

            # Kill any existing service that might be running - use SIGKILL for
            # immediate termination. service_launcher's patterns never match
            # the tray's own command line.
            from tuxtalk.service_launcher import stop_dictation_service
            stop_dictation_service(force=True)
            time.sleep(0.5)  # Give processes time to die

            # Schedule the welcome dialog after tray is initialized
            def show_first_run_setup():
                # Show unified welcome dialog with all setup options. An error
                # in onboarding counts as a cancel, so the service still starts
                # and the welcome comes back next launch.
                try:
                    result = show_welcome_and_install()
                except Exception:
                    logger.error("Onboarding failed", exc_info=True)
                    result = None

                # Unsupported desktop (no GlobalShortcuts portal): the user can only
                # quit — the app cannot function here, so exit instead of starting a
                # dead service. First run is left unmarked so a later install on a
                # supported desktop still onboards.
                if result and result.get('quit'):
                    logger.info("Onboarding: dictation hotkey unsupported on this desktop - quitting")
                    tray.quit_app(None)
                    return False

                # Only mark first run complete if user completed the wizard
                if result and result.get('continue'):
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
                import threading
                threading.Thread(target=_refresh_kde_menu_cache, daemon=True).start()

                GLib.timeout_add(500, tray._auto_start_service)
                return False  # Don't repeat

            GLib.timeout_add(1500, show_first_run_setup)  # Show after 1.5 seconds
        else:
            # Not first run, auto-start immediately
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
                logger.debug(f"Could not check the login entry: {e}")
            if command:
                # The auto-start below starts the service; the recording
                # waits until its model is loaded.
                status = tray._waiting_start.apply(command)
                logger.info(f"Started with --{command}: {status}")
                control.report(status)
            GLib.timeout_add(1000, tray._auto_start_service)
            if open_window and open_window != "quit":
                # Started by --open (a launcher shortcut) with nothing running.
                GLib.timeout_add(1500, tray.open_window, open_window)
            # Check for updates after a delay (don't interfere with startup)
            GLib.timeout_add(5000, tray.auto_check_for_updates)
    except Exception as e:
        # If any error, only auto-start if NOT during onboarding
        logger.error(f"Error in first run setup: {e}")
        if not tray.onboarding_in_progress:
            GLib.timeout_add(1000, tray._auto_start_service)

    Gtk.main()

if __name__ == "__main__":
    main()
