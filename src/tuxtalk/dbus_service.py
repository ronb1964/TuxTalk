#!/usr/bin/env python3
"""
TuxTalk's D-Bus service, held by the tray: the GNOME extension's menu and
signals, the focus reports from the KWin script and the extension, and the
dictation service's calls and questions to the tray.
"""

from typing import NamedTuple

from gi.repository import GLib
import dbus
import dbus.service
import dbus.mainloop.glib

from .logger import setup_logger

logger = setup_logger(__name__)


class FocusReport(NamedTuple):
    """What the GNOME extension or the KWin focus script last said about the
    focused window, "" for what it didn't: its class, the compositor's own id
    for it (KWin's internalId, Mutter's window id), which tells two windows
    of one app apart, and its frame, "x,y,w,h|output|ox,oy,ow,oh"
    (window_geometry.py). Paste picks Ctrl+V or, in a terminal,
    Ctrl+Shift+V by the class (focused_window.py)."""
    wm_class: str = ""
    window_id: str = ""
    geometry: str = ""


# Replaced whole by each report, never changed in place: the dictation
# service reads it from other threads when it holds the bus name itself, and
# must get one window's class, id and frame together.
_focus = FocusReport()

# True from a report of wl-copy's brief window (desktop_identity) until the
# next window's. Its reports are left out, and so is the geometry sent after
# each of them: the focus goes back to the window before it.
_clipboard_tool_focused = False


def cached_focus() -> FocusReport:
    """The focused window as GetFocusedWindow answers, for the dictation
    service when it holds the bus name itself (no tray running)."""
    return _focus


# The bus identifiers, at module level so providers that fill the focused-window
# cache (the GNOME extension's Python side, and kwin_focus on KDE) can name the
# same service without a second copy of these strings.
DBUS_NAME = "io.github.ronb1964.TalkType"
DBUS_PATH = "/io/github/ronb1964/TalkType"
DBUS_INTERFACE = "io.github.ronb1964.TalkType"

# The error a window or uninstall the command line asks for gets while the
# tray quits (_dispatch_from_command_line).
QUITTING_ERROR = DBUS_INTERFACE + ".Error.Quitting"


def _version_or_unknown(version):
    """An extension version for the D-Bus signal: -1 when it isn't known
    (0 is a version, which "or -1" turned into unknown)."""
    return -1 if version is None else version


