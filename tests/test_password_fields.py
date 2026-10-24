"""TuxTalk never types into a password field.

The words may be the password itself, so in a password field a dictation
(snippets included), Paste Last Dictation and a voice edit type nothing, and
the text stays out of Recent Dictations and notifications. Whether the cursor
is in one comes from the accessibility bus (atspi_helper); whenever that can't
answer, quickly, typing goes ahead as it always did.
"""
import logging
import time
import types

import numpy as np
import pytest
from gi.repository import Gio, GLib

from tuxtalk import app, atspi_helper, delivery_notices, history, screen_message, send_countdown

ACTIVE = 1 << atspi_helper._STATE_ACTIVE
FOCUSED = 1 << atspi_helper._STATE_FOCUSED
SHOWING = 1 << atspi_helper._STATE_SHOWING
PASSWORD = atspi_helper._ROLE_PASSWORD_TEXT
TEXT = 61      # ATSPI_ROLE_TEXT
# Taken before conftest swaps in a stand-in, which every test gets.
REAL_PROMPT_WAITING = app._polkit_prompt_waiting


# --- the app: nothing typed, nothing kept ------------------------------------------

@pytest.fixture
def desktop(monkeypatch):
    """Everything TuxTalk would do to the desktop, recorded. *password* is
    the answer to "is the focus in a password field?" once *answers* (one
    per question, in order) run out."""
    seen = {"typed": [], "pasted": [], "history": [], "notes": [], "beeps": [], "labels": [],
            "password": False, "answers": []}

    class Keyboard:
        def typeable(self, text):
            return text

        def type_text(self, text):
            seen["typed"].append(text)
            return True

    monkeypatch.setattr(app, "_output_backend", lambda: Keyboard())
    monkeypatch.setattr(app, "_determine_injection_method", lambda mode: ("type", "test"))
    monkeypatch.setattr(app, "_paste_text",
                        lambda text, *a, **k: seen["pasted"].append(text) or True)
    monkeypatch.setattr(history, "add_entry",
                        lambda text, replaces=None: seen["history"].append(text))
    monkeypatch.setattr(app, "_notify", lambda title, body: seen["notes"].append(body))
    monkeypatch.setattr(app, "_beep", lambda on, *tone, **k: on and seen["beeps"].append(tone))
    monkeypatch.setattr(screen_message, "show_over_active_window",
                        lambda text, **k: seen["labels"].append(text))
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: None)
    monkeypatch.setattr(app, "_usage_stats", False, raising=False)
    monkeypatch.setattr(atspi_helper, "focused_is_password",
                        lambda: seen["answers"].pop(0) if seen["answers"] else seen["password"])
    return seen


