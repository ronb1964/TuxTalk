"""The virtual-keyboard backend must keep delegating to app._type_text so the non-Flatpak
typing path is behaviorally unchanged."""
import os


def _portal_fd():
    """A real fd standing in for the portal's EIS socket."""
    r, w = os.pipe()
    os.close(w)
    return r


class _FakeSessionBase:
    """A LibeiSession stand-in: it owns the fd it is given, like the real one."""

    def __init__(self, fd):
        self.fd = fd

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        return False


def test_virtual_keyboard_backend_delegates_to_type_text(monkeypatch):
    from tuxtalk import app
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    calls = []
    monkeypatch.setattr(app, "_type_text", lambda t: calls.append(t) or True)
    assert VirtualKeyboardOutputBackend().type_text("hello") is True
    assert calls == ["hello"]


def test_libei_backend_returns_false_when_handshake_fails(monkeypatch):
    # type_text must never raise — it returns False so the caller can notify.
    from tuxtalk.output_backends import LibeiOutputBackend
    b = LibeiOutputBackend()
    monkeypatch.setattr(b, "_run_handshake", lambda: (None, None))
    assert b.type_text("hi") is False


def test_libei_backend_types_via_fresh_session(monkeypatch):
    # A fresh RemoteDesktop/EIS session per call: handshake -> LibeiSession ->
    # pump_until_ready -> type_string, then the session is closed.
    import tuxtalk.libei_ctypes as libei_ctypes
    from tuxtalk.output_backends import LibeiOutputBackend
    b = LibeiOutputBackend()
    typed = []
    closed = []

    class FakeSession(_FakeSessionBase):
        def __init__(self, fd):
            super().__init__(fd)
            self.fd = fd

        def pump_until_ready(self):
            return True

        def type_string(self, t):
            typed.append(t)
            return True

    monkeypatch.setattr(b, "_run_handshake", lambda: (_portal_fd(), "/org/session/1"))
    monkeypatch.setattr(b, "_close_session", lambda p: closed.append(p))
    monkeypatch.setattr(libei_ctypes, "LibeiSession", FakeSession)

    assert b.type_text("hello") is True
    assert typed == ["hello"]
    assert closed == ["/org/session/1"]  # session cleaned up


def test_save_token_creates_missing_data_dir(tmp_path, monkeypatch):
    # On a fresh CPU-only Flatpak install the data dir (data/TuxTalk/) doesn't
    # exist yet — nothing has written CUDA libs or the first-run flag there. If
    # _save_token can't create it, the RemoteDesktop restore token never
    # persists and GNOME re-prompts "Allow remote interaction?" on every single
    # dictation. It must create its parent dir like every other data-dir writer.
    from tuxtalk.output_backends import LibeiOutputBackend
    b = LibeiOutputBackend()
    token_path = tmp_path / "TalkType" / "remote_desktop_token"  # parent missing
    monkeypatch.setattr(b, "_token_path", lambda: str(token_path))

    b._save_token("restore-token-abc")

    assert token_path.exists(), "token must be written even when data dir was absent"
    assert token_path.read_text() == "restore-token-abc"
    assert b._load_token() == "restore-token-abc"


def test_the_restore_token_is_readable_by_its_user_only(tmp_path, monkeypatch):
    """The token lets TuxTalk control the keyboard again without asking.
    Written with the usual umask it was readable by every user of the
    computer, and a token saved that way before stays so until replaced."""
    import os
    from tuxtalk.output_backends import LibeiOutputBackend
    b = LibeiOutputBackend()
    token_path = tmp_path / "remote_desktop_token"
    token_path.write_text("old-token")
    token_path.chmod(0o644)
    monkeypatch.setattr(b, "_token_path", lambda: str(token_path))
    old_umask = os.umask(0o022)
    try:
        b._save_token("restore-token-abc")
    finally:
        os.umask(old_umask)
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert b._load_token() == "restore-token-abc"


def test_output_backend_accessor_returns_the_virtual_keyboard_on_host(monkeypatch):
    # No FLATPAK_ID on the host -> the virtual-keyboard backend.
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    from tuxtalk import app
    app._OUTPUT_BACKEND = None  # reset the process cache for the test
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    assert isinstance(app._output_backend(), VirtualKeyboardOutputBackend)


