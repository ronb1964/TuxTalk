"""Pure-logic checks for the libei ctypes wrapper. The actual keystroke
injection needs a live EIS connection and is exercised by the Flatpak
integration test (BB-9), not here."""
import ctypes
import os

import pytest

from tuxtalk import libei_ctypes as L


def test_keycode_map_covers_punctuated_output():
    for ch in "Hello, world! 123?":
        assert ch in L.CHAR_TO_KEYCODE, f"missing keycode for {ch!r}"
    assert L.CHAR_TO_KEYCODE["h"] == 35
    assert L.CHAR_TO_KEYCODE[","] == 51


def test_uppercase_and_shifted_need_shift():
    # 'H' shares 'h' keycode but needs Shift; '?' shares '/' keycode + Shift.
    assert L.CHAR_TO_KEYCODE["H"] == L.CHAR_TO_KEYCODE["h"]
    assert "H" in L.SHIFT_CHARS
    assert "?" in L.SHIFT_CHARS
    assert "h" not in L.SHIFT_CHARS


def test_enum_constants():
    assert L.EI_EVENT_SEAT_ADDED == 3
    assert L.EI_EVENT_DEVICE_RESUMED == 8
    assert L.EI_DEVICE_CAP_KEYBOARD == 4


def test_load_libei_sets_signatures_or_skips():
    try:
        ei = L.load_libei()
    except OSError:
        pytest.skip("libei.so.1 not present on this host")
    assert ei.ei_new_sender.restype == ctypes.c_void_p
    assert ei.ei_setup_backend_fd.restype == ctypes.c_int


class _FakeEi:
    """Records the (keycode, pressed) sequence emitted through libei, so the key
    combos can be asserted without a live EIS connection."""

    def __init__(self):
        self.events = []

    def ei_device_keyboard_key(self, dev, keycode, pressed):
        self.events.append((keycode, bool(pressed)))

    def ei_device_frame(self, dev, when):
        pass

    def ei_now(self, ctx):
        return 0

    def ei_device_start_emulating(self, dev, seq):
        pass

    def ei_device_stop_emulating(self, dev):
        pass

    def ei_dispatch(self, ctx):
        pass

    def ei_get_event(self, ctx):
        return None  # nothing queued -> _drain loop exits immediately


def _fake_ready_session(monkeypatch):
    """A LibeiSession with a fake ei and a live device, bypassing __init__ (which
    needs a real EIS fd). time.sleep is neutralised so the flush loop is instant."""
    monkeypatch.setattr(L.time, "sleep", lambda *a, **k: None)
    s = L.LibeiSession.__new__(L.LibeiSession)
    s.ei = _FakeEi()
    s.ctx = object()
    s.device = object()
    s._seq = 0
    return s


def test_libei_select_all_delete_emits_ctrl_a_then_backspace(monkeypatch):
    s = _fake_ready_session(monkeypatch)
    assert s.select_all_delete() is True
    assert s.ei.events == [
        (L.KEY_LEFTCTRL, True),
        (L.KEY_A, True),
        (L.KEY_A, False),
        (L.KEY_LEFTCTRL, False),
        (L.KEY_BACKSPACE, True),
        (L.KEY_BACKSPACE, False),
    ]


def test_libei_backspaces_emits_n_backspaces(monkeypatch):
    s = _fake_ready_session(monkeypatch)
    assert s.backspaces(3) is True
    assert s.ei.events == [
        (L.KEY_BACKSPACE, True), (L.KEY_BACKSPACE, False),
        (L.KEY_BACKSPACE, True), (L.KEY_BACKSPACE, False),
        (L.KEY_BACKSPACE, True), (L.KEY_BACKSPACE, False),
    ]


def test_libei_backspaces_zero_is_noop(monkeypatch):
    s = _fake_ready_session(monkeypatch)
    assert s.backspaces(0) is True
    assert s.ei.events == []


# --- typing only what can be typed ------------------------------------------

