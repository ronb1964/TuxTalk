"""An automatic Enter goes only where the dictation's words went
(send_countdown's one rule).

"Send each dictation automatically" presses Enter by itself. Into another
window it sends whatever is in that box or runs the command on a terminal's
line, and two windows of one app (two Brave windows) look the same by
their class. These pin what someone would see: where the words were typed,
and whether an Enter followed them there or anywhere else.
"""
import threading
import types

import numpy as np
import pytest
from evdev import ecodes

from tuxtalk import (app, atspi_helper, compositor_focus, dbus_service, focused_window, history,
                     modifier_hotkeys, send_countdown)

ENTER, RCTRL, F8, F9 = ecodes.KEY_ENTER, ecodes.KEY_RIGHTCTRL, ecodes.KEY_F8, ecodes.KEY_F9
# Taken before conftest runs "Right away" on the test's own thread.
SEND_RIGHT_AWAY = send_countdown._send_right_away
FIRST = ("brave-browser", ("tray", "{5c1d0e2a}"))
SECOND = ("brave-browser", ("tray", "{9f04b7c3}"))      # another window, same app


@pytest.fixture
def chat(monkeypatch, keyboard):
    """Dictation into a chat window with "Send each dictation automatically"
    on, sent right away. *focus* is the window that has the focus; typed
    holds (text, window it went into); notices what floated over it."""
    focus = list(FIRST)
    heard, typed, notices = [], [], []
    monkeypatch.setattr(focused_window, "look", lambda: tuple(focus))
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: focus[0])

    def deliver(text, mode, t0, one_block=False, *, where):
        typed.append((text, focus[1]))
        app.state.last_inserted_text = text
        return "ok"
    monkeypatch.setattr(app, "_deliver_text", deliver)
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, language: heard.pop(0))
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_press_enter_after", True)
    monkeypatch.setattr(app, "_usage_stats", False)
    monkeypatch.setattr(app.state, "chunk_typed", False)
    monkeypatch.setattr(app.state, "pending_boundary", None)
    monkeypatch.setattr(history, "add_entry", lambda text, replaces=None: None)
    monkeypatch.setattr(send_countdown, "show_notice", notices.append)
    return focus, heard, typed, notices


