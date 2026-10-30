"""Hotkey/input backends.

TuxTalk starts and stops recording from a global hotkey. Outside a Flatpak it
reads the key directly with evdev (Backend A); inside a Flatpak that is blocked,
so it registers a shortcut with the GlobalShortcuts portal and reacts to the
desktop's press/release signals (Backend B). Both backends drive the SAME
thread-safe events (recording.start_requested / recording.stop_requested), so the
recording core is identical either way. Chosen once at startup by FLATPAK_ID.

Outside a Flatpak, when TuxTalk's hotkey helper is installed (hotkey_helper.py,
set up by secure_input.py), Backend A reads the keys from it instead of
opening the keyboards, so the user needn't be in the "input" group.
"""
import os


class InputBackend:
    """Listens for the dictate hotkey and drives recording start/stop.
    start() blocks (it runs the service's input loop); stop() is best-effort."""

    def start(self):
        raise NotImplementedError

    def stop(self):
        pass


class EvdevInputBackend(InputBackend):
    """Backend A: hotkeys.run, reading the keyboard directly."""

    def __init__(self, cfg, input_device_idx, helper=None):
        self.cfg = cfg
        self.input_device_idx = input_device_idx
        self.helper = helper        # hotkey_helper_client.HelperKeyboard, or None

    def start(self):
        from . import hotkeys
        if self.helper is not None:
            hotkeys.run(self.cfg, self.input_device_idx, keyboards=[self.helper])
        else:
            hotkeys.run(self.cfg, self.input_device_idx)


class PortalInputBackend(InputBackend):
    """Backend B: the GlobalShortcuts portal (Flatpak).

    Registers the hold and toggle shortcuts; the desktop assigns their keys and
    sends Activated (press) / Deactivated (release) signals, which we translate into
    the same recording.start_requested / recording.stop_requested events the evdev
    loop sets. A GLib timeout runs the shared service_tick.tick so the recording
    core and auto-timeout work identically."""

    IFACE = "org.freedesktop.portal.GlobalShortcuts"

    def __init__(self, cfg, input_device_idx=None):
        self.cfg = cfg
        self.input_device_idx = input_device_idx

    def _shortcut_id(self, params):
        """The shortcut id an Activated/Deactivated signal carries: the
        portal's signature is (session, shortcut id, timestamp, options)."""
        return params.unpack()[1]

    def _on_activated(self, conn, sender, path, iface, signal, params):
        # Two shortcuts are always live, mirroring Backend A where the hold key
        # (F8) and the toggle key (F9) both work at once — not a single mode-gated
        # key. "dictate_hold" = press-and-hold; "dictate_toggle" = tap on/off.
        sid = self._shortcut_id(params)
        from . import recording, recording_state
        if sid == "dictate_hold":
            recording.start_requested.set()
        elif sid == "dictate_toggle":
            # Ask the recording itself rather than keeping a private on/off
            # flag: a recording can end without this key (auto-stop, Esc), and
            # a stale flag would make the next press do nothing.
            if not recording_state.state.is_recording:
                recording.started_from_toggle_key.set()
                recording.start_requested.set()
            elif not recording.toggle_pressed_while_recording(getattr(self.cfg, "beeps", True)):
                recording.stop_requested.set()

    def _on_deactivated(self, conn, sender, path, iface, signal, params):
        # Only the hold shortcut stops on release; toggle ignores key release.
        if self._shortcut_id(params) == "dictate_hold":
            from . import recording
            recording.stop_requested.set()

    def _tick(self):
        from . import service_tick
        service_tick.tick(self.cfg, self._svc)
        return True  # keep the GLib timer running

    def start(self):
        import logging
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        from . import portal_common as pc, service_tick
        logger = logging.getLogger(__name__)

        bus = pc.session_bus()
        loop = GLib.MainLoop()
        state = {"session": None}

        def create_session():
            def builder(token):
                opts = {"handle_token": GLib.Variant("s", token),
                        "session_handle_token": GLib.Variant("s", token + "_sess")}
                return GLib.Variant("(a{sv})", (opts,))
            pc.call_portal(bus, self.IFACE, "CreateSession", builder, on_session)

        def on_session(code, results):
            if code:
                logger.error("GlobalShortcuts CreateSession failed: %s", code)
                loop.quit()
                return
            state["session"] = results["session_handle"]
            bind()

        def bind():
            # Register BOTH keys, always live (Backend A parity): hold-to-talk and
            # tap-to-toggle. The desktop lets the user assign a key to each.
            # Descriptions are short: desktops group these under the app name
            # ("TuxTalk"), so a "TuxTalk:" prefix would read redundantly.
            shortcuts = [
                ("dictate_hold",
                 {"description": GLib.Variant("s", "Hold to talk (push-to-talk)")}),
                ("dictate_toggle",
                 {"description": GLib.Variant("s", "Toggle dictation (tap on / tap off)")}),
            ]

            def builder(token):
                opts = {"handle_token": GLib.Variant("s", token)}
                return GLib.Variant("(oa(sa{sv})sa{sv})",
                                    (state["session"], shortcuts, "", opts))
            pc.call_portal(bus, self.IFACE, "BindShortcuts", builder, on_bound)

        def on_bound(code, results):
            if code:
                logger.warning("BindShortcuts failed/cancelled (%s); the shortcut "
                               "may still be assignable in desktop settings", code)

        # sender=None: portal signals are proxied inside the sandbox.
        bus.signal_subscribe(None, self.IFACE, "Activated", None, None,
                             Gio.DBusSignalFlags.NONE, self._on_activated)
        bus.signal_subscribe(None, self.IFACE, "Deactivated", None, None,
                             Gio.DBusSignalFlags.NONE, self._on_deactivated)

        self._svc = service_tick.ServiceState(self.cfg, self.input_device_idx)
        GLib.timeout_add(50, self._tick)
        create_session()
        loop.run()


def get_input_backend(flatpak_id=None, cfg=None, input_device_idx=None) -> InputBackend:
    """Pick the input backend. flatpak_id=None reads the real environment."""
    if flatpak_id is None:
        flatpak_id = os.environ.get("FLATPAK_ID", "")
    if flatpak_id:
        return PortalInputBackend(cfg, input_device_idx)
    from .hotkey_helper_client import connect_helper
    helper = connect_helper()
    return EvdevInputBackend(cfg, input_device_idx, helper=helper)