def test_smart_quotes_and_dashes_are_typed_as_plain_lookalikes():
    keys = L.plan_keys("“Hi” — it’s")
    plain = L.plan_keys('"Hi" - it\'s')
    assert keys == plain and len(keys) == len("“Hi” — it’s")


def test_text_with_an_untypable_character_types_nothing(monkeypatch):
    """Skipping characters libei can't type while type_string still says it
    typed everything would make undo later delete the user's own text."""
    s = _fake_ready_session(monkeypatch)
    assert s.type_string("5€ time") is False
    assert s.ei.events == []


def test_ellipses_and_accents_are_typed_as_their_plain_forms():
    """Dictation turns "..." into "…", and an accented word is common; both
    have a plain form, so refusing the whole dictation over them would lose
    far more than it saves."""
    assert L.typeable_text("Wait… café, naïve “ok”") == 'Wait... cafe, naive "ok"'
    assert L.plan_keys("Wait… café") == L.plan_keys("Wait... cafe")


def test_characters_without_a_plain_form_are_left_for_plan_keys_to_refuse():
    assert L.typeable_text("m² 5€ 日本") == "m² 5€ 日本"
    assert L.plan_keys("m²") is None
    assert L.typeable_text("a" + L.LINE_BREAK + "b") == "a" + L.LINE_BREAK + "b"


def test_line_breaks_are_planned_as_enter_keys():
    assert L.plan_keys("a" + L.LINE_BREAK + "b\nc") == [
        (L.CHAR_TO_KEYCODE["a"], False), (L.KEY_ENTER, True),
        (L.CHAR_TO_KEYCODE["b"], False), (L.KEY_ENTER, False),
        (L.CHAR_TO_KEYCODE["c"], False)]


# --- freeing each session ----------------------------------------------------

class _LifecycleEi(_FakeEi):
    """Enough of libei for a whole session: one RESUMED device event, and a
    count of every ref and unref."""

    def __init__(self, fd):
        super().__init__()
        self.fd = fd
        self.calls = []
        self.queue = [101]

    def ei_new_sender(self, _):
        return 7

    def ei_setup_backend_fd(self, ctx, fd):
        self.backend_fd = fd
        return 0

    def ei_get_fd(self, ctx):
        return self.fd

    def ei_get_event(self, ctx):
        return self.queue.pop() if self.queue else None

    def ei_event_get_type(self, ev):
        return L.EI_EVENT_DEVICE_RESUMED

    def ei_event_get_device(self, ev):
        return 55

    def ei_event_unref(self, ev):
        pass

    def ei_device_ref(self, dev):
        self.calls.append(("ref", dev))

    def ei_device_unref(self, dev):
        self.calls.append(("device_unref", dev))

    def ei_unref(self, ctx):
        self.calls.append(("unref", ctx))
        os.close(self.backend_fd)       # libei closes the socket it was given


def test_each_session_frees_its_context_and_device(monkeypatch):
    """Otherwise every dictation leaks a libei context and a device reference."""
    from tuxtalk.output_backends import LibeiOutputBackend
    r, w = os.pipe()
    os.write(w, b"x")                   # readable, so the select returns at once
    fake = _LifecycleEi(r)
    monkeypatch.setattr(L, "load_libei", lambda: fake)
    monkeypatch.setattr(L.time, "sleep", lambda *a: None)
    b = LibeiOutputBackend()
    portal_r, portal_w = os.pipe()
    os.close(portal_w)
    monkeypatch.setattr(b, "_run_handshake", lambda: (portal_r, None))

    assert b.type_text("hi") is True
    assert fake.calls == [("ref", 55), ("device_unref", 55), ("unref", 7)]
    # The portal's fd and libei's copy were each closed exactly once.
    for fd in (portal_r, fake.backend_fd):
        with pytest.raises(OSError):
            os.fstat(fd)
    os.close(r)
    os.close(w)
