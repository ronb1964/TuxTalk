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
import threading
import sys
import warnings
from .logger import setup_logger
from . import notes_text, tray_instance, update_offers
from .tray_service import ServiceLifecycle

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


class DictationTray(ServiceLifecycle):
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

        # After updating: the recommended setup, and the hotkey helper or
        # Parakeet Ultra this TuxTalk brings.
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

    def update_icon_status(self, is_running=None):
        """The icon for the service's state, *is_running* if it's known already."""
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

        release_notes = notes_text.NotesView("Loading release notes...")
        scroll.add(release_notes.view)
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
            GLib.idle_add(release_notes.show, notes)

        thread = threading.Thread(target=fetch_notes, daemon=True)
        thread.start()

    def check_for_updates_clicked(self, _):
        """Check for updates and show results dialog."""
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
            if self._quitting:              # the check outlasted a Quit
                return False
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

            if not self._quitting:      # the check outlasted a Quit
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

            scroll.add(notes_text.NotesView(release.get("body", "")).view)

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
        """Download and install the AppImage update behind the progress window
        Preferences uses too (progress_window), then restart onto it. Nothing
        once Quit has begun, and nothing installed if Quit began during the
        download; an install under way finishes (_install_not_cut_off)."""
        if self._quitting:
            return
        from . import update_checker
        from .progress_window import run_with_progress
        url = release.get("appimage_url")
        if not url:
            show_message(None, Gtk.MessageType.ERROR, "Download Error",
                         "Could not find download URL.", keep_above=True)
            return

        def work(progress):
            """On its own thread: the download, then the install; (installed, why not)."""
            path = update_checker.download_update(
                url, release.get("appimage_name", "TuxTalk-update.AppImage"), progress,
                checksums_url=release.get("checksums_url"))
            if not path:
                return False, ("The download could not be completed.\n"
                               "Please try again or download manually from GitHub.")
            if self._quitting:
                return False, None
            progress("Installing update...", 90)
            return self._install_not_cut_off(
                lambda: update_checker.install_appimage_update(path, progress))

        def finish(result):
            success, message = result or (False, "Something TuxTalk didn't expect went "
                                                 "wrong; the log has the details.")
            logger.info(f"AppImage update: installed={success}, {message}")
            if success:
                self.restart_app()
            elif message and not self._quitting:
                show_message(None, Gtk.MessageType.ERROR, "Update Failed", message,
                             keep_above=True)

        run_with_progress(None, "Updating TuxTalk", "Starting download...", work, finish)

    def _download_and_install_package(self, release, install_type):
        """Download the correct .rpm/.deb and install it as root via pkexec,
        then offer to restart. Delegates to the shared update_ui flow."""
        if self._quitting:
            return
        from . import update_ui
        update_ui.run_package_update(release, install_type, restart_callback=self.restart_app,
                                     quitting=lambda: self._quitting)

    def auto_check_for_updates(self):
        """
        Automatically check for updates on startup (once per day).

        Silently checks in the background. An app update opens the same
        result window as a manual check; an extension update only a
        notification.
        """
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
                        GLib.idle_add(lambda: None if self._quitting
                                      else self._show_update_result_dialog(result))
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
        tray_instance.run_command_line(command)
    # tuxtalk --open NAME / --quit (the launcher's right-click shortcuts).
    open_window = control.parse_open(sys.argv[1:])
    if open_window:
        tray_instance.open_from_command_line(open_window)

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
    tray.quit_on_sigterm()

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
                threading.Thread(target=_refresh_kde_menu_cache, daemon=True).start()

                tray._auto_start_service(500)
                return False  # Don't repeat

            tray.onboard_once_service_gone(show_first_run_setup)
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
            tray._auto_start_service(1000)
            if open_window and open_window != "quit":
                # Started by --open (a launcher shortcut) with nothing running.
                GLib.timeout_add(1500, tray.open_window, open_window)
            # Check for updates after a delay (don't interfere with startup)
            GLib.timeout_add(5000, tray.auto_check_for_updates)
    except Exception as e:
        # If any error, only auto-start if NOT during onboarding
        logger.error(f"Error in first run setup: {e}")
        if not tray.onboarding_in_progress:
            tray._auto_start_service(1000)

    Gtk.main()

if __name__ == "__main__":
    main()
