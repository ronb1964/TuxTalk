"""The virtual-keyboard backend must keep delegating to keyboard_output.type_text so the non-Flatpak
typing path is behaviorally unchanged."""
import os

import pytest
from tuxtalk import recording_state as rs
from tuxtalk import keyboard_output
from tuxtalk import voice_commands
from tuxtalk import delivery


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
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    calls = []
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: calls.append(t) or True)
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
    keyboard_output._OUTPUT_BACKEND = None  # reset the process cache for the test
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    assert isinstance(keyboard_output.output_backend(), VirtualKeyboardOutputBackend)


# --- Editing key-combos must route through the backend, not the host keyboard ---
# The 'delete everything' and char-undo commands press special keys (Ctrl+A,
# Backspace). The virtual keyboard can't be made in the Flatpak sandbox (no
# /dev/uinput), so calling it directly would silently fail there.

def test_virtual_keyboard_backend_select_all_delete_delegates(monkeypatch):
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    calls = []
    monkeypatch.setattr(keyboard_output, "send_select_all_delete", lambda: calls.append("clear") or True)
    assert VirtualKeyboardOutputBackend().select_all_delete() is True
    assert calls == ["clear"]


def test_virtual_keyboard_backend_backspaces_delegates(monkeypatch):
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    calls = []
    monkeypatch.setattr(keyboard_output, "send_backspaces", lambda n: calls.append(n) or True)
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
    monkeypatch.setattr(voice_commands, "detect_undo_command", lambda raw: ("everything", 1))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    calls = []

    class FakeBackend:
        def select_all_delete(self):
            calls.append("clear")
            return True

    monkeypatch.setattr(keyboard_output, "output_backend", lambda: FakeBackend())
    assert voice_commands.handle_undo("delete everything", False, False) is True
    assert calls == ["clear"]


def test_handle_undo_char_undo_routes_backspaces_through_backend(monkeypatch):
    # Character-level undo must send its backspaces through the backend too.
    monkeypatch.setattr(voice_commands, "detect_undo_command", lambda raw: ("word", 1))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    rs.state.last_inserted_text = "hello world"
    got = []

    class FakeBackend:
        def backspaces(self, n):
            got.append(n)
            return True

    monkeypatch.setattr(keyboard_output, "output_backend", lambda: FakeBackend())
    assert voice_commands.handle_undo("undo that", False, False) is True
    assert got and got[0] > 0  # deleted a positive number of characters


def test_flatpak_electron_app_types_via_libei_not_the_virtual_keyboard(monkeypatch, keyboard):
    # On the Flatpak, dictating into an Electron app (Claude Desktop) must NOT
    # use the virtual keyboard (no /dev/uinput in the sandbox). It types through
    # the libei backend directly, keeping only the tab→spaces safety (a real Tab
    # jumps focus to Claude Desktop's Send button).
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    from tuxtalk import focused_window
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "Claude")
    monkeypatch.setattr(focused_window, "look", lambda: ("Claude", None))
    typed = []
    from tuxtalk.output_backends import OutputBackend

    class FakeBackend(OutputBackend):
        def type_text(self, t):
            typed.append(t)
            return True

    monkeypatch.setattr(keyboard_output, "output_backend", lambda: FakeBackend())
    delivery.inject_text("line1\tindented", "auto")

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
    from tuxtalk.output_backends import LibeiOutputBackend
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "org.kde.kwrite")
    typed = []

    class FakeLibei(LibeiOutputBackend):
        def type_text(self, t):
            typed.append(t)
            return True

    monkeypatch.setattr(keyboard_output, "output_backend", lambda: FakeLibei())
    assert delivery.inject_text("Wait… café", "auto") == "ok"
    assert typed == ["Wait... cafe"]
    assert rs.state.last_inserted_text == "Wait... cafe"


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


def test_a_portal_session_that_wont_close_is_logged(monkeypatch, caplog):
    from gi.repository import GLib
    from tuxtalk import portal_common
    from tuxtalk.output_backends import LibeiOutputBackend

    class Bus:
        def call_sync(self, *args):
            raise GLib.Error("the portal went away")
    monkeypatch.setattr(portal_common, "session_bus", lambda: Bus())
    with caplog.at_level("WARNING"):
        LibeiOutputBackend()._close_session("/org/freedesktop/portal/desktop/session/1_9/t")
    assert "Couldn't close the remote-control session" in caplog.text


def _libei_typing_into(monkeypatch, focus_moves_while_the_device_is_away):
    """_libei_with_the_focus_moving, with the typing held to KWrite, where it began."""
    b, keys = _libei_with_the_focus_moving(monkeypatch, focus_moves_while_the_device_is_away)
    monkeypatch.setattr(keyboard_output.this_thread, "into", ("org.kde.kwrite", ("tray", "{1}")))
    return b, keys


