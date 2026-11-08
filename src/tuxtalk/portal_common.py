"""Minimal GDBus helper for the xdg-desktop-portal Request/Response pattern.

The input (GlobalShortcuts) and output (RemoteDesktop) backends, the
screenshot command (Screenshot) and start at login (Background) use this to
talk to the portal over the session bus.
"""
import itertools
import logging

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

logger = logging.getLogger(__name__)

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJ_PATH = "/org/freedesktop/portal/desktop"
# next() on a count is atomic, so two threads can never get the same token
# (and the same Request path, which would cross their responses).
_tokens = itertools.count(1)


def new_request_token() -> str:
    """A handle token no other request from this process has used."""
    return f"talktype_{next(_tokens)}"


def session_bus() -> Gio.DBusConnection:
    """The session bus connection the portal listens on."""
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _sender_prefix(bus) -> str:
    # unique name ":1.234" -> "1_234" per the portal Request path convention
    return bus.get_unique_name().lstrip(":").replace(".", "_")


def request_path(bus, token: str) -> str:
    """The object path of the portal's Request made with *token* on *bus*."""
    return f"/org/freedesktop/portal/desktop/request/{_sender_prefix(bus)}/{token}"


def close_request(bus, path: str):
    """Ask the portal to close its Request at *path* (a dialog it still shows);
    an error is logged: the request may already be gone."""
    try:
        bus.call_sync(BUS_NAME, path, "org.freedesktop.portal.Request", "Close", None, None,
                      Gio.DBusCallFlags.NONE, 2000, None)
    except Exception as e:
        logger.info("Portal request couldn't be closed: %s", e)


def call_portal(bus, iface, method, param_builder, on_response, on_error=None,
                timeout_ms=-1, token=None):
    """Issue a portal request and invoke on_response(code:int, results:dict) when
    its Response signal arrives. param_builder(handle_token) returns the method's
    IN args as a GLib.Variant, embedding handle_token in the options a{sv}.
    *timeout_ms* bounds the method call itself (-1: GDBus's 25 s). *token*,
    from new_request_token(), lets the caller name the Request (to close it);
    by default a new one is taken.

    An exception inside on_response goes to on_error(exc). PyGObject would
    otherwise print and swallow it, and a caller waiting on a main loop for
    the response would wait forever.

    Returns a function that drops the Response subscription; it is dropped by
    itself once the response arrives, and calling it again does nothing.
    This keeps finished requests from piling up subscriptions on the shared bus.
    """
    token = token or new_request_token()
    path = request_path(bus, token)
    sub_id = None

    def unsubscribe():
        nonlocal sub_id
        if sub_id is not None:
            bus.signal_unsubscribe(sub_id)
            sub_id = None

    def _on_signal(conn, sender, path, iface_, signal, params):
        unsubscribe()
        try:
            code, results = params.unpack()
            on_response(code, results)
        except Exception as e:
            logger.error("Portal %s.%s response could not be handled: %s", iface, method, e)
            if on_error is not None:
                on_error(e)

    sub_id = bus.signal_subscribe(BUS_NAME, "org.freedesktop.portal.Request", "Response",
                                  path, None, Gio.DBusSignalFlags.NONE, _on_signal)
    try:
        bus.call_sync(BUS_NAME, OBJ_PATH, iface, method, param_builder(token),
                      None, Gio.DBusCallFlags.NONE, timeout_ms, None)
    except BaseException:
        unsubscribe()
        raise
    return unsubscribe
