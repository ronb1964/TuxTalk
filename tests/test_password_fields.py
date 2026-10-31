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

from tuxtalk import (atspi_helper, delivery_notices, focused_window, history,
                     screen_message, send_countdown)
from tuxtalk import recording_state as rs
from tuxtalk import live_settings as live
from tuxtalk import keyboard_output
from tuxtalk import engine
from tuxtalk import gtk_loop
from tuxtalk import dictation_text
from tuxtalk import delivery
from tuxtalk import type_at_pause
from tuxtalk import pipeline
from tuxtalk.beeps import CANCEL_BEEP
from tuxtalk import hotkeys

ACTIVE = 1 << atspi_helper._STATE_ACTIVE
FOCUSED = 1 << atspi_helper._STATE_FOCUSED
SHOWING = 1 << atspi_helper._STATE_SHOWING
PASSWORD = atspi_helper._ROLE_PASSWORD_TEXT
TEXT = 61      # ATSPI_ROLE_TEXT
# Taken before conftest swaps in a stand-in, which every test gets.
REAL_PROMPT_WAITING = focused_window._polkit_prompt_waiting


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

    monkeypatch.setattr(keyboard_output, "output_backend", lambda: Keyboard())
    monkeypatch.setattr(delivery, "determine_injection_method", lambda mode: ("type", "test"))
    monkeypatch.setattr(keyboard_output, "paste_text",
                        lambda text, *a, **k: seen["pasted"].append(text) or True)
    monkeypatch.setattr(history, "add_entry",
                        lambda text, replaces=None: seen["history"].append(text))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: seen["notes"].append(body))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda on, *tone, **k: on and seen["beeps"].append(tone))
    monkeypatch.setattr(screen_message, "show_over_active_window",
                        lambda text, **k: seen["labels"].append(text))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    monkeypatch.setattr(live, "usage_stats", False, raising=False)
    monkeypatch.setattr(atspi_helper, "focused_is_password",
                        lambda *a, **k: (seen["answers"].pop(0) if seen["answers"]
                                         else seen["password"]))
    return seen


def dictate(monkeypatch, said="my secret is swordfish", beeps_on=True, chunk_role=None):
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: said)
    frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    pipeline.transcribe_and_inject(frames, engine.SAMPLE_RATE, beeps_on=beeps_on,
                               smart_quotes=False, notify_on=True, chunk_role=chunk_role,
                               message=rs.state.current.message)


def test_a_dictation_in_a_password_field_is_not_typed_or_kept(desktop, monkeypatch, caplog):
    desktop["password"] = True
    with caplog.at_level(logging.INFO, logger="tuxtalk.app"):
        dictate(monkeypatch)
    assert desktop["typed"] == [] and desktop["history"] == []
    # Not even in the "Transcribed: …" notification.
    assert not any("swordfish" in n for n in desktop["notes"])
    assert desktop["beeps"] == [CANCEL_BEEP]
    assert desktop["labels"] == [delivery_notices.PASSWORD_FIELD_LABEL]
    assert "password field" in caplog.text and "swordfish" not in caplog.text


def test_a_dictation_anywhere_else_is_typed_and_kept(desktop, monkeypatch):
    dictate(monkeypatch)
    assert len(desktop["typed"]) == 1 and "swordfish" in desktop["typed"][0]
    assert len(desktop["history"]) == 1
    assert desktop["labels"] == []


def test_beep_and_label_say_it_without_a_notification(desktop, monkeypatch):
    monkeypatch.setattr(gtk_loop, "_thread", object())       # the label can show
    monkeypatch.setenv("DISPLAY", ":0")
    desktop["password"] = True
    dictate(monkeypatch)
    assert desktop["notes"] == []