class TuxTalkDBusService(dbus.service.Object):
    """
    D-Bus service interface for TuxTalk

    Bus Name: io.github.ronb1964.TalkType
    Object Path: /io/github/ronb1964/TalkType
    Interface: io.github.ronb1964.TalkType
    """

    def __init__(self, app_instance, primary=True):
        """Initialize D-Bus service with reference to app instance.

        *primary* is True for the tray, which owns the bus name: the GNOME
        extension talks to it, and the dictation service reports recording
        state to it over D-Bus. The dictation service registers too
        (primary=False), only as a stand-in for when no tray is running.

        The tray always takes the name, from a stand-in service too. The
        service never queues for it, so it can't inherit the name from a
        restarting tray; if the tray already has it, the service gets
        NameExistsException.
        """
        self.app = app_instance
        # Hotkeys the running service asked the GNOME extension to hold back.
        self.claimed_hotkeys = []
        # (model, device) the running service really loaded; None until it says.
        self.running_engine = None

        # Set up D-Bus main loop
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

        # Get session bus
        self.bus = dbus.SessionBus()

        if primary:
            self.bus_name = dbus.service.BusName(
                DBUS_NAME, bus=self.bus, replace_existing=True)
        else:
            self.bus_name = dbus.service.BusName(
                DBUS_NAME, bus=self.bus, allow_replacement=True, do_not_queue=True)

        # Initialize parent
        super().__init__(self.bus_name, DBUS_PATH)

        if self.owns_name():
            logger.info(f"D-Bus service started: {DBUS_NAME}")
        else:
            # Only possible for the tray, behind an older TuxTalk that does
            # not allow its name to be replaced.
            logger.warning(f"D-Bus name {DBUS_NAME} is held by another process; "
                           "waiting in line for it")

    def owns_name(self):
        """Whether this process currently owns the TuxTalk bus name."""
        try:
            return self.bus.get_name_owner(DBUS_NAME) == self.bus.get_unique_name()
        except dbus.exceptions.DBusException as e:     # no owner, or the bus went away
            logger.info(f"Couldn't ask the bus who holds {DBUS_NAME}: {e}")
            return False

    # ==================== Internal Helpers ====================

    def _dispatch(self, method_name, *args):
        """Dispatch a D-Bus method call to the app instance via GLib main loop.

        Logs the call and schedules the app's method on the main loop; a
        method the app does not have is ignored, and so is every call once
        the tray is quitting.
        """
        logger.debug(f"D-Bus: {method_name} called" +
                      (f" with {args}" if args else ""))
        if getattr(self.app, "quitting", False):
            logger.info(f"D-Bus: {method_name} refused, TuxTalk is quitting")
            return
        if hasattr(self.app, method_name):
            GLib.idle_add(getattr(self.app, method_name), *args)

    def _dispatch_from_command_line(self, method_name):
        """_dispatch for a call tuxtalk --open or --uninstall makes too
        (control.OPEN_METHODS). Refused while quitting with QUITTING_ERROR:
        refused quietly, the command took the silence for done and exited
        as if the window had opened or TuxTalk had been removed."""
        if getattr(self.app, "quitting", False):
            logger.info(f"D-Bus: {method_name} refused, TuxTalk is quitting")
            raise dbus.exceptions.DBusException(
                "TuxTalk is quitting; try again once it has gone", name=QUITTING_ERROR)
        self._dispatch(method_name)

    # ==================== Status ====================

    def _current_model(self):
        """The model the service really runs, or the configured one before it
        has reported (see NotifyRunningEngine)."""
        running = self.running_engine
        if running:
            return running[0]
        if hasattr(self.app, 'config'):
            return str(self.app.config.model)
        return 'unknown'

    def _device_type(self):
        """The device the model really runs on (cpu, cuda or vulkan), or the
        configured one before the service has reported."""
        running = self.running_engine
        if running:
            return running[1]
        if hasattr(self.app, 'config'):
            from .parakeet_engine import effective_device
            cfg = self.app.config
            return str(effective_device(getattr(cfg, 'model', ''), getattr(cfg, 'device', 'cpu')))
        return 'cpu'

    @dbus.service.method(DBUS_INTERFACE, out_signature='a{sv}')
    def GetStatus(self):
        """What the GNOME extension's menu shows: recording, the model and
        where it runs, the hotkeys to grab and the performance preset."""
        status = {
            'recording': bool(getattr(self.app, 'is_recording', False)),
            'service_running': bool(getattr(self.app, 'service_running', False)),
            'model': self._current_model(),
            'device': self._device_type(),
        }

        # Older GNOME extensions tell the battery-saving preset apart by its
        # auto-timeout; current ones read 'preset' below. Adding keys to this
        # dict is backward-compatible: extensions ignore what they do not read.
        if hasattr(self.app, 'config'):
            # The GNOME menu's line about what runs, word for word the tray's.
            from .recommend import engine_line
            status['engine'] = engine_line(getattr(self.app.config, 'model', ''),
                                           getattr(self.app.config, 'device', 'cpu'),
                                           self.running_engine)
            status['auto_timeout_enabled'] = bool(
                getattr(self.app.config, 'auto_timeout_enabled', False))
            status['auto_timeout_minutes'] = int(
                getattr(self.app.config, 'auto_timeout_minutes', 0))

        # The hotkeys the extension grabs while the service runs. An explicit
        # signature, or an empty list could not be sent as a variant.
        status['hotkeys'] = dbus.Array(self.claimed_hotkeys, signature='s')

        current = getattr(self.app, "current_preset", None)
        status['preset'] = str(current() if current else "custom")

        return status

    # ==================== Actions ====================

    @dbus.service.method(DBUS_INTERFACE)
    def StartRecording(self):
        """Start recording (hold mode)"""
        self._dispatch('start_recording')

    @dbus.service.method(DBUS_INTERFACE)
    def StopRecording(self):
        """Stop recording"""
        self._dispatch('stop_recording')

    @dbus.service.method(DBUS_INTERFACE)
    def ToggleRecording(self):
        """Toggle recording on/off"""
        self._dispatch('toggle_recording')

    @dbus.service.method(DBUS_INTERFACE, in_signature='s', out_signature='s')
    def ControlDictation(self, action: str):
        """tuxtalk --toggle, --start or --stop (control.py): starts the service
        if needed and keeps the request until its model is loaded. Answered
        here rather than dispatched, as the command prints the status."""
        from .control import ACTIONS
        control = getattr(self.app, "control", None)
        if str(action) not in ACTIONS or control is None:
            return "unknown"
        logger.info(f"D-Bus: command line --{action}")
        return str(control(str(action)))

    @dbus.service.method(DBUS_INTERFACE, in_signature='u')
    def NotifyServiceReady(self, pid):
        """Called by the dictation service once its model is loaded and a
        toggle is acted on (sent after NotifyRunningEngine). *pid* is the
        service's, so a report from a service since replaced counts for nothing."""
        self._dispatch('service_ready', int(pid))

    @dbus.service.method(DBUS_INTERFACE)
    def StartService(self):
        """Start the dictation service"""
        self._dispatch('start_service')

    @dbus.service.method(DBUS_INTERFACE)
    def StopService(self):
        """Stop the dictation service"""
        self._dispatch('stop_service')

    @dbus.service.method(DBUS_INTERFACE)
    def RestartService(self):
        """Restart the dictation service, as the user asked: with Dictation
        off it turns it on."""
        self._dispatch('restart_service')

    @dbus.service.method(DBUS_INTERFACE)
    def RestartIfOn(self):
        """Restart the dictation service onto new settings, as Preferences
        asks after a change: only while Dictation is on."""
        self._dispatch('restart_if_on')

    @dbus.service.method(DBUS_INTERFACE, in_signature='sss')
    def SetFocusedWindow(self, wm_class: str, window_id: str, geometry: str):
        """The focused window, from the KWin script or the GNOME extension on
        every focus change: its class, the compositor's id for it and its
        frame (FocusReport), in one call, so a look can't pair one window's
        class with the next one's id."""
        global _focus, _clipboard_tool_focused
        from .desktop_identity import is_clipboard_tool_class
        _clipboard_tool_focused = is_clipboard_tool_class(wm_class)
        if _clipboard_tool_focused:
            return
        # TuxTalk's own windows are kept as told: focused_window.look knows
        # them by their class, with or without an id.
        if not wm_class:
            window_id = ""
        if wm_class != _focus.wm_class:
            logger.info(f"Focused window class: {wm_class or None!r}")
        _focus = FocusReport(str(wm_class), str(window_id or ""), str(geometry or ""))

    @dbus.service.method(DBUS_INTERFACE, in_signature='s')
    def SetFocusedWindowClass(self, wm_class: str):
        """The focused window's class alone, from a GNOME extension of 0.18
        or earlier, still loaded until the user logs out after an update. Its
        frame follows in SetFocusedWindowGeometry; until then it isn't known."""
        self.SetFocusedWindow(wm_class, "", "")

    @dbus.service.method(DBUS_INTERFACE, in_signature='s')
    def SetFocusedWindowGeometry(self, geometry: str):
        """Where the focused window is now, from the KWin script or GNOME
        extension, sent as it moves or is resized: "x,y,w,h|output|ox,oy,ow,oh"
        (window_geometry.py). Sent on every move, so it is cached without
        logging."""
        global _focus
        if not _clipboard_tool_focused:
            _focus = _focus._replace(geometry=str(geometry or ""))

    @dbus.service.method(DBUS_INTERFACE, out_signature='sss')
    def GetFocusedWindow(self):
        """The focused window's class, id and frame, "" for what isn't known:
        the cache the focus reporters push into, for the dictation service,
        which runs in a process of its own (focused_window.py)."""
        return tuple(_focus)

    @dbus.service.method(DBUS_INTERFACE, out_signature='a(sss)')
    def GetPresets(self):
        """The Performance presets for this computer: (id, label, description).
        The GNOME extension builds its menu from this, so it can't drift from
        the tray's."""
        get = getattr(self.app, "get_presets", None)
        try:
            presets = get() if get else []
        except Exception as e:
            # An empty list, not a D-Bus error: the menu keeps working.
            logger.error(f"Could not work out the presets: {e}")
            presets = []
        return dbus.Array([dbus.Struct(p, signature='sss') for p in presets],
                          signature='(sss)')

    @dbus.service.method(DBUS_INTERFACE, in_signature='s')
    def ApplyPerformancePreset(self, preset: str):
        """Apply a performance preset (recommended/lightest/battery)"""
        self._dispatch('set_performance_preset', preset)

    @dbus.service.method(DBUS_INTERFACE, out_signature='as')
    def GetRecentDictations(self):
        """Recent dictations, newest first (see history.py).

        Read straight from the history file rather than dispatched to the app:
        it is a quick file read, and the caller needs the answer back.
        """
        from .history import get_entries
        return get_entries()

    @dbus.service.method(DBUS_INTERFACE, out_signature='s')
    def GetPasteLastHotkey(self):
        """The Paste Last Dictation combo ("" when off), so the GNOME
        extension's Recent Dictations menu can mention it like the tray's."""
        from . import config
        return getattr(config.load_config(), "paste_last_hotkey", "") or ""

    @dbus.service.method(DBUS_INTERFACE)
    def ClearRecentDictations(self):
        """Forget every recent dictation. It may wait for another change to
        the list, so it runs on a thread of its own, not this D-Bus loop."""
        from .history import clear_in_background
        clear_in_background()

    @dbus.service.method(DBUS_INTERFACE)
    def FixWordInDictation(self):
        """Open the Fix a Word window on the newest recent dictation."""
        self._dispatch_from_command_line('show_fix_word')

    @dbus.service.method(DBUS_INTERFACE)
    def TranscribeFile(self):
        """Open the Transcribe a File window."""
        self._dispatch_from_command_line('show_transcribe_file')

    @dbus.service.method(DBUS_INTERFACE, out_signature='b')
    def ShowMenu(self):
        """Show TuxTalk's menu as a window (menu_window.py). Called when
        TuxTalk is opened again while it runs. False when this process has no
        menu window (the dictation service standing in for a missing tray)."""
        if not hasattr(self.app, 'show_menu') or getattr(self.app, "quitting", False):
            return False
        self._dispatch('show_menu')
        return True

    @dbus.service.method(DBUS_INTERFACE)
    def OpenPreferences(self):
        """Open the preferences window"""
        self._dispatch_from_command_line('show_preferences')

    @dbus.service.method(DBUS_INTERFACE)
    def OpenPreferencesStats(self):
        """Open the preferences window on the Stats tab ("Your Stats...")"""
        self._dispatch('show_preferences_stats')

    @dbus.service.method(DBUS_INTERFACE)
    def OpenPreferencesUpdates(self):
        """Open the preferences window directly to the Updates tab"""
        self._dispatch('show_preferences_updates')

    @dbus.service.method(DBUS_INTERFACE)
    def ShowHelp(self):
        """Show the help dialog"""
        self._dispatch_from_command_line('show_help')

    @dbus.service.method(DBUS_INTERFACE)
    def ShowVoiceCommands(self):
        """Show the voice commands quick reference popup"""
        self._dispatch('show_voice_commands')

    @dbus.service.method(DBUS_INTERFACE)
    def ShowAbout(self):
        """Show the about dialog"""
        self._dispatch('show_about')

    @dbus.service.method(DBUS_INTERFACE)
    def Quit(self):
        """Quit the application"""
        self._dispatch('quit')

    @dbus.service.method(DBUS_INTERFACE)
    def Uninstall(self):
        """Ask, then remove TuxTalk and everything it set up (uninstall.py).
        Called by Preferences and tuxtalk --uninstall."""
        if not hasattr(self.app, "uninstall"):
            # The dictation service standing in for the tray can't. Saying so
            # lets the caller do it itself instead of taking silence as done.
            raise dbus.exceptions.DBusException(
                "This TuxTalk can't uninstall", name="org.freedesktop.DBus.Error.UnknownMethod")
        self._dispatch_from_command_line('uninstall')

    @dbus.service.method(DBUS_INTERFACE)
    def RestartApp(self):
        """Restart TuxTalk (tray and dictation service) into a just-installed update"""
        self._dispatch('restart_app')

    @dbus.service.method(DBUS_INTERFACE, in_signature='b')
    def NotifyRecordingState(self, is_recording: bool):
        """
        Called by the dictation engine (app.py) to report recording state changes.
        The tray owns the D-Bus name, so only it can emit signals that the
        GNOME extension will receive. App.py calls this method to relay the state.
        """
        logger.debug(f"D-Bus: NotifyRecordingState called: {is_recording}")
        try:
            self.app.is_recording = is_recording
        except AttributeError:
            # The stand-in service reads the state live from its engine (a
            # read-only property), so there is nothing to store; the signal
            # still has to go out.
            pass
        self.RecordingStateChanged(is_recording)

    @dbus.service.method(DBUS_INTERFACE, in_signature='s')
    def NotifyHotkeyPressed(self, key_name: str):
        """
        Called by app.py when a hotkey is pressed during test mode.
        The tray owns the D-Bus name, so only it can emit signals that
        the prefs dialog will receive. key_name is 'hold' or 'toggle'.
        """
        logger.debug(f"D-Bus: NotifyHotkeyPressed called: {key_name}")
        self.HotkeyPressed(key_name)

    @dbus.service.method(DBUS_INTERFACE, in_signature='as')
    def NotifyClaimedHotkeys(self, accelerators):
        """Called by the dictation service with its hotkeys, in GNOME
        accelerator syntax. The GNOME extension grabs them while the service
        runs so they don't also reach the focused app (see gnome_hotkeys).
        """
        self.claimed_hotkeys = [str(a) for a in accelerators]
        logger.debug(f"D-Bus: NotifyClaimedHotkeys: {self.claimed_hotkeys}")
        self.HotkeysChanged(self.claimed_hotkeys)

    @dbus.service.method(DBUS_INTERFACE, in_signature='ss')
    def NotifyRunningEngine(self, model, device):
        """Called by the dictation service once its model is loaded: what it
        really runs, which a fallback can make different from the settings."""
        self.running_engine = (str(model), str(device))
        logger.info(f"D-Bus: service is running {model} on {device}")
        self.ModelChanged(str(model))   # the extension re-reads GetStatus on this

    def clear_recording_state(self):
        """The service stopped, so nothing is recording. A killed or crashed
        service never reports that itself, and the extension's icon would
        stay red."""
        if getattr(self.app, 'is_recording', False):
            self.app.is_recording = False
            self.RecordingStateChanged(False)

    def clear_running_engine(self):
        """The service stopped; until it reports again, show the settings."""
        self.running_engine = None

    def clear_claimed_hotkeys(self):
        """The service stopped. Forget its hotkeys, so a status read during the
        next start can't hand the extension a list that may since have changed."""
        if self.claimed_hotkeys:
            self.claimed_hotkeys = []
            self.HotkeysChanged([])

    @dbus.service.method(DBUS_INTERFACE)
    def CheckForUpdates(self):
        """
        Check for updates asynchronously.
        Results are sent via UpdateCheckComplete signal.
        """
        logger.debug("D-Bus: CheckForUpdates called")
        import threading

        def do_check():
            """Check for updates on its own thread, show the result window and
            tell the extension, with a failure reported the same way."""
            try:
                from . import update_checker
                result = update_checker.check_for_updates()

                # Show a result window the user dismisses, since the extension's
                # GNOME notification is easy to miss. Only the manual check from
                # the extension reaches this method; the auto-check does not.
                # An extension update alone opens the Updates tab from the
                # extension, so a window saying "up to date" would contradict it.
                only_extension = (result.get("extension_update")
                                  and not result.get("update_available"))
                if (result.get("success") and not only_extension
                        and hasattr(self.app, "show_update_result")):
                    self.app.show_update_result(result)

                # Also emit the signal so the extension can update its own state/icon.
                GLib.idle_add(
                    self.UpdateCheckComplete,
                    result.get("success", False),
                    result.get("current_version", "unknown"),
                    result.get("latest_version", "unknown"),
                    result.get("update_available", False),
                    _version_or_unknown(result.get("extension_current")),
                    _version_or_unknown(result.get("extension_latest")),
                    result.get("extension_update", False),
                    result.get("release", {}).get("html_url", "") if result.get("release") else "",
                    result.get("error", "") if result.get("error") else ""
                )
            except Exception as e:
                logger.error(f"CheckForUpdates error: {e}")
                GLib.idle_add(
                    self.UpdateCheckComplete,
                    False, "unknown", "unknown", False, -1, -1, False, "", str(e)
                )

        thread = threading.Thread(target=do_check, daemon=True)
        thread.start()

    # ==================== Signals ====================

    @dbus.service.signal(DBUS_INTERFACE, signature='b')
    def RecordingStateChanged(self, is_recording: bool):
        """Emitted when recording state changes"""
        pass

    @dbus.service.signal(DBUS_INTERFACE, signature='b')
    def ServiceStateChanged(self, is_running: bool):
        """Emitted when service state changes"""
        pass

    @dbus.service.signal(DBUS_INTERFACE, signature='s')
    def ModelChanged(self, model_name: str):
        """Emitted when the model is changed"""
        pass

    @dbus.service.signal(DBUS_INTERFACE, signature='as')
    def HotkeysChanged(self, accelerators):
        """The hotkeys the GNOME extension should hold back (empty: none)."""
        pass

    @dbus.service.signal(DBUS_INTERFACE, signature='s')
    def HotkeyPressed(self, key_name: str):
        """Emitted when a hotkey is pressed during test mode (PauseHotkeys).
        key_name is 'hold' or 'toggle'."""
        pass

    @dbus.service.signal(DBUS_INTERFACE, signature='bssbiibss')
    def UpdateCheckComplete(self, success: bool, current_version: str,
                           latest_version: str, update_available: bool,
                           extension_current: int, extension_latest: int,
                           extension_update: bool, release_url: str, error: str):
        """
        Emitted when update check completes.

        Args:
            success: True if check succeeded
            current_version: Current AppImage version
            latest_version: Latest available version
            update_available: True if AppImage update available
            extension_current: Installed extension version (-1 if not installed)
            extension_latest: Latest extension version (-1 if unknown)
            extension_update: True if extension update available
            release_url: URL to release page on GitHub
            error: Error message if check failed
        """
        pass

    # ==================== Helper Methods ====================

    def emit_service_state(self, is_running: bool):
        """Emit service state change signal"""
        self.ServiceStateChanged(is_running)

    def emit_model_changed(self, model_name: str):
        """Emit model changed signal"""
        self.ModelChanged(model_name)

