"""An automatic Enter goes only where the dictation's words went
(send_countdown's one rule).

"Send each dictation automatically" presses Enter by itself. Into another
window it sends whatever is in that box or runs the command on a terminal's
line, and two windows of one app (two Brave windows) look the same by
their class. These pin what someone would see: where the words were typed,
and whether an Enter followed them there or anywhere else.
"""
import time
import threading
import types

import numpy as np
import pytest
from evdev import ecodes

from conftest import CHAT_WINDOW as FIRST, next_recording
from tuxtalk import (atspi_helper, compositor_focus, dbus_service, focused_window, history,
                     modifier_hotkeys, send_countdown)
from tuxtalk import recording_state as rs
from tuxtalk import live_settings as live
from tuxtalk import keyboard_grab
from tuxtalk import keyboard_output
from tuxtalk import engine
from tuxtalk import last_dictation
from tuxtalk import notices as service_notices
from tuxtalk import recording
from tuxtalk import delivery
from tuxtalk import voice_edit
from tuxtalk import type_at_pause
from tuxtalk import dictation_worker
from tuxtalk import pipeline
from tuxtalk import hotkeys

ENTER, RCTRL, F8, F9 = ecodes.KEY_ENTER, ecodes.KEY_RIGHTCTRL, ecodes.KEY_F8, ecodes.KEY_F9
# Taken before conftest runs "Right away" on the test's own thread, and
# before the chat fixture fakes the delivery.
SEND_RIGHT_AWAY = send_countdown._send_right_away
DELIVER_TEXT = delivery.deliver_text
SECOND = ("brave-browser", ("tray", "{9f04b7c3}"))      # another window, same app


def send_automatically(sent_to):
    """An automatic send of words delivered to *sent_to* (a destination)."""
    send_countdown.send_automatically("test", send_countdown.Message(sent_to), keyboard_output.press_key,
                                      delivery.type_onto_dictation)


def dictate(chunk_role=None, audio=True):
    frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()] if audio else []
    pipeline.transcribe_and_inject(frames, engine.SAMPLE_RATE, beeps_on=False, smart_quotes=False,
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
    deliver = delivery.deliver_text

    def clicked_away_meanwhile(*a, **k):
        outcome = deliver(*a, **k)
        focus[:] = SECOND
        return outcome
    monkeypatch.setattr(delivery, "deliver_text", clicked_away_meanwhile)
    heard.append("see you at noon")
    dictate()
    assert typed == [("See you at noon.", FIRST[1])]                 # the words stay
    assert keyboard.events == [] and notices == [send_countdown.MOVED_NOTICE]


def test_no_countdown_after_a_switch_while_the_words_went_in(chat, keyboard, monkeypatch):
    focus, heard, typed, notices = chat
    started = []
    monkeypatch.setattr(send_countdown, "_delay", 2.0)
    monkeypatch.setattr(send_countdown.threading, "Timer",
                        lambda *a, **k: started.append(a) or pytest.fail("a countdown"))
    deliver = delivery.deliver_text

    def clicked_away_meanwhile(*a, **k):
        outcome = deliver(*a, **k)
        focus[:] = SECOND
        return outcome
    monkeypatch.setattr(delivery, "deliver_text", clicked_away_meanwhile)
    heard.append("see you at noon")
    dictate()
    assert started == [] and notices == [send_countdown.MOVED_NOTICE]


def test_after_a_click_on_the_kde_desktop_it_doesnt_say_you_switched(chat, keyboard,
                                                                      monkeypatch):
    """KDE: the desktop was clicked before dictating, and the paste's
    clipboard window handed the focus back to KWrite as the words went in.
    The user switched nothing; the Enter still can't follow."""
    focus, heard, typed, notices = chat
    focus[:] = ("plasmashell", ("tray", "{0d35}"))
    clicked_away_while_the_words_went_in(monkeypatch, focus, ("org.kde.kwrite", ("tray", "{a}")))
    heard.append("see you at noon")
    dictate()
    assert keyboard.events == [] and notices == [send_countdown.MOVED_NOTICE]


def test_a_pause_then_another_window_of_the_app_gets_neither_mark_nor_enter(chat, keyboard,
                                                                            monkeypatch):
    """Type at pause: "See you at noon" typed into one Brave window, its
    period held back for the next phrase; a click into the other Brave
    window; the recording ends in that pause. The period went into the other
    window, and the Enter followed it there, sending what was in its box."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(live, "chunk_context_marks", True)
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
    monkeypatch.setattr(live, "chunk_context_marks", True)
    heard.append("See you at noon.")
    dictate("middle")
    dictate("last", audio=False)
    assert typed == [("See you at noon", FIRST[1]), (".", FIRST[1])]
    assert keyboard.typed_keys() == [ENTER] and notices == []


def test_a_new_recording_doesnt_take_the_last_ones_held_mark(chat, keyboard, monkeypatch):
    """The next recording started before the last one's end was typed (the
    hotkey is read while typing goes on): starting it threw the last
    phrase's held-back period away, so that sentence never got its end."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(live, "chunk_context_marks", True)
    heard.append("See you at noon.")
    dictate("middle")
    first = rs.state.current.message
    next_recording(engine.SAMPLE_RATE)          # the next recording starts
    assert rs.state.current.message is not first
    pipeline.transcribe_and_inject([], engine.SAMPLE_RATE, beeps_on=False, smart_quotes=False,
                                   notify_on=False, chunk_role="last", message=first)
    assert typed[:2] == [("See you at noon", FIRST[1]), (".", FIRST[1])]