def dictate(chunk_role=None, audio=True):
    frames = [(np.ones(app.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()] if audio else []
    app._transcribe_and_inject(frames, app.SAMPLE_RATE, beeps_on=False, smart_quotes=False,
                               notify_on=False, chunk_role=chunk_role)


def test_a_dictation_sends_into_the_window_it_went_to(chat, keyboard):
    focus, heard, typed, notices = chat
    heard.append("see you at noon")
    dictate()
    assert typed == [("See you at noon.", FIRST[1])]
    assert keyboard.typed_keys() == [ENTER] and notices == []


def test_a_window_switch_while_the_words_go_in_sends_nothing(chat, keyboard, monkeypatch):
    """A long dictation, or a slow paste: the user clicked into the other
    Brave window while it went in. The look taken after it said the second
    window, and the Enter went there."""
    focus, heard, typed, notices = chat
    deliver = app._deliver_text

    def clicked_away_meanwhile(*a, **k):
        outcome = deliver(*a, **k)
        focus[:] = SECOND
        return outcome
    monkeypatch.setattr(app, "_deliver_text", clicked_away_meanwhile)
    heard.append("see you at noon")
    dictate()
    assert typed == [("See you at noon.", FIRST[1])]                 # the words stay
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


def test_no_countdown_after_a_switch_while_the_words_went_in(chat, keyboard, monkeypatch):
    focus, heard, typed, notices = chat
    started = []
    monkeypatch.setattr(send_countdown, "_delay", 2.0)
    monkeypatch.setattr(send_countdown.threading, "Timer",
                        lambda *a, **k: started.append(a) or pytest.fail("a countdown"))
    deliver = app._deliver_text

    def clicked_away_meanwhile(*a, **k):
        outcome = deliver(*a, **k)
        focus[:] = SECOND
        return outcome
    monkeypatch.setattr(app, "_deliver_text", clicked_away_meanwhile)
    heard.append("see you at noon")
    dictate()
    assert started == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


def test_a_pause_then_another_window_of_the_app_gets_neither_mark_nor_enter(chat, keyboard,
                                                                            monkeypatch):
    """Type at pause: "See you at noon" typed into one Brave window, its
    period held back for the next phrase; a click into the other Brave
    window; the recording ends in that pause. The period went into the other
    window, and the Enter followed it there, sending what was in its box."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(app, "_chunk_context_marks", True)
    heard.append("See you at noon.")
    dictate("middle")
    assert typed == [("See you at noon", FIRST[1])]
    focus[:] = SECOND
    dictate("last", audio=False)
    assert typed == [("See you at noon", FIRST[1])]
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


def test_a_pause_at_the_end_in_the_same_window_types_the_mark_and_sends(chat, keyboard,
                                                                        monkeypatch):
    focus, heard, typed, notices = chat
    monkeypatch.setattr(app, "_chunk_context_marks", True)
    heard.append("See you at noon.")
    dictate("middle")
    dictate("last", audio=False)
    assert typed == [("See you at noon", FIRST[1]), (".", FIRST[1])]
    assert keyboard.typed_keys() == [ENTER] and notices == []


def test_the_mark_typed_at_the_end_doesnt_aim_the_enter_elsewhere(chat, keyboard,
                                                                  monkeypatch):
    """The held mark is typed where the focus is now, which the desktop
    can't tell from the phrase's window (only the app is known). Its own
    delivery must not become where the Enter goes."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(app, "_chunk_context_marks", True)
    heard.append("See you at noon.")
    dictate("middle")
    focus[1] = None                                     # the tray didn't answer
    deliver = app._deliver_text

    def mark_then_switch(*a, **k):
        outcome = deliver(*a, **k)
        focus[:] = SECOND                               # clicked away as it went in
        return outcome
    monkeypatch.setattr(app, "_deliver_text", mark_then_switch)
    dictate("last", audio=False)
    assert typed[-1] == (".", None)
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


# --- "Right away": the same claim as after a countdown ---------------------------------

class Keyboard:
    """A real keyboard as the hotkey loop sees it, Right Ctrl down while
    *pressed(look)* says so."""

    def __init__(self, pressed):
        self.pressed, self.looks = pressed, 0

    def capabilities(self, verbose=False):
        return {ecodes.EV_KEY: [ecodes.KEY_A, ecodes.KEY_M, ecodes.KEY_Z]}

    def active_keys(self):
        self.looks += 1
        return [RCTRL] if self.pressed(self.looks) else []


@pytest.fixture
def right_ctrl_toggle(monkeypatch):
    """Right Ctrl is the toggle key, still down as a send is due."""
    monkeypatch.setattr(modifier_hotkeys, "_noted", ([], frozenset()))
    monkeypatch.setattr(modifier_hotkeys.time, "sleep", lambda s: None)
    return lambda pressed: modifier_hotkeys.note([Keyboard(pressed)], F8, RCTRL)


def test_right_away_checks_the_window_once_the_key_is_up(chat, keyboard, right_ctrl_toggle):
    """Clicked into the other Brave window as the Enter was due, Right Ctrl
    still down: the window was checked before the wait, and the wait only
    compared the window at its start with the one at its end."""
    focus, heard, typed, notices = chat

    def pressed(look):
        focus[:] = SECOND
        return look <= 3
    right_ctrl_toggle(pressed)
    send_countdown.send_automatically("test", focused_window.Destination(*FIRST),
                                      app._press_key, app._type_onto_dictation)
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


def test_right_away_a_tap_while_the_enter_waits_holds_it(chat, keyboard, right_ctrl_toggle):
    """The hotkey loop reads a tap of the Right Ctrl toggle while the Enter
    waits for the key. Before, the send wasn't pending any more by then,
    and the tap held nothing."""
    held = []

    def pressed(look):
        if look == 2:
            held.append(send_countdown.hold(announce=True))
        return look <= 3
    right_ctrl_toggle(pressed)
    send_countdown.send_automatically("test", focused_window.Destination(*FIRST),
                                      app._press_key, app._type_onto_dictation)
    assert held == [True] and keyboard.events == []


def test_right_away_a_tap_the_hotkey_loop_reads_holds_the_enter(chat, keyboard, monkeypatch):
    """The words are typed on the hotkey loop's thread, then the Enter's
    checks run. Run there too, the loop read the tap of F9 only once the
    Enter had gone out."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(send_countdown, "_send_right_away", SEND_RIGHT_AWAY)
    words_in, tap_read = threading.Event(), threading.Event()
    deliver = app._deliver_text

    def deliver_then_note(*a, **k):
        outcome = deliver(*a, **k)
        words_in.set()
        return outcome
    monkeypatch.setattr(app, "_deliver_text", deliver_then_note)
    monkeypatch.setattr(atspi_helper, "focused_is_password",
                        lambda: words_in.is_set() and tap_read.wait(2) and False)
    heard.append("see you at noon")
    dictate()
    tap = types.SimpleNamespace(type=ecodes.EV_KEY, code=F9, value=1)
    app._handle_key_event(tap, F8, F9, None, set(), [], None, None)
    tap_read.set()
    for thread in threading.enumerate():
        if thread.name == "send-right-away":
            thread.join(5)
    assert keyboard.events == [] and notices == [send_countdown.HELD_NOTICE]


def test_a_click_during_the_password_check_sends_nothing(chat, keyboard, monkeypatch):
    """The accessibility bus can take a quarter of a second to say whether
    the cursor is in a password field. Clicked into the other Brave window
    meanwhile: the window looked at before the check got the Enter."""
    focus, _, _, notices = chat

    def slow_check():
        focus[:] = SECOND
        return False
    monkeypatch.setattr(atspi_helper, "focused_is_password", slow_check)
    send_countdown.send_automatically("test", focused_window.Destination(*FIRST),
                                      app._press_key, app._type_onto_dictation)
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


def test_a_click_back_during_the_password_check_sends_nothing(chat, keyboard, monkeypatch):
    """In the other window as the Enter was due, then back into the chat
    during the password check: the check may have been asked about the
    other window, so the chat's field isn't known not to be a password's."""
    focus, _, _, notices = chat
    focus[:] = SECOND

    def slow_check():
        focus[:] = FIRST
        return False
    monkeypatch.setattr(atspi_helper, "focused_is_password", slow_check)
    send_countdown.send_automatically("test", focused_window.Destination(*FIRST),
                                      app._press_key, app._type_onto_dictation)
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


def test_right_away_into_the_other_window_of_the_app_sends_nothing(chat, keyboard):
    focus, _, _, notices = chat
    focus[:] = SECOND
    send_countdown.send_automatically("test", focused_window.Destination(*FIRST),
                                      app._press_key, app._type_onto_dictation)
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


# --- TuxTalk's own windows ------------------------------------------------------------------

@pytest.fixture
def tray(monkeypatch):
    """The tray's real focus cache, told the focused window as the KWin
    script and the GNOME extension tell it, and asked as the service asks."""
    monkeypatch.setattr(dbus_service, "_focus", dbus_service.FocusReport())
    monkeypatch.setattr(dbus_service, "_clipboard_tool_focused", False)
    monkeypatch.setattr(focused_window, "reported", lambda: tuple(dbus_service.cached_focus()))
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: (None, None))
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    service = object.__new__(dbus_service.TuxTalkDBusService)
    return lambda wm_class, window_id: dbus_service.TuxTalkDBusService.SetFocusedWindow(
        service, wm_class, window_id, "")


@pytest.mark.parametrize("dictated_into", ["org.kde.kwrite", "tuxtalk"])
def test_no_enter_goes_into_a_tuxtalk_window(tray, keyboard, monkeypatch, dictated_into):
    """On KDE or GNOME: Preferences opened during the countdown, or the
    Parakeet offer came up by itself, and the Enter pressed its default
    button (OK applied the settings). Or the words went into one of
    TuxTalk's own boxes."""
    notices = []
    monkeypatch.setattr(send_countdown, "show_notice", notices.append)
    tray(dictated_into, "{5c1d0e2a}")
    sent_to = focused_window.destination()
    tray("tuxtalk", "{77a0c4e1}")
    send_countdown.send_automatically("test", sent_to, app._press_key, app._type_onto_dictation)
    assert keyboard.events == [] and notices == [send_countdown.OWN_WINDOW_NOTICE]


# --- Where the desktop can't say which window: the open decision -------------------------

@pytest.mark.parametrize("window", [None, ("frame", "0,0,1920,1050|DP-1|0,0,1920,1080")])
def test_where_only_the_app_is_known_it_sends_as_before(chat, keyboard, window):
    """COSMIC (only the app), or GNOME with 0.18's extension still loaded
    (the app and its frame): until it's decided otherwise, it sends."""
    focus, _, _, notices = chat
    focus[1] = window
    assert send_countdown.SEND_WHEN_ONLY_THE_APP_IS_KNOWN is True
    send_countdown.send_automatically("test", focused_window.destination(),
                                      app._press_key, app._type_onto_dictation)
    assert keyboard.typed_keys() == [ENTER] and notices == []


@pytest.mark.parametrize("window", [None, ("frame", "0,0,1920,1050|DP-1|0,0,1920,1080")])
def test_set_not_to_send_there_nothing_is_sent_and_it_says_why(chat, keyboard, monkeypatch,
                                                               window):
    focus, _, _, notices = chat
    focus[1] = window
    monkeypatch.setattr(send_countdown, "SEND_WHEN_ONLY_THE_APP_IS_KNOWN", False)
    monkeypatch.setattr(send_countdown, "_delay", 2.0)
    monkeypatch.setattr(send_countdown.threading, "Timer",
                        lambda *a, **k: pytest.fail("counted down to a send that can't go"))
    send_countdown.send_automatically("test", focused_window.destination(),
                                      app._press_key, app._type_onto_dictation)
    assert keyboard.events == [] and notices == [send_countdown.UNKNOWN_WINDOW_NOTICE]


def test_set_not_to_send_there_a_known_window_still_sends(chat, keyboard, monkeypatch):
    monkeypatch.setattr(send_countdown, "SEND_WHEN_ONLY_THE_APP_IS_KNOWN", False)
    send_countdown.send_automatically("test", focused_window.destination(),
                                      app._press_key, app._type_onto_dictation)
    assert keyboard.typed_keys() == [ENTER]


def test_set_not_to_send_there_a_tray_too_busy_to_answer_stops_the_enter(chat, keyboard,
                                                                         monkeypatch):
    """The window was known when the words went in; at the Enter, nothing
    can say it's still that one."""
    focus, _, _, notices = chat
    monkeypatch.setattr(send_countdown, "SEND_WHEN_ONLY_THE_APP_IS_KNOWN", False)
    sent_to = focused_window.destination()
    focus[:] = [None, None]
    send_countdown.send_automatically("test", sent_to, app._press_key, app._type_onto_dictation)
    assert keyboard.events == [] and notices == [send_countdown.UNKNOWN_WINDOW_NOTICE]


# --- "... press enter" said after the words: the same rule ---------------------------------

def clicked_away_while_the_words_went_in(monkeypatch, focus, to=SECOND):
    deliver = app._deliver_text

    def deliver_then_switch(*a, **k):
        outcome = deliver(*a, **k)
        focus[:] = to
        return outcome
    monkeypatch.setattr(app, "_deliver_text", deliver_then_switch)


@pytest.mark.parametrize("key", ["enter", "tab"])
def test_a_key_said_after_the_words_goes_where_they_went(chat, keyboard, key):
    focus, heard, typed, notices = chat
    heard.append(f"see you at noon, press {key}")
    dictate()
    assert typed[0][1] == FIRST[1]
    assert keyboard.typed_keys() == [getattr(ecodes, f"KEY_{key.upper()}")] and notices == []


@pytest.mark.parametrize("key", ["enter", "tab"])
def test_a_switch_while_the_words_go_in_keeps_back_the_key_said_after_them(chat, keyboard,
                                                                           monkeypatch, key):
    """"See you at noon, press enter", and a click into the other Brave
    window as the words went in: the Enter sent what was in that window's box."""
    focus, heard, typed, notices = chat
    clicked_away_while_the_words_went_in(monkeypatch, focus)
    heard.append(f"see you at noon, press {key}")
    dictate()
    assert typed[0][1] == FIRST[1] and keyboard.events == []
    assert len(notices) == 1


def test_a_switch_after_the_words_keeps_back_the_enter_said_after_them(chat, keyboard,
                                                                       monkeypatch):
    """The click comes after the words are in, in the moment the app is
    given to take them in before the Enter."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(app.time, "sleep", lambda s: focus.__setitem__(slice(None), SECOND))
    heard.append("see you at noon, press enter")
    dictate()
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


def test_press_enter_said_alone_goes_wherever_the_focus_is(chat, keyboard):
    """A password typed by hand, then "press enter" to log in: nothing was
    dictated there, so there's no window it must match."""
    focus, heard, typed, notices = chat
    heard.append("see you at noon")
    dictate()
    focus[:] = SECOND
    heard.append("press enter")
    dictate()
    assert keyboard.typed_keys() == [ENTER, ENTER] and notices == []


def test_a_pause_then_another_window_keeps_back_press_enter_said_alone(chat, keyboard,
                                                                       monkeypatch):
    """Type at pause: "see you at noon" typed into one Brave window, a click
    into the other during the pause, then only "press enter": it sent the
    other window's box."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(app, "_press_enter_after", False)
    heard.append("See you at noon.")
    dictate("middle")
    focus[:] = SECOND
    heard.append("press enter")
    dictate("last")
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


def test_a_pause_then_press_enter_alone_in_a_password_field_logs_in(chat, keyboard,
                                                                    monkeypatch):
    """Hands-free: a phrase dictated, then a password typed by hand in a
    login box, then "press enter"."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(app, "_press_enter_after", False)
    heard.append("See you at noon.")
    dictate("middle")
    focus[:] = SECOND
    monkeypatch.setattr(focused_window, "is_password", lambda: True)
    heard.append("press enter")
    dictate("last")
    assert keyboard.typed_keys() == [ENTER] and notices == []
