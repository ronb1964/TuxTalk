"""The dictation service's side of its D-Bus link to the tray: telling it
when recording starts and stops and when a hotkey is pressed, and asking it
to show the voice commands. The tray re-sends these to the GNOME extension.
Every call has a short time limit: a busy or missing tray must never hold
up a dictation."""
from .logger import setup_logger
from . import focused_window

logger = setup_logger(__name__)

# D-Bus proxy for notifying the tray process of recording state changes.
# The tray owns the D-Bus name and relays signals to the GNOME extension.
_tray_dbus_proxy = None


def notify_recording_state(is_recording: bool):
    """Send recording state to the tray's D-Bus service.

    The tray process owns the D-Bus name that the GNOME extension listens to.
    The service cannot emit signals that the extension will see, so it
    calls the tray's NotifyRecordingState method, which emits the signal.

    Called from start_recording/stop_recording — that is, with every keyboard
    exclusively grabbed. It must never block for long: see focused_window.TRAY_TIMEOUT_S.
    """
    try:
        import dbus
        proxy = get_proxy()
        proxy.NotifyRecordingState(
            dbus.Boolean(is_recording),
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=focused_window.TRAY_TIMEOUT_S,
        )
    except Exception as e:
        logger.info(f"Could not notify tray of recording state: {e}")


def get_proxy():
    """Get or create the D-Bus proxy for communicating with the tray process.

    introspect=False: Introspect() is itself a blocking call with the 25-second
    reply timeout, so it would double the worst-case stall on a busy tray.
    Without it dbus-python guesses argument types from the Python values, so
    wrap anything that isn't a plain str or bool (dbus.Array(..., signature=...)).
    """
    global _tray_dbus_proxy
    import dbus
    if _tray_dbus_proxy is None:
        import dbus.mainloop.glib
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        _tray_dbus_proxy = bus.get_object(
            'io.github.ronb1964.TalkType',
            '/io/github/ronb1964/TalkType',
            introspect=False,
        )
    return _tray_dbus_proxy


def notify_hotkey_pressed(key_name: str):
    """Send hotkey press notification to tray D-Bus service during test mode.

    Called from the evdev loop while hotkey test mode is on. The tray's D-Bus
    service emits a HotkeyPressed signal that the prefs dialog listens for.
    """
    try:
        proxy = get_proxy()
        proxy.NotifyHotkeyPressed(
            key_name,
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=focused_window.TRAY_TIMEOUT_S,
        )
    except Exception as e:
        logger.info(f"Could not notify tray of hotkey press: {e}")


def show_voice_commands():
    """Tell the tray to show the voice commands quick reference via D-Bus."""
    try:
        proxy = get_proxy()
        proxy.ShowVoiceCommands(
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=focused_window.TRAY_TIMEOUT_S,
        )
    except Exception as e:
        logger.info(f"Could not show voice commands via D-Bus: {e}")