@pytest.mark.parametrize("beeps_on, label_can_show", [(False, True), (True, False)])
def test_a_notification_says_it_when_beep_or_label_cant(desktop, monkeypatch, beeps_on,
                                                        label_can_show):
    monkeypatch.setattr(gtk_loop, "_thread", object() if label_can_show else None)
    monkeypatch.setenv("DISPLAY", ":0")
    desktop["password"] = True
    dictate(monkeypatch, beeps_on=beeps_on)
    assert desktop["notes"] == [delivery_notices.PASSWORD_FIELD_NOTICE]


def test_type_at_pause_types_nothing_of_its_own_into_a_password_field(desktop, monkeypatch):
    """The mark held back from the phrase before, a held "No wait." and the
    local AI all come before the words; none of them reach the field."""
    ran = []
    monkeypatch.setattr(type_at_pause, "resolve_boundary", lambda *a, **k: ran.append("mark"))
    monkeypatch.setattr(type_at_pause, "flush_boundary", lambda *a, **k: ran.append("mark"))
    monkeypatch.setattr(dictation_text, "prepare_text", lambda *a, **k: ran.append("cleanup") or "x")
    monkeypatch.setattr(rs.state.current.message, "held_mark", {"mark": "."})
    monkeypatch.setattr(rs.state.current.message, "held_cue", "No wait.")
    desktop["password"] = True
    dictate(monkeypatch, chunk_role="middle")
    assert desktop["typed"] == [] and ran == []
    assert rs.state.current.message.held_mark is None and rs.state.current.message.held_cue == ""
    assert desktop["labels"] == [delivery_notices.PASSWORD_FIELD_LABEL]


def test_saying_only_press_enter_still_presses_enter_there(desktop, monkeypatch):
    """Type the password by hand, then say "press enter" to log in."""
    pressed = []
    monkeypatch.setattr(keyboard_output, "press_key", lambda key, why, claim=None: pressed.append(why) or True)
    desktop["password"] = True
    dictate(monkeypatch, said="press enter")
    assert pressed and desktop["typed"] == [] and desktop["labels"] == []


def test_saying_only_press_tab_still_presses_tab_there(desktop, monkeypatch):
    """Move from the password box to the next one by voice."""
    pressed = []
    monkeypatch.setattr(keyboard_output, "press_key", lambda key, why, claim=None: pressed.append(key) or True)
    desktop["password"] = True
    dictate(monkeypatch, said="Press tab.")
    assert pressed == ["TAB"] and desktop["typed"] == [] and desktop["labels"] == []


def test_words_before_press_tab_are_not_typed_there(desktop, monkeypatch):
    pressed = []
    monkeypatch.setattr(keyboard_output, "press_key", lambda key, why, claim=None: pressed.append(key) or True)
    desktop["password"] = True
    dictate(monkeypatch, said="swordfish press tab")
    assert pressed == [] and desktop["typed"] == [] and desktop["history"] == []
    assert desktop["labels"] == [delivery_notices.PASSWORD_FIELD_LABEL]


def test_paste_last_dictation_types_nothing_in_a_password_field(desktop, monkeypatch):
    monkeypatch.setattr(history, "get_entries", lambda: ["my secret is swordfish"])
    cfg = types.SimpleNamespace(beeps=True, auto_space=True, injection_mode="type")
    desktop["password"] = True
    hotkeys._paste_last_dictation(cfg)
    assert desktop["typed"] == []
    assert desktop["labels"] == [delivery_notices.PASSWORD_FIELD_LABEL]
    desktop["password"] = False
    hotkeys._paste_last_dictation(cfg)
    assert desktop["typed"] == ["my secret is swordfish "]


def _pasted_with_real_keys(desktop):
    """paste_text with the clipboard faked and the paste key really sent
    (the recording keyboard), so the checks right before it are made."""
    def paste(text):
        desktop["pasted"].append(text)
        return keyboard_output.send_paste_keystroke(focused_window.window_class())
    return paste


def _focus_moves_into_a_password_field_after_the_first_line(desktop, monkeypatch):
    """The first wait after the first line (the paste settling, or the
    pause before its line break) is when the user tabs into a password box."""
    monkeypatch.setattr("time.sleep", lambda s: desktop.__setitem__("password", True))