# --- Editing key-combos must route through the backend, not the host keyboard ---
# The 'delete everything' and char-undo commands press special keys (Ctrl+A,
# Backspace). The virtual keyboard can't be made in the Flatpak sandbox (no
# /dev/uinput), so calling it directly would silently fail there.

def test_virtual_keyboard_backend_select_all_delete_delegates(monkeypatch):
    from tuxtalk import app
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    calls = []
    monkeypatch.setattr(app, "_send_select_all_delete", lambda: calls.append("clear") or True)
    assert VirtualKeyboardOutputBackend().select_all_delete() is True
    assert calls == ["clear"]


def test_virtual_keyboard_backend_backspaces_delegates(monkeypatch):
    from tuxtalk import app
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    calls = []
    monkeypatch.setattr(app, "_send_backspaces", lambda n: calls.append(n) or True)
    assert VirtualKeyboardOutputBackend().backspaces(5) is True
    assert calls == [5]


def test_libei_backend_select_all_delete_via_fresh_session(monkeypatch):
    import tuxtalk.libei_ctypes as libei_ctypes
    from tuxtalk.output_backends import LibeiOutputBackend
    b = LibeiOutputBackend()
    called, closed = [], []

    class FakeSession(_FakeSessionBase):
        def __init__(self, fd):
            super().__init__(fd)
            pass

        def pump_until_ready(self):
            return True

        def select_all_delete(self):
            called.append("clear")
            return True

    monkeypatch.setattr(b, "_run_handshake", lambda: (_portal_fd(), "/org/session/1"))
    monkeypatch.setattr(b, "_close_session", lambda p: closed.append(p))
    monkeypatch.setattr(libei_ctypes, "LibeiSession", FakeSession)

    assert b.select_all_delete() is True
    assert called == ["clear"]
    assert closed == ["/org/session/1"]  # session cleaned up


def test_libei_backend_backspaces_via_fresh_session(monkeypatch):
    import tuxtalk.libei_ctypes as libei_ctypes
    from tuxtalk.output_backends import LibeiOutputBackend
    b = LibeiOutputBackend()
    got, closed = [], []

    class FakeSession(_FakeSessionBase):
        def __init__(self, fd):
            super().__init__(fd)
            pass

        def pump_until_ready(self):
            return True

        def backspaces(self, n):
            got.append(n)
            return True

    monkeypatch.setattr(b, "_run_handshake", lambda: (_portal_fd(), "/org/session/2"))
    monkeypatch.setattr(b, "_close_session", lambda p: closed.append(p))
    monkeypatch.setattr(libei_ctypes, "LibeiSession", FakeSession)

    assert b.backspaces(4) is True
    assert got == [4]
    assert closed == ["/org/session/2"]


def test_handle_undo_everything_routes_through_backend(monkeypatch):
    # 'delete everything' must call the backend's select_all_delete, so it works
    # on the Flatpak (libei) exactly like on the host (the virtual keyboard).
    from tuxtalk import app
    monkeypatch.setattr(app, "detect_undo_command", lambda raw: ("everything", 1))
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_notify", lambda *a, **k: None)
    calls = []

    class FakeBackend:
        def select_all_delete(self):
            calls.append("clear")
            return True

    monkeypatch.setattr(app, "_output_backend", lambda: FakeBackend())
    assert app._handle_undo("delete everything", False, False) is True
    assert calls == ["clear"]


def test_handle_undo_char_undo_routes_backspaces_through_backend(monkeypatch):
    # Character-level undo must send its backspaces through the backend too.
    from tuxtalk import app
    monkeypatch.setattr(app, "detect_undo_command", lambda raw: ("word", 1))
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_notify", lambda *a, **k: None)
    app.state.last_inserted_text = "hello world"
    got = []

    class FakeBackend:
        def backspaces(self, n):
            got.append(n)
            return True

    monkeypatch.setattr(app, "_output_backend", lambda: FakeBackend())
    assert app._handle_undo("undo that", False, False) is True
    assert got and got[0] > 0  # deleted a positive number of characters