def test_a_pause_after_and_at_the_end_sends_nothing(chat, keyboard, monkeypatch):
    """Type at pause: "meet me at noon and", a pause, and the key let go.
    Half a sentence was sent; without Type at pause it is held back, with
    no period, and what's said next carries it on in lowercase."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(live, "chunk_context_marks", True)
    heard.append("Meet me at noon and.")
    dictate("middle")
    dictate("last", audio=False)
    assert typed == [("Meet me at noon and", FIRST[1]), (" ", FIRST[1])]
    assert keyboard.events == [] and rs.state.continue_mid_sentence
    assert notices == ["Not sent: it ended on \u201cand\u201d. Press Enter when you're ready."]



@pytest.fixture
def notified(monkeypatch):
    """The desktop notifications a send without its Enter gives, by title."""
    titles = []
    monkeypatch.setattr(send_countdown, "_notify_in_background",
                        lambda title, body: titles.append(title))
    return titles


def test_a_dictation_held_back_on_and_says_why(chat, keyboard, notified):
    """Held back in silence, it looked as if "Send each dictation
    automatically" had stopped working; a screen reader reads the
    notification, not the label."""
    focus, heard, typed, notices = chat
    heard.append("Meet me at noon and")
    dictate()
    assert keyboard.typed_keys() == []
    assert notices == ["Not sent: it ended on \u201cand\u201d. Press Enter when you're ready."]
    assert notified == ["Not sent: it ended on \u201cand\u201d"]
    rs.state.continue_mid_sentence = False


def test_held_back_after_the_focus_moved_says_that_instead(chat, keyboard, monkeypatch, notified):
    """Enter wouldn't have gone into the window the focus moved to either:
    "press Enter when you're ready" there would send someone else's text."""
    focus, heard, typed, notices = chat
    deliver = delivery.deliver_text

    def clicked_away_meanwhile(*a, **k):
        outcome = deliver(*a, **k)
        focus[:] = SECOND
        return outcome
    monkeypatch.setattr(delivery, "deliver_text", clicked_away_meanwhile)
    heard.append("Meet me at noon and")
    dictate()
    assert keyboard.typed_keys() == [] and notices == [send_countdown.MOVED_NOTICE]
    rs.state.continue_mid_sentence = False


def test_held_back_with_a_phrase_missing_says_that_instead(chat, keyboard, monkeypatch, notified):
    """Type at pause: an earlier phrase didn't go in, the last one ends on
    "and". The message couldn't have been sent as it is."""
    focus, heard, typed, notices = chat
    deliver = delivery.deliver_text
    outcomes = iter(["failed"])
    monkeypatch.setattr(delivery, "deliver_text",
                        lambda *a, **k: deliver(*a, **k) and next(outcomes, "ok"))
    monkeypatch.setattr("tuxtalk.notices.report_undelivered", lambda *a, **k: None)
    heard.extend(["See you at noon.", "and"])
    dictate("middle")
    dictate("last")
    assert keyboard.typed_keys() == [] and notices == [send_countdown.INCOMPLETE_NOTICE]
    rs.state.continue_mid_sentence = False


def test_no_held_back_notice_in_a_text_editor(chat, keyboard, notified):
    """A text editor never gets an automatic Enter: nothing was held back."""
    focus, heard, typed, notices = chat
    focus[:] = ("org.kde.kwrite", ("tray", "{kwrite}"))
    heard.append("Meet me at noon and")
    dictate()
    assert notices == [] and notified == []
    rs.state.continue_mid_sentence = False

def test_esc_while_a_phrase_is_transcribed_types_none_of_it(chat, keyboard, monkeypatch):
    """Type at pause: a phrase cut at a pause was still being transcribed
    (or waiting its turn) when Esc cancelled the recording, and it was typed."""
    focus, heard, typed, notices = chat

    def heard_then_esc(audio, language):
        recording.cancel_recording(False, False, "Cancelled by ESC")
        return "see you at noon"
    monkeypatch.setattr(engine, "transcribe", heard_then_esc)
    dictate("middle")
    dictate("middle")                       # one queued behind it
    assert typed == [] and keyboard.events == []


def test_the_mark_typed_at_the_end_doesnt_aim_the_enter_elsewhere(chat, keyboard,
                                                                  monkeypatch):
    """The held mark is typed where the focus is now, which the desktop
    can't tell from the phrase's window (only the app is known). Its own
    delivery must not become where the Enter goes."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(live, "chunk_context_marks", True)
    heard.append("See you at noon.")
    dictate("middle")
    focus[1] = None                                     # the tray didn't answer
    deliver = delivery.deliver_text

    def mark_then_switch(*a, **k):
        outcome = deliver(*a, **k)
        focus[:] = SECOND                               # clicked away as it went in
        return outcome
    monkeypatch.setattr(delivery, "deliver_text", mark_then_switch)
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
    send_automatically(focused_window.Destination(*FIRST))
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
    send_automatically(focused_window.Destination(*FIRST))
    assert held == [True] and keyboard.events == []


def test_right_away_a_tap_the_hotkey_loop_reads_holds_the_enter(chat, keyboard, monkeypatch):
    """Right away, the Enter's checks run on a thread of their own, so a tap
    of F9 read while they run still holds it; run where the words were
    typed, the tap was read only once the Enter had gone out."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(send_countdown, "_send_right_away", SEND_RIGHT_AWAY)
    words_in, tap_read = threading.Event(), threading.Event()
    deliver = delivery.deliver_text

    def deliver_then_note(*a, **k):
        outcome = deliver(*a, **k)
        words_in.set()
        return outcome
    monkeypatch.setattr(delivery, "deliver_text", deliver_then_note)
    monkeypatch.setattr(atspi_helper, "focused_is_password",
                        lambda: words_in.is_set() and tap_read.wait(2) and False)
    heard.append("see you at noon")
    dictate()
    tap = types.SimpleNamespace(type=ecodes.EV_KEY, code=F9, value=1)
    hotkeys._handle_key_event(tap, F8, F9, None, set(), [], None, None)
    tap_read.set()
    for thread in threading.enumerate():
        if thread.name == "send-right-away":
            thread.join(5)
    assert keyboard.events == [] and notices == [send_countdown.HELD_NOTICE]


class Countdown:
    """threading.Timer for a send counting down: it never fires by itself."""

    def __init__(self, delay, run, args):
        self.daemon = False

    def start(self):
        pass

    def cancel(self):
        pass