def _libei_with_the_focus_moving(monkeypatch, focus_moves_while_the_device_is_away):
    """A Libei backend over a fake libei whose keyboard device goes away
    (paused) right before typing and comes back; while it's away, the focus
    moves from KWrite to a chat or doesn't. Returns the backend and the keys
    libei got."""
    from tuxtalk import focused_window, libei_ctypes
    from tuxtalk.output_backends import LibeiOutputBackend
    focus = [("org.kde.kwrite", ("tray", "{1}"))]

    def away():
        if focus_moves_while_the_device_is_away:
            focus[0] = ("brave-browser", ("tray", "{2}"))
    libei = _FakeLibei([(None, 1, 2),          # ready for the session
                        (None, 3),             # paused as the keys are about to go
                        (away, 2)])            # back, up to 2 s later
    monkeypatch.setattr(libei_ctypes, "load_libei", lambda: libei)
    monkeypatch.setattr(libei_ctypes.time, "sleep", lambda s: None)
    monkeypatch.setattr(focused_window, "look", lambda: focus[0])
    b = LibeiOutputBackend()
    monkeypatch.setattr(b, "_run_handshake", lambda: (_portal_fd(), None))
    return b, libei.keys


def test_flatpak_typing_held_to_a_window_goes_nowhere_once_another_has_the_focus(monkeypatch):
    """The user clicked into a chat while the portal's keyboard came back:
    Fix a Word's replacement, or a dictation that may only go where it was
    looked at, went into the chat."""
    import pytest
    from tuxtalk import virtual_keyboard
    b, keys = _libei_typing_into(monkeypatch, focus_moves_while_the_device_is_away=True)
    with pytest.raises(virtual_keyboard.GaveUp):
        b.type_text("fixed")
    assert keys == []


def test_flatpak_typing_held_to_a_window_goes_in_while_it_has_the_focus(monkeypatch):
    b, keys = _libei_typing_into(monkeypatch, focus_moves_while_the_device_is_away=False)
    assert b.type_text("fixed") is True
    assert len(keys) == 2 * len("fixed")


@pytest.fixture
def flatpak_commands(monkeypatch):
    """Voice commands said right after "hello world" was dictated into KWrite,
    in the Flatpak: the problems they reported."""
    from tuxtalk import notices
    from tuxtalk import live_settings as live
    problems = []
    monkeypatch.setattr(notices, "edit_problem", lambda kind, beeps: problems.append(kind))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr(live, "voice_navigation", True)
    monkeypatch.setattr(rs.state, "last_inserted_text", "hello world")
    monkeypatch.setattr(rs.state, "last_inserted_window", "org.kde.kwrite")
    monkeypatch.setattr(rs.state, "last_inserted_where", ("org.kde.kwrite", ("tray", "{1}")))
    return problems


@pytest.mark.parametrize("said", ["delete last word", "clear everything", "select the last word"])
def test_a_flatpak_voice_command_presses_nothing_once_another_window_has_the_focus(
        monkeypatch, flatpak_commands, said):
    """The portal's keyboard took a moment to come back after "delete last
    word", and the user clicked into a chat meanwhile: the backspaces went
    into the chat, deleting what was there."""
    b, keys = _libei_with_the_focus_moving(monkeypatch, focus_moves_while_the_device_is_away=True)
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: b)
    assert (voice_commands.handle_undo(said, False, False)
            or voice_commands.handle_navigation(said, False)) is True
    assert keys == [] and flatpak_commands == ["focus_moved"]
    if said == "delete last word":       # nothing deleted: a second try still can
        assert rs.state.last_inserted_text == "hello world"     # nothing was deleted


@pytest.mark.parametrize("said", ["delete last word", "clear everything", "select the last word"])
def test_a_flatpak_voice_command_goes_in_while_its_window_has_the_focus(
        monkeypatch, flatpak_commands, said):
    b, keys = _libei_with_the_focus_moving(monkeypatch, focus_moves_while_the_device_is_away=False)
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: b)
    assert (voice_commands.handle_undo(said, False, False)
            or voice_commands.handle_navigation(said, False)) is True
    assert keys and flatpak_commands == []


def test_a_flatpak_dictation_goes_nowhere_once_the_cursor_is_in_a_password_field(monkeypatch):
    """The portal's approval dialog and keyboard can take a while: a tab
    into a password box meanwhile got the dictation, which may be the
    password itself."""
    from tuxtalk import focused_window
    b, keys = _libei_with_the_focus_moving(monkeypatch, focus_moves_while_the_device_is_away=False)
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: b)
    answers = iter([False])                 # a text box when the dictation was made
    monkeypatch.setattr(focused_window, "is_password", lambda **k: next(answers, True))
    assert delivery.inject_text("swordfish", "auto") == "password_field"
    assert keys == []


def test_a_flatpak_dictation_stops_before_a_line_break_into_a_password_field(monkeypatch):
    """Typed through the portal in one go, the lines were checked once: a
    tab into a password box after the first line got the line break, which
    can press a login form's button, and the next line."""
    from tuxtalk import focused_window
    b, keys = _libei_with_the_focus_moving(monkeypatch, focus_moves_while_the_device_is_away=False)
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: b)
    answers = iter([False, False])          # when it was made, and before its first line
    monkeypatch.setattr(focused_window, "is_password", lambda **k: next(answers, True))
    assert delivery.inject_text("ab\ncd", "auto") == "password_field"
    assert [code for code, down in keys if down] == [30, 48]      # a, b: no Enter, no c or d
