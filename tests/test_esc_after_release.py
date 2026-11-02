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
from tuxtalk import (delivery, dictation_worker, engine, hotkeys, pipeline, recording,
                     send_countdown, tray_link, type_at_pause)
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