@pytest.mark.parametrize("hold_key", [RCTRL, F8])
def test_the_hold_key_holds_the_send_before_it_hides_itself(chat, keyboard, monkeypatch,
                                                            hold_key):
    """Right Ctrl as the hold key, pressed as the countdown ran out: hiding
    the key let the Enter that waited for it go out before the press held
    the send. With F8 the grab can take as long as the countdown's last
    moment."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(modifier_hotkeys, "_noted", ([], frozenset()))
    devices = [Keyboard(lambda look: True)]           # the hold key is down
    modifier_hotkeys.note(devices, hold_key, F9)
    counting_down(monkeypatch)
    waiting = send_countdown._pending

    def grab_then_the_send_runs(devices, codes):
        modifier_hotkeys.note_hidden(set(codes), devices)
        send_countdown._send(waiting)                 # its thread got there first
    monkeypatch.setattr(keyboard_grab, "grab_keyboards_hiding", grab_then_the_send_runs)
    monkeypatch.setattr(recording, "open_input_stream", lambda idx: None)
    monkeypatch.setattr("tuxtalk.type_at_pause.arm_chunker", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.recording.pause_media_players", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.tray_link.notify_recording_state", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    monkeypatch.setattr(keyboard_grab, "release_all", lambda: None)
    monkeypatch.setattr(recording, "indicator", None)
    cfg = types.SimpleNamespace(beeps=False, notify=False)
    press = types.SimpleNamespace(type=ecodes.EV_KEY, code=hold_key, value=1)
    try:
        hotkeys._handle_key_event(press, hold_key, F9, None, set(), devices, cfg, 0)
        assert keyboard.events == [] and rs.state.current.held_send is True
    finally:
        rs.state.is_recording = False
        rs.state.current.stream = None


HELD_TITLE = "Not sent: you held it"


def counting_down(monkeypatch):
    monkeypatch.setattr(send_countdown, "_delay", 2.0)
    monkeypatch.setattr(send_countdown.threading, "Timer", Countdown)
    send_automatically(focused_window.destination())


def test_a_tap_of_the_toggle_key_that_holds_the_send_says_so_aloud(chat, keyboard, monkeypatch,
                                                                    notifications):
    """Notifications off, or no XWayland for the label: the send was held
    and nothing a screen reader reads said so."""
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)
    tap = types.SimpleNamespace(type=ecodes.EV_KEY, code=F9, value=1)
    hotkeys._handle_key_event(tap, F8, F9, None, set(), [], None, None)
    assert notices == [send_countdown.HELD_NOTICE] and notifications() == [HELD_TITLE]


@pytest.mark.parametrize("notify_on", [False, True])
def test_a_short_tap_of_the_hold_key_that_holds_the_send_says_so_aloud_once(
        chat, keyboard, monkeypatch, notifications, notify_on):
    focus, heard, typed, notices = chat
    monkeypatch.setattr("tuxtalk.recording.resume_media_players", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.recording.stop_stream_safely", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.tray_link.notify_recording_state", lambda *a, **k: None)
    monkeypatch.setattr(recording, "indicator", None)
    monkeypatch.setattr(rs.state.current, "held_send", True)
    monkeypatch.setattr(rs.state.current, "silence", None)
    monkeypatch.setattr(rs.state.current, "press_t0", time.time())
    assert recording.finish_capture(False, notify_on) is None
    assert notices == [send_countdown.HELD_NOTICE] and notifications() == [HELD_TITLE]


def test_a_hold_key_press_whose_recording_cant_start_says_the_send_is_held(
        chat, keyboard, monkeypatch, notifications):
    """The press held the send to dictate more onto it, then the microphone
    didn't open: nothing said the message wasn't sent."""
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)

    def no_microphone(idx):
        raise OSError("Device unavailable")
    monkeypatch.setattr(recording, "open_input_stream", no_microphone)
    monkeypatch.setattr(recording, "pick_input_device", lambda mic: 0)
    monkeypatch.setattr(recording, "stop_stream_safely", lambda *a, **k: None)
    monkeypatch.setattr(keyboard_grab, "grab_keyboards_hiding", lambda *a, **k: None)
    monkeypatch.setattr(keyboard_grab, "release_all", lambda: None)
    cfg = types.SimpleNamespace(beeps=False, notify=False)
    try:
        hotkeys._start_hold_recording(F8, set(), [], cfg, 0)
    finally:
        rs.state.is_recording = False
    assert keyboard.events == []
    assert notices == [send_countdown.HELD_NOTICE] and notifications() == [HELD_TITLE]


def test_holding_the_send_to_dictate_more_onto_it_says_nothing(chat, monkeypatch, notifications):
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)
    assert send_countdown.hold(announce=False)
    assert notices == [] and notifications() == []


def test_a_click_during_the_password_check_sends_nothing(chat, keyboard, monkeypatch):
    """The accessibility bus can take a quarter of a second to say whether
    the cursor is in a password field. Clicked into the other Brave window
    meanwhile: the window looked at before the check got the Enter."""
    focus, _, _, notices = chat

    def slow_check():
        focus[:] = SECOND
        return False
    monkeypatch.setattr(atspi_helper, "focused_is_password", slow_check)
    send_automatically(focused_window.Destination(*FIRST))
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
    send_automatically(focused_window.Destination(*FIRST))
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


def test_right_away_into_the_other_window_of_the_app_sends_nothing(chat, keyboard):
    focus, _, _, notices = chat
    focus[:] = SECOND
    send_automatically(focused_window.Destination(*FIRST))
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


# --- Type at pause's held mark: only into its phrase's window, up to the key ---------

class WlCopy:
    """wl-copy setting the clipboard, during which the user clicks."""

    def __init__(self, clicked):
        self.stdin, self.stderr, self.clicked = self, None, clicked

    def write(self, data):
        pass

    def close(self):
        pass

    def poll(self):
        return 0

    def wait(self, timeout=None):
        self.clicked()
        return 0


def test_a_click_while_the_mark_is_put_on_the_clipboard_keeps_it_out_of_the_other_window(
        chat, keyboard, monkeypatch):
    """The held period for the first Brave window is pasted. The window was
    checked before wl-copy set the clipboard, the user clicked into the
    other one meanwhile, and Ctrl+V put the period there."""
    from tuxtalk import clipboard
    focus, _, _, _ = chat
    monkeypatch.setattr(delivery, "deliver_text", DELIVER_TEXT)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(keyboard_output, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(live, "restore_clipboard", False)
    monkeypatch.setattr(clipboard, "wl_copy_command", lambda: ["wl-copy"])
    monkeypatch.setattr(keyboard_output.subprocess, "Popen",
                        lambda cmd, **kw: WlCopy(lambda: focus.__setitem__(slice(None), SECOND)))
    assert delivery.inject_text(".", "paste", into=focused_window.Destination(*FIRST)) == "moved"
    assert keyboard.events == []


def test_a_click_while_typing_waits_for_the_dictation_key_keeps_the_mark_out(
        chat, keyboard, monkeypatch, right_ctrl_toggle):
    """Right Ctrl down as the mark is typed: the click came as the wait
    began, so it compared the other window with itself."""
    focus, _, _, _ = chat
    monkeypatch.setattr(delivery, "deliver_text", DELIVER_TEXT)

    def pressed(look):
        if look == 1:
            focus[:] = SECOND
        return look <= 3
    right_ctrl_toggle(pressed)
    assert delivery.inject_text(".", "type", into=focused_window.Destination(*FIRST)) == "moved"
    assert keyboard.events == []


# --- TuxTalk's own windows ------------------------------------------------------------------

@pytest.fixture
def tray(monkeypatch):
    """The tray's real focus cache, told the focused window as the KWin
    script and the GNOME extension tell it, and asked as the service asks."""
    monkeypatch.setattr(dbus_service, "_focus", dbus_service.FocusReport())
    monkeypatch.setattr(dbus_service, "_clipboard_tool_focused", False)
    monkeypatch.setattr(focused_window, "reported", lambda: tuple(dbus_service.cached_focus()))
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: (None, None))
    monkeypatch.setattr("time.sleep", lambda s: None)
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
    send_automatically(sent_to)
    assert keyboard.events == [] and notices == [send_countdown.OWN_WINDOW_NOTICE]


@pytest.mark.parametrize("told, before, after", [
    ("tray", ("org.kde.kwrite", "{5c1d}"), ("tuxtalk", "")),              # 0.18's extension
    ("compositor", ("foot", 42), ("tuxtalk", 57)),                         # Sway, niri, Hyprland
    ("compositor", ("cosmic-term", None), ("tuxtalk", None)),             # COSMIC
    ("compositor", ("Kwrite", 0x3200007), ("Tuxtalk", 0x3400004)),        # X11
])
@pytest.mark.parametrize("key", ["auto", "enter", "tab"])
def test_a_tuxtalk_window_however_the_desktop_names_it_gets_no_key(
        tray, keyboard, monkeypatch, told, before, after, key):
    """Preferences, or the Parakeet offer that came up by itself, has the
    focus when the Enter is due. Told only by the compositor, or with no
    id, TuxTalk's window wasn't known as its own, and the Enter pressed
    its default button (OK applied the settings)."""
    notices = []
    monkeypatch.setattr(send_countdown, "show_notice", notices.append)
    service = object.__new__(dbus_service.TuxTalkDBusService)

    def focus(wm_class, window_id):
        if told == "tray":
            dbus_service.TuxTalkDBusService.SetFocusedWindow(service, wm_class, window_id, "")
        else:
            monkeypatch.setattr(focused_window, "reported", lambda: None)
            monkeypatch.setattr(compositor_focus, "focused_window", lambda: (wm_class, window_id))
    focus(*before)
    message = send_countdown.Message(focused_window.destination(), has_words=True)
    focus(*after)
    if told == "tray":                    # 0.18's extension: the class alone
        dbus_service.TuxTalkDBusService.SetFocusedWindowClass(service, "tuxtalk")
    if key == "auto":
        send_countdown.send_automatically("test", message, keyboard_output.press_key, None)
    else:
        send_countdown.press_where_sent(key.upper(), "test", message, keyboard_output.press_key)
    assert keyboard.events == []
    if key == "auto" and before[1] is None:     # COSMIC: no automatic Enter at all
        assert notices == [send_countdown.UNKNOWN_WINDOW_NOTICE]
    else:
        assert notices == [send_countdown.OWN_WINDOW_NOTICE if key != "tab"
                           else "Tab not pressed: a TuxTalk window has the focus."]


@pytest.mark.parametrize("told, window", [
    ("tray", ("tuxtalk", "{77a0c4e1}")),                                  # KDE, GNOME
    ("compositor", ("tuxtalk", 57)),                                       # Sway, niri, Hyprland
    ("compositor", ("Tuxtalk", 0x3400004)),                                # X11
])
@pytest.mark.parametrize("key", ["auto", "enter", "tab"])
def test_words_dictated_into_a_tuxtalk_box_get_only_the_key_said_after_them(
        tray, keyboard, monkeypatch, told, window, key):
    """Preferences → Words → Add Word: "ClaudeClaw, press tab" moves to the
    next box, as the user asked; an automatic Enter still doesn't go there,
    where it would press the dialog's default button."""
    notices = []
    monkeypatch.setattr(send_countdown, "show_notice", notices.append)
    if told == "tray":
        tray(*window)
    else:
        monkeypatch.setattr(focused_window, "reported", lambda: None)
        monkeypatch.setattr(compositor_focus, "focused_window", lambda: window)
    message = send_countdown.Message(focused_window.destination(), has_words=True)
    if key == "auto":
        send_countdown.send_automatically("test", message, keyboard_output.press_key, None)
        assert keyboard.events == [] and notices == [send_countdown.OWN_WINDOW_NOTICE]
    else:
        send_countdown.press_where_sent(key.upper(), "test", message, keyboard_output.press_key)
        assert keyboard.typed_keys() == [getattr(ecodes, f"KEY_{key.upper()}")]


