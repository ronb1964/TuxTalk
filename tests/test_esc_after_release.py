"""Esc after the dictation key is let go, while what was said is still
being transcribed or typed: what isn't in yet stays out, and no Enter
follows it. Once it's all in, Esc is the app's again."""
import threading
import types

import numpy as np
import pytest
from evdev import ecodes

from conftest import CHAT_WINDOW, next_recording
from type_at_pause_rig import fake_pipeline, frames_of, speech, type_chunk
from tuxtalk import (delivery, dictation_worker, engine, hotkeys, keyboard_output, pipeline,
                     recording, send_countdown, tray_link, type_at_pause)
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs

CFG = types.SimpleNamespace(beeps=False, notify=False, smart_quotes=False, language=None,
                            auto_space=True, auto_period=True, injection_mode="type")


class QueuedWorker:
    """The dictation worker, busy: jobs wait until run() runs them, on the
    test's thread, which then counts as the worker's (a key pressed from a
    job runs within it, as dictation_worker.call does)."""

    thread = None

    def __init__(self):
        self.jobs = []

    def submit(self, job):
        self.jobs.append(job)

    def idle(self):
        return not self.jobs

    def run(self):
        self.thread = threading.current_thread()
        while self.jobs:
            self.jobs.pop(0)()


def esc():
    """Esc pressed, through the hotkey loop's handler."""
    event = types.SimpleNamespace(type=ecodes.EV_KEY, code=ecodes.KEY_ESC, value=1)
    hotkeys._handle_key_event(event, ecodes.KEY_F8, ecodes.KEY_F9, None, set(), [], CFG, 0)


@pytest.fixture
def let_go(monkeypatch):
    """let_go(): a dictation's key comes up, and its audio goes to the
    dictation worker. Returns its recording."""
    monkeypatch.setattr(rs.state, "is_recording", False)
    monkeypatch.setattr(recording, "indicator", None)
    monkeypatch.setattr(recording, "stop_stream_safely", lambda rec: None)
    monkeypatch.setattr(recording, "resume_media_players", lambda: None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *a: None)
    monkeypatch.setattr(live, "chunk_pause_seconds", 0)

    def stop():
        rec = next_recording()
        rec.frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
        rs.state.is_recording = True
        pipeline.stop_recording(False, False, False)
        return rec
    return stop


def test_esc_before_the_words_are_heard_types_nothing(chat, keyboard, let_go, monkeypatch):
    focus, heard, typed, notices = chat
    worker = QueuedWorker()
    monkeypatch.setattr(dictation_worker, "_instance", worker)
    heard.append("see you at noon")
    let_go()
    esc()
    worker.run()
    assert typed == [] and keyboard.events == []
    assert heard == ["see you at noon"]                # never even heard


def test_esc_while_the_typing_gets_ready_types_nothing(chat, keyboard, let_go, monkeypatch):
    """Esc a moment after the words were checked, while the delivery looks
    at the focused window and picks how to type: Esc came 0.3 s after the
    key and the words went in all the same, only the send held. Its first
    key is the last moment to stop it."""
    from tuxtalk import focused_window
    focus, heard, typed, notices = chat
    destination = focused_window.destination

    def esc_meanwhile():
        esc()
        return destination()

    def type_for_real(text, mode, one_block=False, *, where):
        typed.append((text, focus[1]))
        keyboard_output.type_text_raw(text)
        return "ok"
    monkeypatch.setattr(focused_window, "destination", esc_meanwhile)
    monkeypatch.setattr(delivery, "deliver_text", type_for_real)
    heard.append("see you at noon")
    rec = let_go()
    assert rec.message.cancelled
    assert keyboard.events == [] and notices == []     # nothing typed, so nothing to send


def test_esc_while_the_words_go_in_sends_nothing_after_them(chat, keyboard, let_go,
                                                            monkeypatch):
    """The words already going in are typed to the end; the automatic
    Enter after them doesn't go, and the notice says why."""
    focus, heard, typed, notices = chat
    deliver = delivery.deliver_text

    def deliver_then_esc(*a, **k):
        outcome = deliver(*a, **k)
        esc()
        return outcome
    monkeypatch.setattr(delivery, "deliver_text", deliver_then_esc)
    heard.append("see you at noon")
    let_go()
    assert typed == [("See you at noon.", CHAT_WINDOW[1])]
    assert keyboard.typed_keys() == [] and notices == [send_countdown.CANCELLED_NOTICE]