def test_delivery_stops_when_the_focus_moves_into_a_password_field(desktop, monkeypatch,
                                                                    keyboard):
    """Pasted line by line, the line break went into the password field
    before it was asked about again: in a login form it can submit it."""
    from evdev import ecodes
    monkeypatch.setattr(delivery, "determine_injection_method", lambda mode: ("paste", "test"))
    monkeypatch.setattr(keyboard_output, "paste_text", _pasted_with_real_keys(desktop))
    _focus_moves_into_a_password_field_after_the_first_line(desktop, monkeypatch)
    assert delivery.inject_text("first line\nsecond line", "auto") == "password_field"
    assert desktop["pasted"] == ["first line"] and desktop["typed"] == []
    assert ecodes.KEY_ENTER not in keyboard.typed_keys()


def test_a_paste_stopped_there_isnt_finished_by_typing(desktop, monkeypatch, keyboard):
    """Not asked a second time: a slow app whose answer runs out of time says
    "no", and the rest of the dictation was then typed into the field."""
    monkeypatch.setattr(delivery, "determine_injection_method", lambda mode: ("paste", "test"))
    monkeypatch.setattr(keyboard_output, "paste_text", _pasted_with_real_keys(desktop))
    monkeypatch.setattr("time.sleep", lambda s: None)
    # Before it, before the first paste key, then before the line break;
    # then "no" to any further question.
    desktop["answers"] = [False, False, True]
    assert delivery.inject_text("first line\nsecond line", "auto") == "password_field"
    assert desktop["pasted"] == ["first line"] and desktop["typed"] == []


def test_typed_line_by_line_it_stops_there_too(desktop, monkeypatch, keyboard):
    """X11 types every dictation, and its Shift+Enter went into the password
    field before the next line was asked about."""
    from evdev import ecodes
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    monkeypatch.setattr(keyboard_output, "output_backend", VirtualKeyboardOutputBackend)
    _focus_moves_into_a_password_field_after_the_first_line(desktop, monkeypatch)
    assert delivery.inject_text("ab\ncd", "auto") == "password_field"
    assert keyboard.typed_keys() == [ecodes.KEY_A, ecodes.KEY_B]
    assert rs.state.last_inserted_text == ""      # undo deletes nothing it can't count


def test_the_first_line_is_refused_when_the_focus_moved_while_it_was_made_ready(
        desktop, monkeypatch, keyboard):
    """Asked before the clipboard and the keys were made ready, then not
    again: the user tabbing into a password box meanwhile got the text."""
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    monkeypatch.setattr(keyboard_output, "output_backend", VirtualKeyboardOutputBackend)
    desktop["answers"], desktop["password"] = [False], True
    assert delivery.inject_text("swordfish", "auto") == "password_field"
    assert keyboard.events == []


def test_a_dictation_refused_at_the_last_moment_leaves_recent_dictations(desktop, monkeypatch):
    """It goes into Recent Dictations before it's typed; refused then, it comes back out."""
    forgotten = []
    monkeypatch.setattr(history, "forget", lambda text: forgotten.append(text))
    desktop["answers"] = [False, False, True]    # before the AI, before the beep, at delivery
    dictate(monkeypatch)
    assert desktop["typed"] == []
    assert len(forgotten) == 1 and "swordfish" in forgotten[0]


