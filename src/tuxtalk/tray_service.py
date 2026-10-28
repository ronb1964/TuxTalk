"""The tray's hold on the dictation service (a mixin of tray.DictationTray):
starting it at start-up and after setup, Dictation off and on, Restart
Service, Quit and the tray's part in an uninstall. Every wait for the
service runs off GTK's thread (service_launcher.end_in_background), and one
launch number (_service_gen) drops a start scheduled before a later Stop or
Quit."""
import os
import signal

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import GLib, Gtk  # noqa: E402
from gi.repository import AyatanaAppIndicator3 as AppIndicator3  # noqa: E402

from .logger import setup_logger  # noqa: E402
from .ui_style import show_message  # noqa: E402

logger = setup_logger(__name__)


class ServiceLifecycle:
    """DictationTray's methods for starting, stopping and quitting. They use
    its state: _service_gen, _restarting, _restart_pending, _quitting,
    _waiting_start, onboarding_in_progress, preferences_process,
    dbus_service, indicator and _menu_window."""

    def _auto_start_service(self, delay_ms):
        """Start the dictation service *delay_ms* from now, from main() and
        after onboarding, unless one runs (it's adopted) or Dictation is
        turned off meanwhile."""
        GLib.timeout_add(delay_ms, self._check_and_start, self._service_gen)

    def _check_and_start(self, gen, waited=0, watched=0):
        """The start-up check's steps, dropped once *gen* isn't the current
        decision (Dictation turned off meanwhile). A TalkType service from
        before the 0.15.0 rename (left by a 0.14.x update; old code holding
        the shared lock) is stopped and given 3 s (*waited*, 100 ms GLib
        timeouts, so the menu stays usable). One running already is looked
        at each second for QUIT_WAIT_S (*watched*): with no tray it may be
        quitting, and once it's gone one is started."""
        if gen != self._service_gen:
            return False
        try:
            # NEVER start during onboarding - hotkey test needs to capture keys
            if self.onboarding_in_progress:
                logger.info("Onboarding in progress - refusing to auto-start service")
                return False
            from .service_launcher import (QUIT_WAIT_S, pre_rename_service_pids,
                                           stop_dictation_service)
            if pre_rename_service_pids() and waited < 30:
                if waited == 0:
                    logger.info("Stopping the pre-0.15.0 dictation service an update left")
                    stop_dictation_service()
                GLib.timeout_add(100, self._check_and_start, gen, waited + 1)
            elif not self.is_service_running():
                logger.info("Auto-starting dictation service...")
                self.start_service(None)
            elif watched < QUIT_WAIT_S:
                if not watched:
                    logger.info("Dictation service already running; looking again for a while")
                GLib.timeout_add(1000, self._check_and_start, gen, 30, watched + 1)
        except Exception as e:
            logger.error(f"Failed to auto-start service: {e}", exc_info=True)
        return False  # Don't repeat this timer

    def onboard_once_service_gone(self, show_setup):
        """First run: *show_setup* once no dictation service runs. Its hotkey
        test needs F8 and F9, so one running (no tray, or setup's marker gone)
        types what it holds and quits first, with GTK answering it which
        window has the focus. One that won't stop: said, and TuxTalk quits
        (setup stays unmarked, so it comes back next start)."""
        def service_gone(ended):
            if self._quitting:
                return False
            if ended:
                GLib.timeout_add(1500, lambda: False if self._quitting else show_setup())
                return False
            logger.error("A dictation service that was running won't stop; not starting setup")
            show_message(None, Gtk.MessageType.ERROR, "TuxTalk couldn't start its setup",
                         "A dictation service that was already running wouldn't stop. "
                         "Restart the computer, then open TuxTalk again.")
            self.quit_app(None)
            return False
        from .service_launcher import end_in_background, find_service_pids
        end_in_background(service_gone, find_service_pids())

    def _drop_pending_launches(self):
        """A decision about the service (Stop, Quit, Restart, uninstall): a
        launch scheduled before it (start-up, its watch, a restart's relaunch,
        a queued restart) is dropped."""
        self._service_gen += 1
        self._restart_pending = False

    def _launch_service(self):
        """Launch the dictation service subprocess (see service_launcher)."""
        # Delegated so the tray and Preferences start the service identically,
        # including its GDK_BACKEND (see service_launcher for why it matters).
        from .service_launcher import launch_dictation_service

        proc = launch_dictation_service()
        if proc is not None:
            self._service_pid = proc.pid

    def start_service(self, _):
        """Start the dictation service directly."""
        # CRITICAL: Never start service during onboarding - hotkeys must not be active
        if self.onboarding_in_progress:
            logger.info("Onboarding in progress - refusing to start service")
            return
        if self._quitting:
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
        """Dictation off: the service types what it holds and quits; waited
        for (and killed past its time) on a thread of its own."""
        if self._quitting:
            return
        self._drop_pending_launches()
        self._waiting_start.take()          # a --start waiting for the model is off too
        from .service_launcher import end_in_background, find_service_pids
        end_in_background(self._after_stop, find_service_pids())
        logger.info("Asked the dictation service to stop")

    def _after_stop(self, ended):
        """stop_service's second half, on GTK's thread: the icon and the
        GNOME extension's switch show what's really running now."""
        if not ended:
            logger.error("The dictation service is still running after being stopped")
        self.update_status_and_menu(repeat=False)
        if self.dbus_service:
            self._emit_service_state_after_check()
        return False

    def restart_service(self, _):
        """Restart the dictation service: it types what it holds and quits,
        then a new one starts. The wait runs on a thread of its own, so the
        menu stays usable (this fires on every model and preset change, and
        after a Parakeet Ultra download)."""
        # CRITICAL: Never restart service during onboarding
        if self.onboarding_in_progress:
            logger.info("Onboarding in progress - refusing to restart service")
            return
        if self._quitting:
            return

        # Re-entrancy guard: a second restart overlapping this one could kill
        # the freshly-launched service or leave two backends both grabbing the
        # hotkey (double-typing). A request that arrives meanwhile is queued,
        # not dropped: the new service may already have read the old settings.
        # Both this and _relaunch run on the GTK main thread, so no lock is needed.
        if self._restarting:
            logger.info("Restart already in progress - will restart again when it finishes")
            self._restart_pending = True
            return
        self._drop_pending_launches()
        self._restarting = True
        gen = self._service_gen
        from .service_launcher import end_in_background, find_service_pids
        end_in_background(lambda ended: self._relaunch(gen, ended), find_service_pids())

    def _relaunch(self, gen, ended):
        """Restart Service's second half, on GTK's thread once the old service
        is gone (*ended*; if not, the new one waits for its lock). Nothing is
        started when Dictation was turned off or Quit began meanwhile (*gen*);
        a restart asked for since runs now."""
        if not ended:
            logger.error("Restarting with the old dictation service still running")
        self._restarting = False
        if self._quitting:
            return False
        pending, self._restart_pending = self._restart_pending, False
        if gen == self._service_gen:
            try:
                self._launch_service()
            except Exception as e:
                logger.error(f"Failed to restart service: {e}", exc_info=True)
            GLib.timeout_add_seconds(1, lambda: self.update_status_and_menu(repeat=False))
        if pending:         # asked for after this one began (and after any Stop)
            self.restart_service(None)
        return False

    def quit_app(self, _):
        """Quit: the icon goes and nothing new starts at once, then the tray
        leaves once the service has typed what it holds and gone. Until then
        it keeps what the service still asks it (which window has the focus)
        and, from an AppImage, the service's files. The wait runs on a
        thread of its own."""
        if self._quitting:
            return False
        self._hide_for_quit()
        from .service_launcher import end_in_background, find_service_pids
        end_in_background(self._finish_quit, find_service_pids())
        return False  # one-shot when scheduled through GLib

    def _finish_quit(self, ended):
        """Quit's second half, on GTK's thread once the service has gone."""
        if not ended:
            logger.error("Quitting with a dictation service still running")
        self._shut_down()
        self._leave_main_loop()
        return False

    def quit_on_sigterm(self):
        """Quit as Quit does on SIGTERM, SIGHUP and SIGINT (a plain kill, the
        session or terminal ending, Ctrl+C), so the service finishes from a
        mounted AppImage; unhandled, the tray ended at once, under it."""
        def on_signal():
            self.quit_app(None)
            return True     # kept: a second one would end the tray mid-quit
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, on_signal)

    def uninstall(self, _=None):
        """Preferences' Uninstall TuxTalk... and tuxtalk --uninstall: ask,
        remove everything (uninstall.py), then quit."""
        # A second request while the question is up would open another one
        # inside the first's dialog loop, and neither could close properly.
        if self._uninstalling or self._quitting:
            logger.info("Uninstall already asked, or quitting; ignoring the request")
            return False
        self._uninstalling = True
        from . import uninstall
        try:
            done = uninstall.run(uninstall.TrayParts(
                stop=self._stop_taking_actions, resume=self._resume_after_uninstall,
                shut_down=self._shut_down_for_uninstall))
        finally:
            self._uninstalling = False
        if done:
            self._leave_main_loop()
        return False  # one-shot when scheduled through GLib

    def _hide_for_quit(self):
        """Quit's first half: _stop_taking_actions, and close Preferences. The
        service is asked to quit by the wait for it (end_in_background, off
        GTK's thread: it hands the desktop its hotkeys back, which can take
        seconds on KDE)."""
        self._stop_taking_actions()
        self._close_preferences()

    def _stop_taking_actions(self):
        """Quit or uninstall begins: refuse every new action (D-Bus included:
        TuxTalk opened again waits for this one to go), drop pending launches
        and a waiting --start, hide the icon and the menu window."""
        self._quitting = True
        self._drop_pending_launches()
        self._waiting_start.take()
        self.indicator.set_status(AppIndicator3.IndicatorStatus.PASSIVE)
        if self._menu_window is not None:
            self._menu_window.window.destroy()

    def _close_preferences(self):
        """Close Preferences. Not waited for: it has no SIGTERM handler and
        ends at once."""
        if self.preferences_process and self.preferences_process.poll() is None:
            self.preferences_process.terminate()
            logger.info("Closed preferences window")

    def _resume_after_uninstall(self):
        """An uninstall that didn't happen: the tray takes actions again."""
        self._quitting = False
        if self._should_show_tray:
            self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

    def _shut_down_for_uninstall(self):
        """Uninstall goes ahead: Preferences closes (kept open until then, so
        a cancelled password prompt loses no edits), then _shut_down."""
        self._close_preferences()
        self._shut_down()

    def _shut_down(self):
        """What the service needed until it went: the KWin focus script and
        the D-Bus service (the window with the focus, the GNOME extension)."""
        # Leaving the script loaded means it keeps calling a D-Bus name that
        # no longer exists.
        self._stop_kwin_focus_provider()
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
        # Not while the first-run copy into ~/AppImages runs: cut off, the
        # launchers would point at a file that never arrived.
        from . import appimage_copy
        GLib.timeout_add(100, lambda: appimage_copy.copying() or os._exit(0))