def dictate(monkeypatch, said="my secret is swordfish", beeps_on=True, chunk_role=None):
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, language: said)
    frames = [(np.ones(app.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    app._transcribe_and_inject(frames, app.SAMPLE_RATE, beeps_on=beeps_on,
                               smart_quotes=False, notify_on=True, chunk_role=chunk_role)


def test_a_dictation_in_a_password_field_is_not_typed_or_kept(desktop, monkeypatch, caplog):
    desktop["password"] = True
    with caplog.at_level(logging.INFO, logger="tuxtalk.app"):
        dictate(monkeypatch)
    assert desktop["typed"] == [] and desktop["history"] == []
    # Not even in the "Transcribed: …" notification.
    assert not any("swordfish" in n for n in desktop["notes"])
    assert desktop["beeps"] == [app.CANCEL_BEEP]
    assert desktop["labels"] == [delivery_notices.PASSWORD_FIELD_LABEL]
    assert "password field" in caplog.text and "swordfish" not in caplog.text


def test_a_dictation_anywhere_else_is_typed_and_kept(desktop, monkeypatch):
    dictate(monkeypatch)
    assert len(desktop["typed"]) == 1 and "swordfish" in desktop["typed"][0]
    assert len(desktop["history"]) == 1
    assert desktop["labels"] == []


def test_beep_and_label_say_it_without_a_notification(desktop, monkeypatch):
    monkeypatch.setattr(app, "_gtk_loop_running", True)
    monkeypatch.setenv("DISPLAY", ":0")
    desktop["password"] = True
    dictate(monkeypatch)
    assert desktop["notes"] == []


@pytest.mark.parametrize("beeps_on, label_can_show", [(False, True), (True, False)])
def test_a_notification_says_it_when_beep_or_label_cant(desktop, monkeypatch, beeps_on,
                                                        label_can_show):
    monkeypatch.setattr(app, "_gtk_loop_running", label_can_show)
    monkeypatch.setenv("DISPLAY", ":0")
    desktop["password"] = True
    dictate(monkeypatch, beeps_on=beeps_on)
    assert desktop["notes"] == [delivery_notices.PASSWORD_FIELD_NOTICE]


def test_type_at_pause_types_nothing_of_its_own_into_a_password_field(desktop, monkeypatch):
    """The mark held back from the phrase before, a held "No wait." and the
    local AI all come before the words; none of them reach the field."""
    ran = []
    monkeypatch.setattr(app, "_resolve_boundary", lambda *a, **k: ran.append("mark"))
    monkeypatch.setattr(app, "_flush_boundary", lambda *a, **k: ran.append("mark"))
    monkeypatch.setattr(app, "_prepare_text", lambda *a, **k: ran.append("cleanup") or "x")
    monkeypatch.setattr(app.state, "pending_boundary", {"mark": "."})
    monkeypatch.setattr(app.state, "held_cue", "No wait.")
    desktop["password"] = True
    dictate(monkeypatch, chunk_role="middle")
    assert desktop["typed"] == [] and ran == []
    assert app.state.pending_boundary is None and app.state.held_cue == ""
    assert desktop["labels"] == [delivery_notices.PASSWORD_FIELD_LABEL]


def test_saying_only_press_enter_still_presses_enter_there(desktop, monkeypatch):
    """Type the password by hand, then say "press enter" to log in."""
    pressed = []
    monkeypatch.setattr(app, "_press_key", lambda key, why: pressed.append(why) or True)
    desktop["password"] = True
    dictate(monkeypatch, said="press enter")
    assert pressed and desktop["typed"] == [] and desktop["labels"] == []


def test_saying_only_press_tab_still_presses_tab_there(desktop, monkeypatch):
    """Move from the password box to the next one by voice."""
    pressed = []
    monkeypatch.setattr(app, "_press_key", lambda key, why: pressed.append(key) or True)
    desktop["password"] = True
    dictate(monkeypatch, said="Press tab.")
    assert pressed == ["TAB"] and desktop["typed"] == [] and desktop["labels"] == []


def test_words_before_press_tab_are_not_typed_there(desktop, monkeypatch):
    pressed = []
    monkeypatch.setattr(app, "_press_key", lambda key, why: pressed.append(key) or True)
    desktop["password"] = True
    dictate(monkeypatch, said="swordfish press tab")
    assert pressed == [] and desktop["typed"] == [] and desktop["history"] == []
    assert desktop["labels"] == [delivery_notices.PASSWORD_FIELD_LABEL]


def test_paste_last_dictation_types_nothing_in_a_password_field(desktop, monkeypatch):
    monkeypatch.setattr(history, "get_entries", lambda: ["my secret is swordfish"])
    cfg = types.SimpleNamespace(beeps=True, auto_space=True, injection_mode="type")
    desktop["password"] = True
    app._paste_last_dictation(cfg)
    assert desktop["typed"] == []
    assert desktop["labels"] == [delivery_notices.PASSWORD_FIELD_LABEL]
    desktop["password"] = False
    app._paste_last_dictation(cfg)
    assert desktop["typed"] == ["my secret is swordfish "]


def test_delivery_stops_when_the_focus_moves_into_a_password_field(desktop, monkeypatch):
    """Pasted line by line, a dictation is asked about again before each line."""
    monkeypatch.setattr(app, "_determine_injection_method", lambda mode: ("paste", "test"))
    monkeypatch.setattr(app, "_send_shift_enter", lambda: True)
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    desktop["answers"], desktop["password"] = [False], True   # a text box, then a password field
    assert app._inject_text("first line\nsecond line", "auto", 0.0) == "password_field"
    assert desktop["pasted"] == ["first line"] and desktop["typed"] == []


def test_a_paste_stopped_there_isnt_finished_by_typing(desktop, monkeypatch):
    """Not asked a second time: a slow app whose answer runs out of time says
    "no", and the rest of the dictation was then typed into the field."""
    monkeypatch.setattr(app, "_determine_injection_method", lambda mode: ("paste", "test"))
    monkeypatch.setattr(app, "_send_shift_enter", lambda: True)
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    desktop["answers"] = [False, True]          # then "no" to any further question
    assert app._inject_text("first line\nsecond line", "auto", 0.0) == "password_field"
    assert desktop["pasted"] == ["first line"] and desktop["typed"] == []


def test_typed_line_by_line_it_stops_there_too(desktop, monkeypatch):
    """X11 types every dictation, and asks again before each line as the paste does."""
    keys = []
    monkeypatch.setattr(app, "_output_backend",
                        lambda: types.SimpleNamespace(typeable=lambda t: t,
                                                      type_text=app._type_text))
    monkeypatch.setattr(app, "_type_text_raw", lambda t: keys.append(t) or True)
    monkeypatch.setattr(app, "_send_shift_enter", lambda: keys.append("SHIFT+ENTER") or True)
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    desktop["answers"], desktop["password"] = [False], True
    assert app._inject_text("first line\nsecond line", "auto", 0.0) == "password_field"
    assert keys == ["first line", "SHIFT+ENTER"]
    assert app.state.last_inserted_text == ""      # undo deletes nothing it can't count


def test_a_dictation_refused_at_the_last_moment_leaves_recent_dictations(desktop, monkeypatch):
    """It goes into Recent Dictations before it's typed; refused then, it comes back out."""
    forgotten = []
    monkeypatch.setattr(history, "forget", lambda text: forgotten.append(text))
    monkeypatch.setattr(app.state, "said_text", "")
    desktop["answers"] = [False, False, True]    # before the AI, before the beep, at delivery
    dictate(monkeypatch)
    assert desktop["typed"] == []
    assert len(forgotten) == 1 and "swordfish" in forgotten[0]


def test_sending_automatically_doesnt_press_enter_in_a_password_field(desktop, monkeypatch,
                                                                     keyboard):
    """Dictate a username, tab into the password box during the wait: Enter
    there would submit the login form."""
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    monkeypatch.setattr(app, "_output_backend", VirtualKeyboardOutputBackend)
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    pending = {"window": None, "why": "sending automatically", "timer": None}
    monkeypatch.setattr(app.state, "pending_send", pending)
    desktop["password"] = True
    send_countdown._send_when_due(pending)
    assert keyboard.events == [] and desktop["labels"] == [send_countdown.PASSWORD_FIELD_NOT_SENT]
    desktop["password"], desktop["labels"] = False, []
    app.state.pending_send = pending
    send_countdown._send_when_due(pending)
    assert keyboard.typed_keys() == [28]          # KEY_ENTER


def test_no_wait_doesnt_backspace_in_a_password_field(desktop, monkeypatch):
    """The AI behind "no wait" can take seconds after the first check."""
    erased = []
    monkeypatch.setattr(app, "_output_backend",
                        lambda: types.SimpleNamespace(backspaces=lambda n: erased.append(n)))
    monkeypatch.setattr(app.state, "last_inserted_window", None)
    desktop["password"] = True
    assert app._erase_typed("Meet at three. ") is False
    assert erased == []


def test_fix_a_word_doesnt_touch_a_password_field(desktop, monkeypatch):
    from tuxtalk import fix_word_dialog
    keys = []
    monkeypatch.setattr(fix_word_dialog, "_REFOCUS_S", 0)
    monkeypatch.setattr(app, "_output_backend",
                        lambda: types.SimpleNamespace(press_keys=lambda c: keys.append(c) or True,
                                                      type_text=lambda t: keys.append(t) or True))
    desktop["password"] = True
    assert fix_word_dialog.fix_in_text("I use tax talk ", (6, 14, "TuxTalk"), "kwrite") is False
    assert keys == []


# --- the desktop's own password prompt ----------------------------------------------

@pytest.mark.parametrize("desktop_name, focused, waiting, refused", [
    ("cinnamon", "tt-sink", True, True),      # drawn by the shell, over the app,
    ("cosmic", "tt-sink", True, True),        # and it takes every key
    ("cinnamon", "tt-sink", False, False),    # nothing waits
    ("kde", None, True, False),               # unknown focus isn't a prompt
    ("kde", "org.kde.polkit-kde-authentication-agent-1", True, True),
    ("kde", "org.kde.polkit-kde-authentication-agent-1", None, True),  # /proc hidden
    ("lxqt", "lxqt-policykit-agent", True, True),
    ("kde", "org.kde.kwrite", True, False),   # the prompt waits behind KWrite
])
def test_the_desktops_own_password_prompt_gets_nothing(desktop, monkeypatch, desktop_name,
                                                       focused, waiting, refused):
    from tuxtalk import desktop_detect
    monkeypatch.setattr(desktop_detect, "get_desktop_environment", lambda: desktop_name)
    monkeypatch.setattr(app, "_polkit_prompt_waiting", lambda: waiting)
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: focused)
    dictate(monkeypatch)
    assert (desktop["typed"] == []) is refused


def test_with_no_prompt_waiting_the_focused_window_isnt_asked(desktop, monkeypatch):
    """The check runs before every typed line; asking the tray which window
    has the focus each time would slow typing down for nothing."""
    asked = []
    monkeypatch.setattr(app, "_polkit_prompt_waiting", lambda: False)
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: asked.append(1))
    assert app._password_prompt_has_focus() is False
    assert asked == []


def test_a_waiting_prompt_is_seen_by_its_helper_process(tmp_path):
    """polkit-agent-helper-1 runs while a prompt waits; the kernel keeps the
    first 15 characters of its name."""
    import shutil
    import subprocess
    helper = tmp_path / "polkit-agent-helper-1"
    shutil.copy("/bin/sleep", helper)
    proc = subprocess.Popen([str(helper), "30"])
    try:
        time.sleep(0.2)
        assert REAL_PROMPT_WAITING() is True
    finally:
        proc.kill()
        proc.wait()
    assert REAL_PROMPT_WAITING() is False      # unless a real prompt is open right now


# --- the question: which widget has the focus, and is it a password field ----------

class FakeA11yBus:
    """The accessibility bus as call_sync sees it: apps, their windows and
    widgets, each a (bus name, path) pair like the real ones."""

    def __init__(self):
        self.nodes, self.calls = {}, []
        self.apps = self._add(("registry", "root"))["children"]

    def _add(self, ref, **node):
        self.nodes[ref] = dict(dict(name="", role=0, states=0, children=[], collection=True,
                                    hang=False), **node)
        return self.nodes[ref]

    def app(self, name, focus_role, active=True, collection=True, hang=False):
        """An app with one window holding a text box and the focused widget,
        of *focus_role*. *collection* False is an app without the Collection
        interface (Qt 5), so it is walked; "slow" times out on it. *hang*: it
        never answers at all."""
        def ref(path):
            return (f":{name}", path)
        common = dict(collection=collection, hang=hang)
        self._add(ref("/2"), role=focus_role, states=SHOWING | FOCUSED, **common)
        self._add(ref("/3"), role=TEXT, states=SHOWING, **common)
        self._add(ref("/1"), states=SHOWING | (ACTIVE if active else 0),
                  children=[ref("/3"), ref("/2")], **common)
        self._add(ref("/root"), name=name, children=[ref("/1")], **common)
        self.apps.append(ref("/root"))

    def call_sync(self, name, path, interface, method, args, reply_type, flags, timeout_ms,
                  cancellable):
        if (name, path) == (atspi_helper._REGISTRY, atspi_helper._ROOT):
            name, path = "registry", "root"
        node = self.nodes[(name, path)]
        self.calls.append((name, path, method))
        if node["hang"]:
            time.sleep(timeout_ms / 1000)
            raise GLib.Error("Timeout was reached")
        if method == "GetChildren":
            return GLib.Variant("(a(so))", (node["children"],))
        if method == "GetState":
            return GLib.Variant("(au)", ([node["states"] & 0xFFFFFFFF, node["states"] >> 32],))
        if method == "GetRole":
            return GLib.Variant("(u)", (node["role"],))
        if method == "Get":
            return GLib.Variant("(v)", (GLib.Variant("s", node["name"]),))
        if method == "GetMatches":
            if node["collection"] == "slow":
                time.sleep(timeout_ms / 1000)
                raise GLib.Error("Timeout was reached")
            if not node["collection"]:
                raise Gio.DBusError.new_for_dbus_error(
                    "org.freedesktop.DBus.Error.UnknownMethod",
                    "No such interface “org.a11y.atspi.Collection”")
            found = [c for c in node["children"] if self.nodes[c]["states"] & FOCUSED]
            return GLib.Variant("(a(so))", (found,))
        raise AssertionError(f"unexpected call {method}")


@pytest.fixture
def bus(monkeypatch):
    fake = FakeA11yBus()
    monkeypatch.setattr(atspi_helper, "_a11y_bus", lambda deadline: fake)
    return fake


def test_a_focused_password_field_is_found(bus):
    bus.app("gedit", PASSWORD)
    assert atspi_helper.focused_is_password() is True


def test_a_focused_text_box_is_not_a_password_field(bus):
    bus.app("gedit", TEXT)
    assert atspi_helper.focused_is_password() is False


def test_apps_without_the_collection_interface_are_walked(bus):
    bus.app("kwrite", PASSWORD, collection=False)
    assert atspi_helper.focused_is_password() is True


def test_a_password_field_in_a_window_in_the_background_doesnt_count(bus):
    bus.app("seahorse", PASSWORD, active=False)
    assert atspi_helper.focused_is_password() is False


def test_no_accessibility_bus_means_typing_as_usual(monkeypatch):
    monkeypatch.setattr(atspi_helper, "_a11y_bus", lambda deadline: None)
    assert atspi_helper.focused_is_password() is False


def test_a_bus_that_errors_means_typing_as_usual(monkeypatch):
    def broken(deadline):
        raise GLib.Error("The name org.a11y.Bus was not provided by any .service files")
    monkeypatch.setattr(atspi_helper, "_a11y_bus", broken)
    assert atspi_helper.focused_is_password() is False


def test_an_answer_in_an_unexpected_shape_means_typing_as_usual(bus, monkeypatch):
    bus.app("gedit", PASSWORD)
    monkeypatch.setattr(atspi_helper, "_states", lambda *a: "not a number")
    assert atspi_helper.focused_is_password() is False


def test_a_hung_app_is_skipped_in_time(bus):
    """An app that never answers costs one call's timeout, then the next
    app is asked."""
    bus.app("frozen", TEXT, hang=True)
    bus.app("gedit", PASSWORD)
    started = time.monotonic()
    assert atspi_helper.focused_is_password() is True
    assert time.monotonic() - started < atspi_helper.BUDGET_S


def test_a_bus_where_everything_hangs_gives_up_within_the_budget(bus, monkeypatch):
    for n in range(5):
        bus.app(f"frozen{n}", PASSWORD, hang=True)
    started = time.monotonic()
    assert atspi_helper.focused_is_password() is False
    took = time.monotonic() - started
    assert took < atspi_helper.BUDGET_S + atspi_helper.CALL_TIMEOUT_MS / 1000


def test_a_slow_search_isnt_followed_by_a_walk_of_the_same_app(bus):
    bus.app("slowpoke", PASSWORD, collection="slow")
    assert atspi_helper.focused_is_password() is False
    assert ("GetChildren" not in
            [method for name, path, method in bus.calls if path == "/1"])


def test_every_active_window_is_asked(bus, caplog):
    """A desktop shell that calls its own window active too must not hide
    the app the focus is really in."""
    bus.app("cinnamon", TEXT)
    bus.app("gedit", PASSWORD)
    with caplog.at_level(logging.INFO, logger="tuxtalk.atspi_helper"):
        assert atspi_helper.focused_is_password() is True
    assert "several apps" in caplog.text


def test_gnome_shells_own_password_prompt_counts(bus):
    """The password prompt GNOME shows for pkexec, the keyring or Wi-Fi is
    part of gnome-shell, and TuxTalk typed into it (Ubuntu VM, 2026-10-22)."""
    bus.app("gedit", TEXT)
    bus.app("gnome-shell", PASSWORD)
    assert atspi_helper.focused_is_password() is True


def test_gnome_shell_beside_an_app_is_not_several_apps(bus, caplog):
    """Its stage always says it's active, which says nothing about the focus."""
    bus.app("gnome-shell", TEXT)
    bus.app("gedit", TEXT)
    with caplog.at_level(logging.INFO, logger="tuxtalk.atspi_helper"):
        assert atspi_helper.focused_is_password() is False
    assert "several apps" not in caplog.text


@pytest.fixture
def no_connection_yet(monkeypatch):
    monkeypatch.setattr(atspi_helper, "_bus", None)
    monkeypatch.setattr(atspi_helper, "_connecting", False)


def test_a_bus_that_never_finishes_connecting_doesnt_hold_up_typing(no_connection_yet,
                                                                     monkeypatch):
    """Opening the connection can hang on D-Bus's 25 s handshake timeout."""
    def stuck():
        time.sleep(1)
    monkeypatch.setattr(atspi_helper, "_open_bus", stuck)
    started = time.monotonic()
    assert atspi_helper.focused_is_password() is False
    assert time.monotonic() - started < atspi_helper.BUDGET_S + 0.1
    # While it's still trying, the next question doesn't wait at all.
    started = time.monotonic()
    assert atspi_helper.focused_is_password() is False
    assert time.monotonic() - started < 0.05


def test_the_first_question_waits_for_a_quick_connection(no_connection_yet, monkeypatch):
    fake = FakeA11yBus()
    fake.app("gedit", PASSWORD)
    fake.is_closed = lambda: False
    monkeypatch.setattr(atspi_helper, "_open_bus", lambda: fake)
    assert atspi_helper.focused_is_password() is True
