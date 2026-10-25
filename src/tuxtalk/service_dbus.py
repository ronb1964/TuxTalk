"""The object the dictation service offers on D-Bus when no tray is
running (dbus_service.TuxTalkDBusService, primary=False): the GNOME
extension's menu can still start and stop recording, open Preferences,
change the model and quit. With the tray running, the tray answers
instead and this is never reached."""
import subprocess
import sys

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
        self.dbus_service = None
        self._start_event = start_event
        self._stop_event = stop_event
        self._control = control

    @property
    def is_recording(self):
        """Live recording state from the dictation engine."""
        return rs.state.is_recording

    def show_preferences(self):
        """Open preferences window"""
        subprocess.Popen([sys.executable, "-m", "tuxtalk.prefs"])

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
        """Stop the dictation service"""
        from .service_launcher import stop_dictation_service
        try:
            stop_dictation_service()
            logger.info("Stopped dictation service")
            self.service_running = False
            if self.dbus_service:
                self.dbus_service.emit_service_state(False)
        except Exception as e:
            logger.error(f"Failed to stop service: {e}", exc_info=True)

    def set_model(self, model_name: str):
        """Change the speech model (it applies after a service restart),
        through config.save_config, the same path every other writer uses."""
        try:
            from .config import VALID_MODELS, load_config, save_config
            if model_name not in VALID_MODELS:
                logger.error(f"SetModel: refusing unknown model {model_name!r}")
                return
            cfg = load_config()
            cfg.model = model_name
            save_config(cfg)
            self.config.model = model_name
            logger.info(f"Model changed to {model_name} (restart required)")
            if self.dbus_service:
                self.dbus_service.emit_model_changed(model_name)   # so the extension updates
        except Exception as e:
            logger.error(f"Failed to set model: {e}", exc_info=True)

    def quit(self):
        """Quit the application"""
        # stop_dictation_service stops only the service; a pkill by name
        # would also match the tray and Preferences.
        from .service_launcher import stop_dictation_service
        try:
            stop_dictation_service()
        except Exception as e:
            logger.warning(f"Could not stop the dictation service cleanly: {e}")
        sys.exit(0)
