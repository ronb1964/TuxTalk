"""Text-injection backends.

TuxTalk needs to type transcribed text into whatever app is focused. Outside a
Flatpak it uses its own virtual keyboard (Backend A, virtual_keyboard.py);
inside a Flatpak /dev/uinput is out of reach, so it types through the
RemoteDesktop portal + libei (Backend B). The backend is
chosen once at startup by FLATPAK_ID and hidden behind a single method so the
recording core never knows which one is in use.
"""
import os


class OutputBackend:
    """Types text into the focused application. Returns True if the text was
    actually delivered, False if injection failed (the caller surfaces a
    plain-language notice on False). The host's virtual keyboard raises
    virtual_keyboard.StoppedPartway when it failed partway, for typing as for
    backspaces: then nothing may deliver the text again.

    Beyond plain text, editing voice commands need to press key combos —
    'delete everything' (Ctrl+A + Backspace) and character-undo (Backspace ×N).
    Those go through the backend too, so the Flatpak (libei) and host
    (virtual keyboard) paths stay in lockstep instead of the commands
    hardcoding one and silently doing nothing inside the sandbox."""

    def type_text(self, text: str) -> bool:
        raise NotImplementedError

    def typeable(self, text: str) -> str:
        """*text* as type_text will actually type it. The caller types and
        records this form, so undo deletes exactly what was typed."""
        return text

    def select_all_delete(self) -> bool:
        """Clear the focused field (Ctrl+A + Backspace)."""
        raise NotImplementedError

    def backspaces(self, count: int) -> bool:
        """Send `count` Backspace presses (character-level undo)."""
        raise NotImplementedError

    def press_keys(self, chords, claim=None) -> bool:
        """Press [(modifiers, key)] in order, by name ("CTRL", "LEFT"), for
        select and move by voice (voice_navigation.py). *claim*, if given,
        is virtual_keyboard.send's: made right before the first key, it may
        stop them all by raising virtual_keyboard.GaveUp
        (send_countdown._claim_send)."""
        raise NotImplementedError


class VirtualKeyboardOutputBackend(OutputBackend):
    """Backend A: app.py's typing on TuxTalk's own virtual keyboard."""

    def type_text(self, text: str) -> bool:
        from . import app
        return app._type_text(text)

    def select_all_delete(self) -> bool:
        from . import app
        return app._send_select_all_delete()

    def backspaces(self, count: int) -> bool:
        from . import app
        return app._send_backspaces(count)

    def press_keys(self, chords, claim=None) -> bool:
        from . import app
        return app._send_chords(chords, claim)