def test_sending_automatically_doesnt_press_enter_in_a_password_field(desktop, monkeypatch,
                                                                     keyboard):
    """Dictate a username, tab into the password box during the wait: Enter
    there would submit the login form."""
    from tuxtalk.output_backends import VirtualKeyboardOutputBackend
    monkeypatch.setattr(keyboard_output, "output_backend", VirtualKeyboardOutputBackend)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(focused_window, "look", lambda: ("firefox", ("tray", "{f1}")))
    pending = {"to": focused_window.look(), "why": "sending automatically",
               "timer": None, "ticks": [], "press": keyboard_output.press_key}
    monkeypatch.setattr(send_countdown, "_pending", pending)
    desktop["password"] = True
    send_countdown._send(pending)
    assert keyboard.events == [] and desktop["labels"] == [send_countdown.PASSWORD_FIELD_NOT_SENT]
    desktop["password"], desktop["labels"] = False, []
    pending = dict(pending, refused=None)
    send_countdown._pending = pending
    send_countdown._send(pending)
    assert keyboard.typed_keys() == [28]          # KEY_ENTER


def test_no_wait_doesnt_backspace_in_a_password_field(desktop, monkeypatch):
    """The AI behind "no wait" can take seconds after the first check."""
    erased = []
    monkeypatch.setattr(keyboard_output, "output_backend",
                        lambda: types.SimpleNamespace(backspaces=lambda n: erased.append(n)))
    monkeypatch.setattr(rs.state, "last_inserted_window", None)
    desktop["password"] = True
    assert type_at_pause._erase_typed("Meet at three. ") is False
    assert erased == []


