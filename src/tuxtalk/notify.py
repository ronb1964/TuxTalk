"""Desktop notifications, shared by the tray and the service.

Sent straight to the desktop's notification service over D-Bus
(org.freedesktop.Notifications), not through libnotify: the AppImage's
Ubuntu-built GObject Introspection can't find a Fedora system's Notify
typelib, so every notice from the AppImage silently vanished there, the
silent-microphone warning included. dbus-python is
bundled already and works the same in every package.

This module has no heavy imports on purpose: the tray shows notices too, and
importing app.py for them would load the whole dictation engine (numpy,
faster_whisper, torch) into the tray for the rest of the session.
"""

from .logger import setup_logger

logger = setup_logger(__name__)

APP_ICON = "io.github.ronb1964.TuxTalk"
# A notice is never worth stalling the hotkey loop for.
TIMEOUT_S = 2.0


def _send(title: str, body: str) -> None:
    # introspect=False: the proxy's Introspect() is a call of its own, with
    # D-Bus's 25-second default, and a hung notification service held the
    # hotkey loop that long. Every argument below carries its D-Bus type.
    import dbus
    proxy = dbus.SessionBus().get_object("org.freedesktop.Notifications",
                                         "/org/freedesktop/Notifications",
                                         introspect=False)
    proxy.Notify("TuxTalk", dbus.UInt32(0), APP_ICON, title, body,
                 dbus.Array([], signature="s"), dbus.Dictionary({}, signature="sv"),
                 dbus.Int32(-1), dbus_interface="org.freedesktop.Notifications",
                 timeout=TIMEOUT_S)


def notify(title: str, body: str) -> None:
    """Show a desktop notification; skipped quietly when the desktop has no
    notification service."""
    try:
        _send(title, body)
    except Exception as e:
        logger.info(f"Notification not shown ({title}): {e}")