def test_esc_while_a_spoken_enter_waits_its_turn_sends_nothing(chat, keyboard, let_go,
                                                              monkeypatch):
    """"see you at noon, press enter", then Esc while the Enter waits for
    the app to take in the words: the message stays unsent."""
    focus, heard, typed, notices = chat
    press = keyboard_output.press_key

    def esc_then_press(*a, **k):
        esc()
        return press(*a, **k)
    monkeypatch.setattr(keyboard_output, "press_key", esc_then_press)
    heard.append("see you at noon, press enter")
    let_go()
    assert typed == [("See you at noon.", CHAT_WINDOW[1])]
    assert keyboard.typed_keys() == [] and notices == [send_countdown.CANCELLED_NOTICE]


def test_esc_once_the_dictation_is_in_cancels_nothing(chat, keyboard, let_go, monkeypatch):
    focus, heard, typed, notices = chat
    beeped = []
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: beeped.append(a))
    heard.append("see you at noon")
    rec = let_go()
    sent = keyboard.typed_keys()
    esc()
    assert sent == [ecodes.KEY_ENTER] and not rec.message.cancelled and len(beeped) == 1


def test_esc_before_a_type_at_pause_recording_ends_types_no_held_cue(monkeypatch):
    """"meet at three" (pause) "no wait" (pause), the key let go, then Esc:
    the "No wait." held for the next phrase isn't typed after all."""
    texts, typed, beeps = fake_pipeline(monkeypatch)
    texts.append("meet at three")
    type_chunk(frames_of(speech(1.0)), "middle")
    message = rs.state.current.message
    monkeypatch.setattr(message, "held_cue", "No wait")
    monkeypatch.setattr(message, "cancelled", True)         # Esc
    before, beeped = list(typed), list(beeps)
    type_at_pause.end_in_a_pause(message, True, True, True, "type", None)
    assert typed == before and beeps == beeped


def test_esc_partway_through_a_dictation_brings_no_paste_it_notice(monkeypatch):
    """Esc while the first of two lines goes in: the second stays out, and
    no "only part of it was typed, paste it" notice argues with the Esc."""
    from tuxtalk import notices
    told = []
    monkeypatch.setattr(notices, "report_undelivered", lambda *a: told.append(a))
    message = rs.Message()
    message.cancelled = True
    pipeline._after_the_words("partial", "", "ENTER", " ", None, False, "type", message)
    assert told == []


def test_esc_before_the_first_key_keeps_what_undo_knows(monkeypatch):
    """The dictation before is still in the app as it was: "delete last
    sentence" must still find it."""
    from tuxtalk import virtual_keyboard
    monkeypatch.setattr(rs.state, "last_inserted_text", "Meet at noon. ")
    outcome = delivery._delivery_stopped(virtual_keyboard.GaveUp("cancelled"), typed_some=False)
    assert outcome == "cancelled" and rs.state.last_inserted_text == "Meet at noon. "


def test_a_line_typed_through_wtype_counts_as_typed(monkeypatch):
    """Esc before the next line after one went in through wtype is partway,
    not "nothing typed": that line is in the app."""
    from tuxtalk import keyboard_layout

    def no_layout(text):
        raise OSError("no libxkbcommon")

    class Wtype:
        returncode = 0

        def __init__(self, *a, **k):
            pass

        def communicate(self, data=None, timeout=None):
            return b"", b""
    monkeypatch.setattr(keyboard_layout, "layout_key_events", no_layout)
    monkeypatch.setattr(keyboard_output, "which", lambda name: "/usr/bin/wtype")
    monkeypatch.setattr(keyboard_output.subprocess, "Popen", Wtype)
    before = keyboard_output.this_thread.sends
    assert keyboard_output.type_text_raw("Meet at noon.") is True
    assert keyboard_output.this_thread.sends == before + 1


def test_a_voice_edit_esc_stops_at_its_first_key_and_says_nothing_more(monkeypatch):
    """Esc while the rewrite got ready to go in: the same check as for a
    dictation, and no notice or beep after Esc's own."""
    from tuxtalk import notices, voice_edit
    message = rs.Message()
    watched, told = [], []

    def inject(text, mode, one_block=False, into=None, message=None):
        watched.append(keyboard_output.this_thread.cancel_with)
        return "cancelled"
    monkeypatch.setattr(delivery, "inject_text", inject)
    monkeypatch.setattr(notices, "report_undelivered", lambda *a: told.append(a))
    monkeypatch.setattr(notices, "edit_problem", lambda *a: told.append(a))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: told.append(a))
    assert voice_edit._type_rewrite("Rewritten.", "type", message, True, None) == "cancelled"
    assert watched == [message] and told == []


