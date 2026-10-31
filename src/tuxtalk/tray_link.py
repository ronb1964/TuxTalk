"""The dictation service's side of its D-Bus link to the tray: telling it
when recording starts and stops and when a hotkey is pressed, and asking it
to show the voice commands. The tray re-sends these to the GNOME extension.
Every call has a short time limit: a busy or missing tray must never hold
up a dictation."""
from . import focused_window
from .logger import setup_logger

logger = setup_logger(__name__)

# D-Bus proxy for notifying the tray process of recording state changes.
# The tray owns the D-Bus name and relays signals to the GNOME extension.
_tray_dbus_proxy = None


def call(method: str, *args, what: str, signature: str | None = None) -> None:
    """Call the tray's D-Bus *method* with *args*. A failure, dbus itself
    missing included, is logged as "Could not {what}" and never raised.
    Every call has the short time limit focused_window.TRAY_TIMEOUT_S: some
    are made with every keyboard exclusively grabbed. Without introspection
    (get_proxy), arguments that aren't plain strings or booleans need their
    D-Bus *signature* ("u" for a process id, "as" for a list of strings)."""
    try:
        from .dbus_service import DBUS_INTERFACE
        getattr(get_proxy(), method)(*args, dbus_interface=DBUS_INTERFACE, signature=signature,
                                     timeout=focused_window.TRAY_TIMEOUT_S)
    except Exception as e:
        logger.info(f"Could not {what}: {e}")


def notify_recording_state(is_recording: bool):
    """Tell the tray recording started or stopped. The tray owns the D-Bus
    name the GNOME extension listens to, and emits the signal for us."""
    call("NotifyRecordingState", is_recording, what="notify tray of recording state")


def get_proxy():
    """Get or create the D-Bus proxy for communicating with the tray process.

    introspect=False: Introspect() is itself a blocking call with the 25-second
    reply timeout, so it would double the worst-case stall on a busy tray.
    Without it dbus-python guesses argument types from the Python values, so
    call() is given the signature for anything that isn't a plain str or bool.

    follow_name_owner_changes: the calls go to whoever holds the name now.
    Bound to the first holder instead, a service that outlived a crashed
    tray kept calling the dead one after TuxTalk was opened again, and the
    GNOME panel, the hotkey test and tuxtalk --start heard nothing from it.
    """
    global _tray_dbus_proxy
    import dbus
    if _tray_dbus_proxy is None:
        import dbus.mainloop.glib
        from .dbus_service import DBUS_NAME, DBUS_PATH
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        _tray_dbus_proxy = bus.get_object(DBUS_NAME, DBUS_PATH, introspect=False,
                                          follow_name_owner_changes=True)
    return _tray_dbus_proxy


def notify_hotkey_pressed(key_name: str):
    """Tell the tray which dictation key was pressed during the hotkey test;
    its D-Bus service emits the HotkeyPressed signal Preferences listens for."""
    call("NotifyHotkeyPressed", key_name, what="notify tray of hotkey press")


def show_voice_commands():
    """Tell the tray to show the voice commands quick reference."""
    call("ShowVoiceCommands", what="show voice commands via D-Bus")