def test_fix_a_word_doesnt_touch_a_password_field(desktop, monkeypatch):
    from tuxtalk import fix_word_dialog
    keys = []
    monkeypatch.setattr(fix_word_dialog, "_REFOCUS_S", 0)
    monkeypatch.setattr(keyboard_output, "output_backend",
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
    monkeypatch.setattr(focused_window, "_polkit_prompt_waiting", lambda: waiting)
    monkeypatch.setattr(focused_window, "window_class", lambda: focused)
    dictate(monkeypatch)
    assert (desktop["typed"] == []) is refused


def test_with_no_prompt_waiting_the_focused_window_isnt_asked(desktop, monkeypatch):
    """The check runs before every typed line; asking the tray which window
    has the focus each time would slow typing down for nothing."""
    asked = []
    monkeypatch.setattr(focused_window, "_polkit_prompt_waiting", lambda: False)
    monkeypatch.setattr(focused_window, "window_class", lambda: asked.append(1))
    assert focused_window._password_prompt_has_focus() is False
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


def test_hung_apps_after_the_password_field_dont_hide_it(bus):
    """Every app was listed before any focus was looked at, so three apps
    that never answer used up the time, and the dictation was typed into
    the password field found first."""
    bus.app("seahorse", PASSWORD)
    for n in range(3):
        bus.app(f"frozen{n}", TEXT, hang=True)
    assert atspi_helper.focused_is_password() is True


def test_a_bus_where_everything_hangs_gives_up_within_the_budget(bus, monkeypatch):
    for n in range(5):
        bus.app(f"frozen{n}", PASSWORD, hang=True)
    started = time.monotonic()
    assert atspi_helper.focused_is_password() is False
    took = time.monotonic() - started
    assert took < atspi_helper.BUDGET_S + atspi_helper.CALL_TIMEOUT_MS / 1000


def test_a_dictations_check_before_each_line_gives_up_sooner(bus, monkeypatch):
    """Asked again before every line, line break and paste key, a bus that
    hangs held each of them up by the whole quarter second: 20 lines of a
    dictation took 10 s more."""
    for n in range(5):
        bus.app(f"frozen{n}", PASSWORD, hang=True)
    monkeypatch.setattr(focused_window, "window_class", lambda: "org.kde.kwrite")
    started = time.monotonic()
    keyboard_output.refuse_password_field()          # no answer in time: typing as usual
    took = time.monotonic() - started
    assert took < atspi_helper.RECHECK_BUDGET_S + atspi_helper.CALL_TIMEOUT_MS / 1000
    assert atspi_helper.RECHECK_BUDGET_S <= atspi_helper.BUDGET_S / 4


def test_a_password_field_left_in_a_background_app_doesnt_refuse_the_dictation(bus):
    """A browser behind KWrite still said its window was active, with its
    login box focused: every dictation into KWrite was refused."""
    bus.app("Firefox", PASSWORD)
    bus.app("kwrite", TEXT)
    assert atspi_helper.focused_is_password(focused_class=lambda: "org.kde.kwrite") is False


def test_the_focused_apps_own_password_field_still_counts(bus):
    bus.app("kwrite", TEXT)
    bus.app("Firefox", PASSWORD)
    assert atspi_helper.focused_is_password(focused_class=lambda: "firefox") is True


@pytest.mark.parametrize("focused", [
    "com.brave.Browser",    # the Flatpak, on the bus as "Brave": it may be the one
    None,                   # the desktop can't say which window has the focus
])
def test_a_password_field_counts_where_the_focused_app_cant_be_told_apart(bus, focused):
    bus.app("Brave", PASSWORD)
    bus.app("kwrite", TEXT)
    assert atspi_helper.focused_is_password(focused_class=lambda: focused) is True


@pytest.mark.parametrize("budget", [atspi_helper.BUDGET_S, atspi_helper.RECHECK_BUDGET_S])
def test_a_password_field_found_still_counts_when_later_apps_use_up_the_time(bus, budget):
    """Brave's login box had the focus, and apps that hang after it used up
    the time: the password field already found was forgotten, and the
    dictation typed into it. Before the first line and before each line."""
    bus.app("Chromium", PASSWORD)
    for n in range(5):
        bus.app(f"frozen{n}", TEXT, hang=True)
    assert atspi_helper.focused_is_password(budget, lambda: "org.kde.kwrite") is True


def test_the_focused_apps_no_still_clears_a_background_one_when_the_time_runs_out(bus):
    bus.app("Firefox", PASSWORD)
    bus.app("kwrite", TEXT)
    for n in range(5):
        bus.app(f"frozen{n}", TEXT, hang=True)
    assert atspi_helper.focused_is_password(focused_class=lambda: "org.kde.kwrite") is False


def test_a_focused_app_that_doesnt_answer_hasnt_said_no(bus):
    """Only an answer clears another app's password field."""
    bus.app("Firefox", PASSWORD)
    bus.app("kwrite", TEXT, collection="slow")
    assert atspi_helper.focused_is_password(focused_class=lambda: "org.kde.kwrite") is True


def test_a_slow_tray_doesnt_hold_up_the_check_before_each_line(bus):
    """Which app has the focus comes from the tray, which has 1.5 s when
    its loop is busy: the 50 ms check before each line took that long."""
    import threading
    answered = threading.Event()

    def slow_tray():
        answered.wait(1.5)
        return "org.kde.kwrite"
    bus.app("Firefox", PASSWORD)
    started = time.monotonic()
    try:
        # Not told in time: the password field may be in the focused app.
        assert atspi_helper.focused_is_password(atspi_helper.RECHECK_BUDGET_S, slow_tray) is True
        assert time.monotonic() - started < 0.5
    finally:
        answered.set()
        atspi_helper._looks.submit(lambda: None).result()    # the next test's look goes first


def test_brave_on_the_bus_is_the_brave_browser_window(bus):
    bus.app("Brave", TEXT)
    bus.app("Firefox", PASSWORD)
    assert atspi_helper.focused_is_password(focused_class=lambda: "brave-browser") is False


def test_the_shells_own_password_prompt_counts_over_any_app(bus):
    bus.app("gedit", TEXT)
    bus.app("gnome-shell", PASSWORD)
    assert atspi_helper.focused_is_password(focused_class=lambda: "org.gnome.gedit") is True


def test_the_focused_window_is_asked_only_once_a_password_field_is_found(bus):
    """A look at it for every check would slow every line down for nothing."""
    looks = []
    bus.app("gedit", TEXT)
    bus.app("kwrite", TEXT)
    assert atspi_helper.focused_is_password(focused_class=lambda: looks.append(1)) is False
    assert looks == []


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