def test_esc_before_the_first_key_keeps_the_window_undo_goes_to(monkeypatch):
    """Dictated into one app, then Esc on the next in another before its
    first key: undo of the first must still point at the first app, not
    reach into the second (deliver_text takes the window early)."""
    from tuxtalk import focused_window, virtual_keyboard
    monkeypatch.setattr(focused_window, "destination", lambda: None)
    monkeypatch.setattr(rs.state, "last_inserted_window", "kwrite")
    monkeypatch.setattr(rs.state, "last_inserted_where", ("kwrite", 7))

    def take_window_then_esc(text, mode, one_block=False, *, where):
        rs.state.last_inserted_window = "org.kde.konsole"
        rs.state.last_inserted_where = ("org.kde.konsole", 9)
        raise virtual_keyboard.GaveUp("cancelled")
    monkeypatch.setattr(delivery, "deliver_text", take_window_then_esc)
    assert delivery.inject_text("Hello.", "type") == "cancelled"
    assert (rs.state.last_inserted_window, rs.state.last_inserted_where) == ("kwrite", ("kwrite", 7))


def test_esc_before_a_press_enter_said_alone_presses_nothing(chat, keyboard, let_go,
                                                           monkeypatch):
    """Only "press enter" said, then Esc while the Enter waited its turn: it
    went all the same, sending whatever was in the box, and Esc's own beep
    is all that's said."""
    focus, heard, typed, notices = chat
    told = []
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: told.append(a))
    press = keyboard_output.press_key

    def esc_then_press(*a, **k):
        esc()
        return press(*a, **k)
    monkeypatch.setattr(keyboard_output, "press_key", esc_then_press)
    heard.append("press enter")
    let_go()
    assert keyboard.events == [] and notices == [] and told == []


def test_esc_before_a_press_tab_said_alone_types_no_spaces(chat, keyboard, let_go, monkeypatch):
    """Claude Desktop gets four spaces for "press tab": Esc while they got
    ready to go in, and they went in all the same."""
    from tuxtalk import focused_window
    focus, heard, typed, notices = chat
    focus[0] = "com.anthropic.Claude"
    destination = focused_window.destination

    def esc_meanwhile():
        esc()
        return destination()

    def type_for_real(text, mode, one_block=False, *, where):
        typed.append((text, focus[1]))
        keyboard_output.type_text_raw(text)
        return "ok"
    monkeypatch.setattr(focused_window, "destination", esc_meanwhile)
    monkeypatch.setattr(delivery, "deliver_text", type_for_real)
    heard.append("press tab")
    let_go()
    assert keyboard.events == [] and notices == []


def test_esc_on_a_dictation_cut_off_on_and_leaves_the_next_one_capitalised(chat, keyboard,
                                                                          let_go, monkeypatch):
    """"meet me at noon and", then Esc before its first key: nothing went in,
    yet the next dictation began in lowercase, carrying on a sentence that
    isn't on the screen."""
    from tuxtalk import focused_window
    focus, heard, typed, notices = chat
    destination = focused_window.destination
    escs = [esc]

    def esc_meanwhile():
        if escs:
            escs.pop()()
        return destination()

    def type_for_real(text, mode, one_block=False, *, where):
        keyboard_output.type_text_raw(text)
        typed.append((text, focus[1]))
        rs.state.last_inserted_text = text
        return "ok"
    monkeypatch.setattr(focused_window, "destination", esc_meanwhile)
    monkeypatch.setattr(delivery, "deliver_text", type_for_real)
    heard.extend(["meet me at noon and", "see you there"])
    let_go()
    assert typed == [] and not rs.state.continue_mid_sentence
    let_go()
    assert typed == [("See you there.", CHAT_WINDOW[1])]


def _esc_before_send(monkeypatch, nth):
    """Esc while the *nth* send of keys (from 1) waits its turn."""
    sends = []

    def wait_while_held(destination=None):
        sends.append(1)
        if len(sends) == nth:
            esc()
        return lambda: None
    monkeypatch.setattr("tuxtalk.modifier_hotkeys.wait_while_held", wait_while_held)


def test_esc_before_clear_everything_clears_nothing(chat, keyboard, let_go, monkeypatch):
    """"clear everything", then Esc while its Ctrl+A waited: the whole box
    was deleted all the same, and only Esc's own beep says anything."""
    focus, heard, typed, notices = chat
    told = []
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: told.append(a))
    _esc_before_send(monkeypatch, 1)
    heard.append("clear everything")
    let_go()
    assert keyboard.events == [] and told == []


def test_esc_between_select_all_and_backspace_still_clears(chat, keyboard, let_go, monkeypatch):
    """Ctrl+A went in, then Esc: the Backspace still goes. Kept back, it
    would leave the whole box selected for the next key typed to replace."""
    focus, heard, typed, notices = chat
    _esc_before_send(monkeypatch, 2)
    heard.append("clear everything")
    let_go()
    assert ecodes.KEY_BACKSPACE in [code for code, down in keyboard.events if down]
