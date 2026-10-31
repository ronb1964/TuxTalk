"""The object the dictation service offers on D-Bus when no tray is
running (dbus_service.TuxTalkDBusService, primary=False): the GNOME
extension's menu can still start and stop recording, open Preferences
and quit. With the tray running, the tray answers
instead and this is never reached."""
import os
import signal

from . import recording_state as rs
from .logger import setup_logger

logger = setup_logger(__name__)


class AppInstance:
    """What dbus_service.TuxTalkDBusService calls on the service. Recording
    starts and stops through *start_event* and *stop_event*, which the input
    loop waits on, so D-Bus's thread never touches the recording itself;
    *control* answers tuxtalk --toggle/--start/--stop."""

    def __init__(self, cfg, start_event, stop_event, control):
        self.config = cfg
        self.service_running = True
        self._start_event = start_event
        self._stop_event = stop_event
        self._control = control

    @property
    def is_recording(self):
        """Live recording state from the dictation engine."""
        return rs.state.is_recording

    def show_preferences(self):
        """Open Preferences, as the tray does (service_launcher)."""
        from .service_launcher import launch_preferences
        launch_preferences()

    def start_recording(self):
        """Signal the input loop to start recording."""
        if not rs.state.is_recording:
            self._start_event.set()

    def stop_recording(self):
        """Signal the input loop to stop recording."""
        if rs.state.is_recording:
            self._stop_event.set()

    def toggle_recording(self):
        """Start recording, or stop the one under way."""
        if rs.state.is_recording:
            self._stop_event.set()
        else:
            self._start_event.set()

    def control(self, action):
        """tuxtalk --toggle/--start/--stop with no tray running."""
        return self._control(action)

    def stop_service(self):
        """Dictation off, with no tray: this service is the one to stop, the
        same way as Quit (the service-wide stop skips its own process)."""
        self.quit()

    def quit(self):
        """Quit with no tray: this service quits as a SIGTERM asks it to
        (app._handle_sigterm), typing what it holds first. A plain exit here,
        on GTK's thread, would end only this callback."""
        logger.info("Quitting (asked over D-Bus with no tray running)")
        os.kill(os.getpid(), signal.SIGTERM)