def test_flatpak_electron_app_types_via_libei_not_the_virtual_keyboard(monkeypatch, keyboard):
    # On the Flatpak, dictating into an Electron app (Claude Desktop) must NOT
    # use the virtual keyboard (no /dev/uinput in the sandbox). It types through
    # the libei backend directly, keeping only the tab→spaces safety (a real Tab
    # jumps focus to Claude Desktop's Send button).
    from tuxtalk import app
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    from tuxtalk import focused_window
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "Claude")
    monkeypatch.setattr(focused_window, "look", lambda: ("Claude", None))
    typed = []
    from tuxtalk.output_backends import OutputBackend

    class FakeBackend(OutputBackend):
        def type_text(self, t):
            typed.append(t)
            return True

    monkeypatch.setattr(app, "_output_backend", lambda: FakeBackend())
    app._inject_text("line1\tindented", "auto", 0.0)

    assert keyboard.events == [], "the virtual keyboard must not be used on the Flatpak"
    assert typed == ["line1    indented"], "tab→4 spaces, delivered via libei"


# --- the RemoteDesktop handshake --------------------------------------------

class _PortalBus:
    """A session bus whose RemoteDesktop portal answers each request with
    *answers[method]* (code, results), or never when the method is missing.
    Answers arrive on the calling thread's main context, like real signals."""

    def __init__(self, answers):
        from gi.repository import GLib
        self.GLib = GLib
        self.answers = answers
        self.subs = {}
        self._next = 1

    def get_unique_name(self):
        return ":1.42"

    def signal_subscribe(self, sender, iface, member, path, arg0, flags, callback):
        sid, self._next = self._next, self._next + 1
        self.subs[sid] = callback
        return sid

    def signal_unsubscribe(self, sid):
        del self.subs[sid]

    def call_sync(self, bus_name, obj, iface, method, params, *rest):
        if method not in self.answers:
            return
        code, results = self.answers[method]
        callback = self.subs[max(self.subs)]
        GLib = self.GLib
        variant = GLib.Variant("(ua{sv})", (code, results))
        src = GLib.idle_source_new()
        src.set_callback(lambda *_: (callback(self, "", "", "", "Response", variant), False)[1])
        src.attach(GLib.MainContext.get_thread_default())


def _handshake_in_a_thread(backend, seconds=10):
    import threading
    out = []
    t = threading.Thread(target=lambda: out.append(backend._run_handshake()), daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), "the handshake never returned"
    return out[0]


def test_an_error_inside_a_portal_answer_ends_the_handshake(monkeypatch):
    """A failure inside a response callback must end the handshake, not leave
    the dictation thread waiting forever."""
    from tuxtalk import portal_common as pc
    from tuxtalk.output_backends import LibeiOutputBackend
    bus = _PortalBus({"CreateSession": (0, {})})        # no session_handle in it
    monkeypatch.setattr(pc, "session_bus", lambda: bus)
    b = LibeiOutputBackend()
    monkeypatch.setattr(b, "_load_token", lambda: "")
    assert _handshake_in_a_thread(b) == (None, None)
    assert bus.subs == {}


def test_a_portal_that_never_answers_times_out(monkeypatch):
    from gi.repository import GLib
    from tuxtalk import portal_common as pc
    from tuxtalk.output_backends import LibeiOutputBackend
    bus = _PortalBus({"CreateSession": (0, {"session_handle": GLib.Variant("o", "/s/1")})})
    monkeypatch.setattr(pc, "session_bus", lambda: bus)
    b = LibeiOutputBackend()
    monkeypatch.setattr(b, "_load_token", lambda: "saved")
    monkeypatch.setattr(LibeiOutputBackend, "HANDSHAKE_TIMEOUT_S", 1)
    fd, session = _handshake_in_a_thread(b)
    assert fd is None
    assert session == "/s/1"                 # returned so the caller closes it
    assert bus.subs == {}


def test_flatpak_typing_records_what_libei_types_for_undo(monkeypatch):
    """libei types "…" as "..." (three keys), so undo must count three."""
    from tuxtalk import app
    from tuxtalk.output_backends import LibeiOutputBackend
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "org.kde.kwrite")
    typed = []

    class FakeLibei(LibeiOutputBackend):
        def type_text(self, t):
            typed.append(t)
            return True

    monkeypatch.setattr(app, "_output_backend", lambda: FakeLibei())
    assert app._inject_text("Wait… café", "auto", 0.0) == "ok"
    assert typed == ["Wait... cafe"]
    assert app.state.last_inserted_text == "Wait... cafe"