# --- Where the desktop can't say which window: no automatic Enter, a spoken one goes ----

# What the tray was told and what the compositor says, (reported, compositor),
# when the words go in and then after: reported is None when the tray
# didn't answer.
UNKNOWN_SOURCES = {
    "COSMIC": [(None, ("cosmic-term", None))] * 2,
    "GNOME with 0.18's extension": [(("brave-browser", "", "0,0,800,600||"), (None, None))] * 2,
    "nothing reported": [(("", "", ""), (None, None))] * 2,       # GNOME, no extension
    "a tray too busy to answer": [(("brave-browser", "{5c1d}", ""), (None, None)),
                                  (None, (None, None))],
    "a busy tray on X11": [(("brave-browser", "{5c1d}", ""), (None, None)),
                           (None, ("Brave-browser", 0x3200007))],
}
REAL_LOOK = focused_window.look


@pytest.fixture(params=list(UNKNOWN_SOURCES))
def unknown_window(request, chat, monkeypatch):
    """The chat fixture on a desktop that doesn't say which window has the
    focus, or stops saying it once the words are in."""
    said = list(UNKNOWN_SOURCES[request.param])
    monkeypatch.setattr(focused_window, "look", REAL_LOOK)
    monkeypatch.setattr(focused_window, "reported", lambda: said[0][0])
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: said[0][1])
    deliver = delivery.deliver_text

    def words_in_then_after(*a, **k):
        outcome = deliver(*a, **k)
        said.pop(0) if len(said) > 1 else None
        return outcome
    monkeypatch.setattr(delivery, "deliver_text", words_in_then_after)
    return chat


def test_where_the_window_isnt_known_nothing_is_sent_automatically(unknown_window, keyboard,
                                                                   monkeypatch):
    """A message sent into the wrong chat can't be taken back: the words
    stay, and the notice says to press Enter."""
    focus, heard, typed, notices = unknown_window
    heard.append("see you at noon")
    dictate()
    assert typed[0][0] == "See you at noon." and keyboard.events == []
    assert notices == [send_countdown.UNKNOWN_WINDOW_NOTICE]


