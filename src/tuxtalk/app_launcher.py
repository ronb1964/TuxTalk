"""Open apps by voice (TuxTalk Labs): starting the app the way the menu would.

Gio's launcher handles what a plain subprocess would miss: field codes in the
Exec line, Terminal=true and D-Bus-activatable apps. The app gets the
environment the user's desktop would have given it (launch_env), and then its
own systemd scope, as GNOME Shell and KDE do, so it neither dies with TuxTalk's
service nor counts as part of it.
"""
import logging
import os
import re

from gi.repository import Gio, GLib

from . import launch_env
from .app_finder import AppEntry
from .kwin_focus import DBUS_TIMEOUT_S

log = logging.getLogger(__name__)

_SYSTEMD = "org.freedesktop.systemd1"
_SYSTEMD_PATH = "/org/freedesktop/systemd1"
_SYSTEMD_MANAGER = "org.freedesktop.systemd1.Manager"
# StartTransientUnit(name, mode, properties a(sv), aux a(sa(sv)))
_START_UNIT_SIGNATURE = "ssa(sv)a(sa(sv))"
# Unit names allow only these characters; the rest are written as \xNN.
_NOT_ALLOWED_IN_UNIT_NAME = re.compile(r"[^A-Za-z0-9_.:]")


def _app_info(app_id: str):
    """The menu entry for a .desktop id, or None when it is gone."""
    return Gio.DesktopAppInfo.new(app_id)


class _Systemd:
    """The user's systemd, reached over the session bus."""

    def __init__(self, bus):
        self._bus = bus

    def start_scope(self, name: str, pids: list) -> None:
        """Move already running processes into a new scope under app.slice."""
        import dbus
        properties = [("PIDs", dbus.Array([dbus.UInt32(p) for p in pids], signature="u")),
                      ("Slice", "app.slice")]
        self._bus.call_blocking(_SYSTEMD, _SYSTEMD_PATH, _SYSTEMD_MANAGER, "StartTransientUnit",
                                _START_UNIT_SIGNATURE, (name, "fail", properties, []),
                                timeout=DBUS_TIMEOUT_S)


def _systemd():
    """The user's systemd, or None on a desktop without it."""
    import dbus
    bus = dbus.SessionBus()
    if not bus.name_has_owner(_SYSTEMD):
        return None
    return _Systemd(bus)


def _unit_name(pid: int, app_id: str) -> str:
    """app-tuxtalk-<escaped app id>-<pid>.scope, the pid keeping two copies apart."""
    escaped = _NOT_ALLOWED_IN_UNIT_NAME.sub(lambda m: "\\x%02x" % ord(m.group()), app_id)
    return f"app-tuxtalk-{escaped}-{pid}.scope"


def own_scope(pid: int, app_id: str) -> None:
    """Put a started app into a systemd scope of its own.

    Without systemd, or when it refuses, the app simply stays where it is: it
    is running either way, so this only logs.
    """
    try:
        systemd = _systemd()
        if systemd is None:
            log.info("No systemd on this desktop; %s stays in TuxTalk's group", app_id)
            return
        systemd.start_scope(_unit_name(pid, app_id), [pid])
    except Exception as exc:  # D-Bus errors come in many classes; the app is running anyway
        log.info("Couldn't give %s its own scope: %s", app_id, exc)


def _launch_context() -> Gio.AppLaunchContext:
    """A launch context whose environment is launch_env.clean of this process's."""
    context = Gio.AppLaunchContext()
    current = dict(os.environ)
    cleaned = launch_env.clean(current)
    for name in current.keys() - cleaned.keys():
        context.unsetenv(name)
    for name, value in cleaned.items():
        if current.get(name) != value:
            context.setenv(name, value)
    return context


def launch(entry: AppEntry) -> bool:
    """Start the app of a menu entry. True when Gio reported it started."""
    info = _app_info(entry.app_id)
    if info is None:
        log.info("Couldn't open %s: it is no longer in the menu", entry.app_id)
        return False

    def got_pid(_info, pid, _data):
        own_scope(pid, entry.app_id)

    try:
        info.launch_uris_as_manager([], _launch_context(), GLib.SpawnFlags.SEARCH_PATH,
                                    None, None, got_pid, None)
    except GLib.Error as exc:
        log.info("Couldn't open %s: %s", entry.app_id, exc.message)
        return False
    log.info("Opened %s", entry.app_id)
    return True