def test_libei_backend_presses_nothing_when_the_claim_says_no(monkeypatch):
    """A send held by a tap during its wait: no Enter goes out (send_countdown._claim)."""
    from tuxtalk import virtual_keyboard
    b, done = _libei_with_fake_session(monkeypatch)

    def claim():
        raise virtual_keyboard.GaveUp("held")
    assert b.press_keys([((), "ENTER")], claim=claim) is False
    assert done == ["ready"]


def test_libei_backend_claims_the_send_once_the_portal_is_ready(monkeypatch):
    """The handshake can wait minutes for the approval dialog; a tap during
    it must still hold the send, so the claim comes right before the Enter."""
    b, done = _libei_with_fake_session(monkeypatch)
    assert b.press_keys([((), "ENTER")], claim=lambda: lambda: done.append("taken")) is True
    assert done == ["ready", "taken", [((), "ENTER")]]


def _libei_with_fake_session(monkeypatch):
    """A Libei backend whose portal handshake and libei session are recorded."""
    from tuxtalk import libei_ctypes
    from tuxtalk.output_backends import LibeiOutputBackend
    done = []

    class Session:
        def __init__(self, fd):
            os.close(fd)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def pump_until_ready(self):
            done.append("ready")
            return True

        def press_chords(self, chords, ready=None):
            if ready is not None and not ready():
                return False
            done.append(chords)
            return True

    b = LibeiOutputBackend()
    r, w = os.pipe()
    os.close(w)
    monkeypatch.setattr(b, "_run_handshake", lambda: (r, None))
    monkeypatch.setattr(libei_ctypes, "LibeiSession", Session)
    return b, done


class _FakeLibei:
    """libei's calls, answering from a script of events: each ei_dispatch
    brings the next batch, and a batch may first run something (a tap of
    the hotkey arriving while the device wasn't ready)."""

    def __init__(self, batches):
        from tuxtalk import libei_ctypes as L
        self.types = {1: L.EI_EVENT_SEAT_ADDED, 2: L.EI_EVENT_DEVICE_RESUMED,
                      3: L.EI_EVENT_DEVICE_PAUSED}
        self.batches, self.queued, self.keys = list(batches), [], []

    def ei_dispatch(self, ctx):
        if self.batches:
            first, *events = self.batches.pop(0)
            if first is not None:
                first()
            self.queued += events

    def ei_get_event(self, ctx):
        return self.queued.pop(0) if self.queued else 0

    def ei_event_get_type(self, ev):
        return self.types[ev]

    def ei_setup_backend_fd(self, ctx, fd):
        os.close(fd)
        return 0

    def ei_get_fd(self, ctx):
        self.fd = _portal_fd()      # the session's socket, closed with it
        return self.fd

    def ei_unref(self, ctx):
        os.close(self.fd)

    def ei_device_keyboard_key(self, dev, code, down):
        self.keys.append((code, down))

    ei_new_sender = ei_now = ei_event_get_seat = ei_event_get_device = lambda self, *a: 7
    ei_seat_bind_capabilities = ei_event_unref = ei_device_frame = lambda self, *a: None
    ei_device_start_emulating = ei_device_stop_emulating = lambda self, *a: None


def test_a_tap_while_the_libei_device_comes_back_holds_the_send(monkeypatch):
    """The keyboard device went away (paused) as the Enter was about to go,
    and a tap held the send while TuxTalk waited for it to come back. Claimed
    before that wait, the Enter went out after the tap."""
    from tuxtalk import libei_ctypes, virtual_keyboard
    from tuxtalk.output_backends import LibeiOutputBackend
    held = []
    libei = _FakeLibei([(None, 1, 2),                       # ready for the session
                        (None, 3),                          # paused before the Enter
                        (lambda: held.append(True), 2),     # the tap, then back
                        (None, 1, 2)])                      # the next session
    monkeypatch.setattr(libei_ctypes, "load_libei", lambda: libei)
    monkeypatch.setattr(libei_ctypes.time, "sleep", lambda s: None)
    b = LibeiOutputBackend()
    monkeypatch.setattr(b, "_run_handshake", lambda: (_portal_fd(), None))

    def take():
        if held:
            raise virtual_keyboard.GaveUp("held")
    assert b.press_keys([((), "ENTER")], claim=lambda: take) is False
    assert held == [True] and libei.keys == []
    assert b.press_keys([((), "ENTER")], claim=lambda: lambda: None) is True
    assert libei.keys == [(libei_ctypes.KEY_ENTER, True), (libei_ctypes.KEY_ENTER, False)]