def test_where_the_window_isnt_known_the_next_dictation_keeps_its_space(unknown_window,
                                                                         keyboard):
    """The auto-space is left off before an Enter; refused, the Enter took
    the space with it, and the next dictation stuck to the last word. The
    space goes right after the words, not in front of the next message,
    which the user may send first."""
    focus, heard, typed, notices = unknown_window
    heard.append("see you at noon")
    dictate()
    next_recording(engine.SAMPLE_RATE)               # the next recording starts
    heard.append("how are you")
    dictate()
    assert "".join(text for text, _ in typed).startswith("See you at noon. How are you")


def test_where_the_window_is_never_known_the_space_goes_after_the_words(chat, keyboard):
    """On COSMIC no Enter can follow: the space goes in at once, as with
    sending off. Owed, it went in front of the next message, after the user
    had pressed Enter on this one: " How are you." """
    focus, heard, typed, notices = chat
    focus[1] = None                                 # COSMIC: only the app
    for words in ("see you at noon", "how are you"):
        next_recording(engine.SAMPLE_RATE)
        heard.append(words)
        dictate()
    assert [text for text, _ in typed] == ["See you at noon.", " ", "How are you.", " "]


def test_where_the_window_is_never_known_no_countdown_runs(chat, keyboard, monkeypatch):
    focus, heard, typed, notices = chat
    focus[1] = None                                 # COSMIC: only the app
    monkeypatch.setattr(send_countdown, "_delay", 2.0)
    monkeypatch.setattr(send_countdown.threading, "Timer",
                        lambda *a, **k: pytest.fail("counted down to a send that can't go"))
    heard.append("see you at noon")
    dictate()
    assert keyboard.events == [] and notices == [send_countdown.UNKNOWN_WINDOW_NOTICE]


@pytest.mark.parametrize("key", ["enter", "tab"])
def test_where_the_window_isnt_known_a_key_said_after_the_words_still_goes(
        unknown_window, keyboard, monkeypatch, key):
    """The user asked for the key, looking at the window."""
    focus, heard, typed, notices = unknown_window
    monkeypatch.setattr(live, "press_enter_after", False)
    heard.append(f"see you at noon, press {key}")
    dictate()
    assert keyboard.typed_keys() == [getattr(ecodes, f"KEY_{key.upper()}")] and notices == []


def test_a_known_window_still_gets_its_automatic_enter(chat, keyboard):
    send_automatically(focused_window.destination())
    assert keyboard.typed_keys() == [ENTER]


# --- "... press enter" said after the words: the same rule ---------------------------------

def clicked_away_while_the_words_went_in(monkeypatch, focus, to=SECOND):
    deliver = delivery.deliver_text

    def deliver_then_switch(*a, **k):
        outcome = deliver(*a, **k)
        focus[:] = to
        return outcome
    monkeypatch.setattr(delivery, "deliver_text", deliver_then_switch)


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


CLAUDE = ("com.anthropic.Claude", ("tray", "{c1a0}"))


@pytest.mark.parametrize("when", ["while", "after"])
def test_a_tab_said_after_the_words_types_no_spaces_into_claude_if_they_went_elsewhere(
        chat, keyboard, monkeypatch, when):
    """"John, press tab" into Brave, and a click into Claude Desktop while
    the words went in or right after: there "press tab" is four spaces,
    and they went into Claude's box, whose Send button a space can press."""
    focus, heard, typed, notices = chat
    if when == "while":
        clicked_away_while_the_words_went_in(monkeypatch, focus, CLAUDE)
    else:                                   # once the words are in
        monkeypatch.setattr(last_dictation, "remember_shown",
                            lambda *a, **k: focus.__setitem__(slice(None), CLAUDE))
    heard.append("john, press tab")
    dictate()
    assert typed == [("John", FIRST[1])] and keyboard.events == []
    assert len(notices) == 1


def test_a_tab_said_after_the_words_in_claude_is_four_spaces_there(chat, keyboard):
    focus, heard, typed, notices = chat
    focus[:] = CLAUDE
    heard.append("john, press tab")
    dictate()
    assert typed == [("John", CLAUDE[1]), ("    ", CLAUDE[1])] and notices == []


def test_a_switch_after_the_words_keeps_back_the_enter_said_after_them(chat, keyboard,
                                                                       monkeypatch):
    """The click comes after the words are in, in the moment the app is
    given to take them in before the Enter."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr("time.sleep", lambda s: focus.__setitem__(slice(None), SECOND))
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
    next_recording(engine.SAMPLE_RATE)               # the next recording starts
    heard.append("press enter")
    dictate()
    assert keyboard.typed_keys() == [ENTER, ENTER] and notices == []


def test_a_pause_then_another_window_keeps_back_press_enter_said_alone(chat, keyboard,
                                                                       monkeypatch):
    """Type at pause: "see you at noon" typed into one Brave window, a click
    into the other during the pause, then only "press enter": it sent the
    other window's box."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(live, "press_enter_after", False)
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
    monkeypatch.setattr(live, "press_enter_after", False)
    heard.append("See you at noon.")
    dictate("middle")
    focus[:] = SECOND
    monkeypatch.setattr(focused_window, "is_password", lambda: True)
    heard.append("press enter")
    dictate("last")
    assert keyboard.typed_keys() == [ENTER] and notices == []


def test_a_click_while_the_pause_is_heard_again_keeps_its_mark_out_of_the_other_window(
        chat, keyboard, monkeypatch):
    """Type at pause hears the end of a phrase again with the start of the
    next to choose its mark, which can take a moment. Clicked into the other
    Brave window meanwhile, the first window's period went there."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(live, "chunk_context_marks", True)
    monkeypatch.setattr(live, "press_enter_after", False)
    heard.append("See you at noon.")
    dictate("middle")
    heard.extend(["how are you", "See you at noon. How are you"])
    transcribe = engine.transcribe

    def context_pass_then_click(audio, language):
        words = transcribe(audio, language)
        if not heard:                       # the pass that heard both
            focus[:] = SECOND
        return words
    monkeypatch.setattr(engine, "transcribe", context_pass_then_click)
    dictate("middle")
    assert typed[0] == ("See you at noon", FIRST[1])
    assert all(window == FIRST[1] for text, window in typed if text.strip() in ".?!")
    assert typed[-1][1] == SECOND[1]


def test_with_0_18s_extension_a_resized_window_isnt_taken_for_a_switch(
        tray, keyboard, monkeypatch):
    """GNOME with the extension from 0.18 still loaded until the next login
    tells the app and the window's frame. Maximizing the browser during the
    countdown said "you switched windows"."""
    notices, frames = [], ["0,0,800,600||0,0,1920,1080", "0,0,1920,1050||0,0,1920,1080"]
    monkeypatch.setattr(send_countdown, "show_notice", notices.append)
    service = object.__new__(dbus_service.TuxTalkDBusService)
    dbus_service.TuxTalkDBusService.SetFocusedWindowClass(service, "brave-browser")
    dbus_service.TuxTalkDBusService.SetFocusedWindowGeometry(service, frames[0])
    sent_to = focused_window.destination()
    dbus_service.TuxTalkDBusService.SetFocusedWindowGeometry(service, frames[1])
    send_automatically(sent_to)
    assert keyboard.events == [] and notices == [send_countdown.UNKNOWN_WINDOW_NOTICE]


def test_a_send_that_doesnt_go_says_so_in_a_notification_too(chat, keyboard, monkeypatch,
                                                              notifications):
    """A screen reader reads notifications, not the label over the window,
    and without XWayland the label can't be shown at all."""
    focus, heard, typed, notices = chat
    clicked_away_while_the_words_went_in(monkeypatch, focus)
    heard.append("see you at noon")
    dictate()
    assert keyboard.events == [] and len(notices) == 1
    assert notifications() == ["Not sent: the focus moved while the words were delivered"]