class LibeiOutputBackend(OutputBackend):
    """Backend B: type via the RemoteDesktop portal + libei (Flatpak).

    Opens a fresh RemoteDesktop/EIS session for each typing action (the user
    approves the "Allow remote control?" dialog once; a restore token
    remembers it) and sends the keystrokes over it. type_text never raises — it returns False so the
    recording core surfaces its usual "couldn't type" notice."""

    IFACE = "org.freedesktop.portal.RemoteDesktop"
    KEYBOARD = 1  # RemoteDesktop DeviceType bitmask: KEYBOARD=1, POINTER=2
    # How long a handshake may take before typing gives up. The first one
    # (no restore token yet) waits for the user to answer the approval dialog.
    FIRST_HANDSHAKE_TIMEOUT_S = 120
    HANDSHAKE_TIMEOUT_S = 30

    def _token_path(self) -> str:
        import os
        from .config import get_data_dir
        return os.path.join(get_data_dir(), "remote_desktop_token")

    def _load_token(self) -> str:
        try:
            with open(self._token_path()) as f:
                return f.read().strip()
        except OSError:
            return ""

    def _save_token(self, token: str) -> None:
        import logging
        import os
        path = self._token_path()
        try:
            # On a fresh CPU-only Flatpak install nothing else may have created
            # the data folder yet, and without a saved restore token the desktop
            # asks "Allow remote interaction?" on every dictation.
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(token or "")
        except OSError as e:
            logging.getLogger(__name__).debug("could not save restore token: %s", e)

    def _close_session(self, session_path):
        """Close a RemoteDesktop portal session (one is created per dictation)."""
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        from . import portal_common as pc
        try:
            pc.session_bus().call_sync(
                pc.BUS_NAME, session_path, "org.freedesktop.portal.Session",
                "Close", None, None, Gio.DBusCallFlags.NONE, 2000, None)
        except Exception:
            pass

    def _run_handshake(self):
        """Drive CreateSession -> SelectDevices -> Start -> ConnectToEIS on a
        GLib loop; returns (EIS fd or None, session handle path). Uses a saved
        restore_token to skip the approval dialog after the first time."""
        import gi
        import logging
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        from . import portal_common as pc

        logger = logging.getLogger(__name__)
        # Own GMainContext: the handshake runs on a worker thread, and two
        # threads may not iterate the same (default) context. The portal's
        # async callbacks are then delivered on this private one.
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        bus = pc.session_bus()
        loop = GLib.MainLoop.new(ctx, False)
        st = {"session": None, "fd": None}
        unsubscribes = []

        def fail(code):
            # 1: the user cancelled, 2: the portal refused, -1: no answer in
            # time, -2: an error while handling an answer.
            logger.warning("RemoteDesktop handshake stopped (code %s)", code)
            st["fd"] = None
            loop.quit()

        def call(method, builder, on_response):
            # Every step runs inside the previous step's response; an error
            # there must end the loop, or the dictation would wait forever.
            unsubscribes.append(pc.call_portal(
                bus, self.IFACE, method, builder, on_response,
                on_error=lambda e: fail(-2)))

        def create_session():
            def b(token):
                opts = {"handle_token": GLib.Variant("s", token),
                        "session_handle_token": GLib.Variant("s", token + "_sess")}
                return GLib.Variant("(a{sv})", (opts,))
            call("CreateSession", b, on_session)

        def on_session(code, results):
            if code:
                return fail(code)
            st["session"] = results["session_handle"]
            select_devices()

        def select_devices():
            def b(token):
                opts = {"handle_token": GLib.Variant("s", token),
                        "types": GLib.Variant("u", self.KEYBOARD),
                        "persist_mode": GLib.Variant("u", 2)}  # until revoked
                saved = self._load_token()
                if saved:
                    opts["restore_token"] = GLib.Variant("s", saved)
                return GLib.Variant("(oa{sv})", (st["session"], opts))
            call("SelectDevices", b, lambda c, r: start() if not c else fail(c))

        def start():
            def b(token):
                opts = {"handle_token": GLib.Variant("s", token)}
                return GLib.Variant("(osa{sv})", (st["session"], "", opts))
            call("Start", b, on_start)

        def on_start(code, results):
            if code:
                return fail(code)
            token = results.get("restore_token")
            if token:
                self._save_token(token)
                logger.info("RemoteDesktop session persisted (restore token saved)")
            else:
                # No token means the desktop won't restore this grant, so the
                # approval dialog reappears next dictation. Surface it plainly so
                # a re-prompt loop is diagnosable from the log alone.
                logger.warning("RemoteDesktop Start returned no restore_token; "
                               "the desktop will re-prompt on the next dictation")
            connect_eis()

        def connect_eis():
            opts = GLib.Variant("(oa{sv})", (st["session"], {}))
            ret, fd_list = bus.call_with_unix_fd_list_sync(
                pc.BUS_NAME, pc.OBJ_PATH, self.IFACE, "ConnectToEIS", opts,
                GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, 10000, None, None)
            st["fd"] = fd_list.get(ret.unpack()[0])
            loop.quit()

        # Attached to this private context: GLib.timeout_add would attach to
        # the default one, which this loop never runs.
        seconds = (self.HANDSHAKE_TIMEOUT_S if self._load_token()
                   else self.FIRST_HANDSHAKE_TIMEOUT_S)
        timeout = GLib.timeout_source_new_seconds(seconds)
        timeout.set_callback(lambda *_: (fail(-1), False)[1])
        timeout.attach(ctx)
        try:
            create_session()
            loop.run()
            # On a failure the half-open session is still returned, so the
            # caller closes it.
            return st["fd"], st["session"]
        finally:
            timeout.destroy()
            for unsubscribe in unsubscribes:
                unsubscribe()
            ctx.pop_thread_default()

    def _with_session(self, action) -> bool:
        """Run `action(session)` inside a FRESH RemoteDesktop/EIS session. A
        reused session types only once, because KDE's EIS delivers keystrokes
        on a fresh DEVICE_RESUMED. The saved restore_token means the approval
        dialog appears only the first time. Returns action's bool result,
        or False if the handshake/device wasn't ready. Never raises."""
        import logging
        import os
        logger = logging.getLogger(__name__)
        fd = None
        session_path = None
        try:
            fd, session_path = self._run_handshake()
            if fd is None:
                logger.warning("RemoteDesktop portal did not yield an EIS fd "
                               "(permission denied?)")
                return False
            from . import libei_ctypes
            # libei gets its own copy of the socket and closes it with the
            # session; this function keeps closing the portal's.
            with libei_ctypes.LibeiSession(os.dup(fd)) as session:
                if not session.pump_until_ready():
                    logger.warning("libei device did not become ready")
                    return False
                return bool(action(session))
        except Exception as e:
            logger.error("libei session action failed: %s", e)
            return False
        finally:
            if session_path:
                self._close_session(session_path)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def type_text(self, text: str) -> bool:
        return self._with_session(lambda s: s.type_string(text))

    def typeable(self, text: str) -> str:
        from . import libei_ctypes
        return libei_ctypes.typeable_text(text)

    def select_all_delete(self) -> bool:
        return self._with_session(lambda s: s.select_all_delete())

    def backspaces(self, count: int) -> bool:
        return self._with_session(lambda s: s.backspaces(count))

    def press_keys(self, chords, claim=None) -> bool:
        from .virtual_keyboard import GaveUp
        try:
            if claim is not None:
                claim()()
        except GaveUp:
            return False
        return self._with_session(lambda s: s.press_chords(chords))


def get_output_backend(flatpak_id=None) -> OutputBackend:
    """Pick the output backend. flatpak_id=None reads the real environment."""
    if flatpak_id is None:
        flatpak_id = os.environ.get("FLATPAK_ID", "")
    return LibeiOutputBackend() if flatpak_id else VirtualKeyboardOutputBackend()