def test_where_the_window_is_never_known_the_notification_comes_once(chat, keyboard,
                                                                      monkeypatch, notifications):
    """On COSMIC every dictation is refused: a notification after each one,
    forever, is a nag. The floating notice still says so each time."""
    focus, heard, typed, notices = chat
    focus[1] = None                                 # COSMIC: only the app
    for words in ("see you at noon", "how are you"):
        next_recording(engine.SAMPLE_RATE)
        heard.append(words)
        dictate()
    assert notices == [send_countdown.UNKNOWN_WINDOW_NOTICE] * 2
    assert notifications() == ["Not sent: which window has the focus isn't known"]


def test_a_tray_too_busy_for_one_tick_doesnt_call_off_the_countdown(chat, keyboard,
                                                                    monkeypatch):
    """Preferences opening or a model loading kept the tray from answering
    for a second: the countdown gave up on a send that could still go."""
    focus, heard, typed, notices = chat
    timers = []
    monkeypatch.setattr(send_countdown, "_delay", 3.0)

    class Timer:
        def __init__(self, delay, run, args):
            self.run, self.args, self.daemon = run, args, False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass
    monkeypatch.setattr(send_countdown.threading, "Timer", Timer)
    send_automatically(focused_window.destination())
    focus[:] = [None, None]                         # the tray doesn't answer
    timers[1].run(*timers[1].args)                  # "Sending in 2 s"
    focus[:] = FIRST
    timers[2].run(*timers[2].args)
    timers[0].run(*timers[0].args)                  # the Enter
    assert keyboard.typed_keys() == [ENTER] and notices == []


def test_a_countdown_says_nothing_aloud_until_it_ends(chat, keyboard, monkeypatch):
    from tuxtalk import notify
    sent, timers = [], []
    monkeypatch.setattr(notify, "_send", lambda title, body: sent.append(title))
    monkeypatch.setattr(send_countdown, "_delay", 3.0)

    class Timer:
        def __init__(self, delay, run, args):
            self.run, self.args, self.daemon = run, args, False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass
    monkeypatch.setattr(send_countdown.threading, "Timer", Timer)
    send_automatically(focused_window.destination())
    for timer in timers[1:]:                    # "Sending in 2 s", then 1
        timer.run(*timer.args)
    timers[0].run(*timers[0].args)              # the Enter
    assert keyboard.typed_keys() == [ENTER] and sent == []


def phrases_that_go_in(monkeypatch, *outcomes):
    """Type at pause's phrases, the next one going in as *outcomes* says
    ("partial": its typing stopped partway, a key stuck down)."""
    deliver, left = delivery.deliver_text, iter(outcomes)
    monkeypatch.setattr(delivery, "deliver_text", lambda *a, **k: deliver(*a, **k) and next(left))
    monkeypatch.setattr(service_notices, "report_undelivered", lambda outcome, beeps_on: None)
    monkeypatch.setattr(live, "chunk_context_marks", False)


def test_a_phrase_that_stopped_partway_leaves_the_message_unsent(chat, keyboard, monkeypatch):
    """The second phrase stopped partway, and the notice said the whole
    text is in Recent Dictations. The recording then ended in a pause, and
    the half message was sent. No send is tried, so no second notice says
    so again."""
    focus, heard, typed, notices = chat
    phrases_that_go_in(monkeypatch, "ok", "partial")
    heard.extend(["See you at noon,", "and bring the cake"])
    dictate("middle")
    dictate("middle")
    dictate("last", audio=False)
    assert len(typed) == 2 and keyboard.events == []
    assert notices == []


def test_a_phrase_that_went_in_whole_after_one_that_didnt_still_sends_nothing(
        chat, keyboard, monkeypatch):
    """The third phrase went in whole and made the message look complete
    again: the stop sent it with the hole the second one left."""
    focus, heard, typed, notices = chat
    phrases_that_go_in(monkeypatch, "ok", "partial", "ok")
    heard.extend(["See you at noon,", "and bring the cake", "for Anna"])
    for _ in range(3):
        dictate("middle")
    dictate("last", audio=False)
    assert len(typed) == 3 and keyboard.events == []
    assert notices == []


def test_a_phrase_that_failed_then_a_last_one_that_went_in_sends_nothing(
        chat, keyboard, monkeypatch):
    focus, heard, typed, notices = chat
    phrases_that_go_in(monkeypatch, "failed", "ok")
    heard.extend(["See you at noon,", "and bring the cake"])
    dictate("middle")
    dictate("last")
    assert keyboard.events == [] and notices == [send_countdown.INCOMPLETE_NOTICE]


def test_a_click_away_while_a_phrase_went_in_keeps_the_message_unsent(chat, keyboard,
                                                                     monkeypatch):
    """The first phrase may be in either Brave window: the next one, typed
    whole into the second, doesn't say where the first went."""
    focus, heard, typed, notices = chat
    deliver = delivery.deliver_text

    def first_one_then_switch(*a, **k):
        outcome = deliver(*a, **k)
        focus[:] = SECOND
        monkeypatch.setattr(delivery, "deliver_text", deliver)
        return outcome
    monkeypatch.setattr(delivery, "deliver_text", first_one_then_switch)
    monkeypatch.setattr(live, "chunk_context_marks", False)
    heard.extend(["See you at noon,", "and bring the cake"])
    dictate("middle")
    dictate("last")
    assert keyboard.events == [] and notices == [send_countdown.MOVED_NOTICE]


def test_press_enter_alone_after_a_phrase_that_stopped_partway_isnt_pressed_anywhere(
        chat, keyboard, monkeypatch):
    """A phrase in one Brave window, the next one stopped partway, a click
    into the other window, then only "press enter": nothing said it came
    after words, and it sent the other window's box."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(live, "press_enter_after", False)
    phrases_that_go_in(monkeypatch, "ok", "partial")
    heard.extend(["See you at noon,", "and bring the cake", "press enter"])
    dictate("middle")
    dictate("middle")
    focus[:] = SECOND
    dictate("middle")
    assert keyboard.events == [] and notices == [send_countdown.INCOMPLETE_NOTICE]


def test_an_enter_the_keyboard_failed_to_press_says_so(chat, keyboard):
    """Every check passed and the keyboard then failed on the Enter: the
    countdown label just ran out, and the message sat unsent."""
    focus, _, _, notices = chat
    keyboard.fail_after = 0
    send_automatically(focused_window.destination())
    assert keyboard.events == [] and notices == [send_countdown.HELD_NOTICE]


@pytest.fixture
def edit_that(chat, monkeypatch):
    """Voice editing on, with the selection and the AI faked: "edit that,
    ..." rewrites what was typed, in the window that has the focus."""
    from tuxtalk import clipboard

    class Engine:
        def is_ready(self):
            return True

        def edit(self, text, instruction):
            return "Hope to see you at noon!"
    monkeypatch.setattr(live, "voice_editing", True)
    monkeypatch.setattr(live, "ai_engine", Engine())
    monkeypatch.setattr(keyboard_output, "is_wayland_session", lambda: True)
    backend = keyboard_output.output_backend()
    # Selecting what was typed is faked; Enter goes to the test's keyboard.
    monkeypatch.setattr(keyboard_output, "output_backend", lambda: types.SimpleNamespace(
        press_keys=lambda chords, claim=None: (backend.press_keys(chords, claim=claim)
                                               if chords == [((), "ENTER")] else True)))
    monkeypatch.setattr(clipboard, "copy_selection",
                        lambda send_copy: ("See you at noon.", lambda also=(): None))
    monkeypatch.setattr(voice_edit, "EDIT_RESTORE_DELAY_S", 0)
    return chat


def test_after_edit_that_press_enter_alone_in_another_window_isnt_pressed(edit_that, keyboard,
                                                                          monkeypatch):
    """Type at pause: a phrase into one Brave window, "edit that, make it
    friendlier" rewrites it there, a click into the other window, then only
    "press enter". The rewrite is still unsent in the first window, but the
    edit forgot the message, so Enter sent the other window's box."""
    focus, heard, typed, notices = edit_that
    monkeypatch.setattr(live, "press_enter_after", False)
    heard.append("See you at noon.")
    dictate("middle")
    heard.append("edit that, make it friendlier")
    dictate("middle")
    assert typed[-1] == ("Hope to see you at noon!", FIRST[1])
    focus[:] = SECOND
    heard.append("press enter")
    dictate("last")
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]


def test_after_edit_that_press_enter_sends_the_rewrite_where_it_is(edit_that, keyboard,
                                                                  monkeypatch):
    """The same in one window: Enter sends the rewritten message."""
    focus, heard, typed, notices = edit_that
    monkeypatch.setattr(live, "press_enter_after", False)
    heard.append("See you at noon.")
    dictate("middle")
    heard.append("edit that, make it friendlier")
    dictate("middle")
    heard.append("press enter")
    dictate("last")
    assert keyboard.typed_keys() == [ENTER] and notices == []


def test_after_a_send_refused_for_a_click_away_the_next_dictation_still_goes_in(
        chat, keyboard, monkeypatch):
    """The focus moved while the words went in, so the send had no window
    to go to. The space it left off was kept for "the window it went to",
    which there wasn't, and the next dictation raised before it was typed
    or saved to Recent Dictations."""
    focus, heard, typed, notices = chat
    saved = []
    monkeypatch.setattr(history, "add_entry", lambda text, replaces=None: saved.append(text))
    into_the_chat = delivery.deliver_text
    clicked_away_while_the_words_went_in(monkeypatch, focus)
    heard.append("see you at noon")
    dictate()
    assert keyboard.events == []                    # the send had nowhere to go
    monkeypatch.setattr(delivery, "deliver_text", into_the_chat)
    next_recording(engine.SAMPLE_RATE)
    heard.append("how are you")
    dictate()
    assert saved[-1].startswith("How are you") and typed[-1][0].startswith("How are you")
    assert keyboard.typed_keys() == [ENTER]         # sent where it went


def test_a_phrase_queued_before_the_next_recording_keeps_its_own_message(chat, keyboard,
                                                                        monkeypatch):
    """On the Flatpak the next recording can start while the last one's
    phrases still wait to be typed. Its fresh message replaced theirs, so
    the old recording's lone "press enter" was pressed unguarded in the
    window the user had switched to."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(live, "press_enter_after", False)
    next_recording(engine.SAMPLE_RATE)               # recording A
    first = rs.state.current.message
    frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    heard.append("See you at noon.")
    pipeline.transcribe_and_inject(frames, engine.SAMPLE_RATE, False, False, False,
                               chunk_role="middle", message=first)
    next_recording(engine.SAMPLE_RATE)               # recording B starts meanwhile
    focus[:] = SECOND
    heard.append("press enter")
    pipeline.transcribe_and_inject(frames, engine.SAMPLE_RATE, False, False, False,
                               chunk_role="last", message=first)
    assert keyboard.events == [] and notices == [send_countdown.SWITCHED_WINDOW_NOTICE]
    assert not rs.state.current.message.has_words          # B's own message untouched


def _rewrite_goes_in(monkeypatch, outcome):
    """The chat's delivery, with the voice edit's rewrite going in as
    *outcome* ("raise": typing it raised)."""
    deliver = delivery.deliver_text

    def rewrite(text, *a, **k):
        if text.startswith("Hope"):
            if outcome == "raise":
                raise OSError("the virtual keyboard went away")
            deliver(text, *a, **k)
            return outcome
        return deliver(text, *a, **k)
    monkeypatch.setattr(delivery, "deliver_text", rewrite)


@pytest.mark.parametrize("outcome", ["raise", "partial"])
def test_a_rewrite_that_didnt_all_go_in_leaves_the_message_unsent(edit_that, keyboard,
                                                                  monkeypatch, outcome):
    """Type at pause with "Send each dictation automatically": "edit that"
    rewrote the phrase, but typing the rewrite failed or stopped partway,
    and the recording's stop sent whatever was in the box."""
    focus, heard, typed, notices = edit_that
    _rewrite_goes_in(monkeypatch, outcome)
    monkeypatch.setattr(service_notices, "report_undelivered", lambda outcome, beeps_on: None)
    heard.append("See you at noon.")
    dictate("middle")
    heard.append("edit that, make it friendlier")
    dictate("middle")
    dictate("last", audio=False)
    assert keyboard.events == []


def test_a_send_that_doesnt_go_notifies_from_a_thread_of_its_own(chat, keyboard, monkeypatch):
    """A slow notification service held up the thread that typed, then the
    hotkey loop: a press and its release read together counted as a tap."""
    from tuxtalk import notify
    threads = []
    monkeypatch.setattr(notify, "_send", lambda title, body: threads.append(
        threading.current_thread().name))
    clicked_away_while_the_words_went_in(monkeypatch, chat[0])
    chat[1].append("see you at noon")
    dictate()
    for thread in threading.enumerate():
        if thread.name == "send-not-sent-notice":
            thread.join(5)
    assert threads == ["send-not-sent-notice"]


def test_a_countdown_called_off_partway_still_gives_back_its_space(chat, keyboard,
                                                                   monkeypatch):
    """A click into the other window during the countdown calls the send
    off; the space left off for the Enter goes in front of the next
    dictation into the first window, not lost ("noon.How")."""
    focus, heard, typed, notices = chat
    timers = []
    monkeypatch.setattr(send_countdown, "_delay", 3.0)

    class Timer:
        def __init__(self, delay, run, args):
            self.run, self.args, self.daemon = run, args, False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass
    monkeypatch.setattr(send_countdown.threading, "Timer", Timer)
    send_countdown.send_automatically("test", send_countdown.Message(focused_window.destination()),
                                      keyboard_output.press_key, delivery.type_onto_dictation, " ")
    focus[:] = SECOND
    timers[1].run(*timers[1].args)                  # "Sending in 2 s": another window
    focus[:] = FIRST
    typed.clear()
    heard.append("how are you")
    dictate()
    assert typed[:2] == [(" ", FIRST[1]), ("How are you.", FIRST[1])]


def test_an_owed_space_never_goes_into_the_window_the_next_dictation_went_to(
        chat, keyboard, monkeypatch):
    """The space a called-off send owes goes only into its own window. The
    look before the next dictation saw that window, then a click moved the
    focus before the keys went out: the dictation goes where the focus is,
    as always, but no space goes in front of it there."""
    focus, heard, typed, notices = chat
    monkeypatch.setattr(send_countdown, "_delay", 0.0)
    monkeypatch.setattr(live, "press_enter_after", False)
    send_countdown._owe_space({"to": focused_window.destination(), "space": " ", "mode": "type",
                               "type": delivery.type_onto_dictation})
    monkeypatch.setattr(send_countdown, "_focus_left", lambda pending: False)   # looked: FIRST
    focus[:] = SECOND                                                           # then a click
    heard.append("how are you")
    dictate()
    assert typed == [("How are you. ", SECOND[1])]


def test_where_the_window_is_never_known_the_space_goes_only_where_the_words_did(
        chat, keyboard, monkeypatch):
    """COSMIC: a click into a terminal as the "Not sent" notice came up.
    The space went into the terminal, and undo, which follows where the
    last text went, then took the terminal for the dictation's window:
    "delete last sentence" there backspaced the chat message's length."""
    focus, heard, typed, notices = chat
    focus[1] = None                                 # COSMIC: only the app

    def notice_then_click(text):
        notices.append(text)
        focus[:] = ["konsole", None]
    monkeypatch.setattr(send_countdown, "show_notice", notice_then_click)
    heard.append("see you at noon")
    dictate()
    assert [text for text, _ in typed] == ["See you at noon."]
    assert rs.state.last_inserted_window == "brave-browser"
    # The refused space didn't wipe what "delete last sentence" works from.
    assert rs.state.last_inserted_text == "See you at noon."



def test_a_failed_space_may_have_gone_in_so_the_record_isnt_brought_back(monkeypatch):
    """"failed" can follow some of the text (wtype timing out): the old
    record of the dictation would then count the wrong characters."""
    monkeypatch.setattr(rs.state, "last_inserted_text", "See you at noon.")

    def inject(text, mode, into=None, **kw):
        """Forgets the dictation, as a delivery that stopped does."""
        rs.state.last_inserted_text = ""
        return "failed"
    monkeypatch.setattr(delivery, "inject_text", inject)
    delivery.type_onto_dictation(" ", "type")
    assert rs.state.last_inserted_text == ""


def test_a_space_that_went_nowhere_leaves_undo_on_the_dictations_window(monkeypatch):
    """Typing takes the focused window for the dictation's before the first
    key. Given up before it (Claude Desktop's four spaces for "press tab",
    the key still held), "undo that" there would delete the other app's text."""
    monkeypatch.setattr(rs.state, "last_inserted_text", "See you at noon.")
    monkeypatch.setattr(rs.state, "last_inserted_window", "brave-browser")

    def inject(text, mode, into=None, **kw):
        """Takes the window, then gives up before typing."""
        rs.state.last_inserted_window = "claude"
        return "key_held"
    monkeypatch.setattr(delivery, "inject_text", inject)
    delivery.type_onto_dictation("    ", "type")
    assert rs.state.last_inserted_text == "See you at noon."
    assert rs.state.last_inserted_window == "brave-browser"

def test_a_phrase_cut_at_a_pause_takes_its_recordings_message_to_the_queue(chat, monkeypatch):
    """The phrase is queued when the pause is heard and typed later; the
    next recording may start in between. Taken when it's typed, the message
    was the new recording's."""
    focus, heard, typed, notices = chat
    jobs = []
    monkeypatch.setattr(dictation_worker, "_instance", types.SimpleNamespace(submit=jobs.append))
    monkeypatch.setattr(live, "chunk_cfg", types.SimpleNamespace(
        beeps=False, smart_quotes=False, notify=False, language=None, auto_space=True,
        auto_period=True, injection_mode="type"))
    next_recording(engine.SAMPLE_RATE)               # recording A
    first = rs.state.current.message
    pause = types.SimpleNamespace(tail_samples=lambda: 0, last_pause=1.0)
    monkeypatch.setattr(rs.state.current, "chunker", pause)
    monkeypatch.setattr(rs.state.current, "frames",
                        [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()])
    type_at_pause.cut_chunk(rs.state.current, pause)                           # "see you at noon", then a pause
    next_recording(engine.SAMPLE_RATE)               # recording B starts
    heard.append("See you at noon.")
    jobs[0]()
    assert first.has_words and not rs.state.current.message.has_words
