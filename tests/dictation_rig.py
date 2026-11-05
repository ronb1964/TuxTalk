"""The dictation worker's rig for tests: a busy worker whose jobs the test
runs itself, the Esc key as the hotkey loop handles it, and a dictation's
key coming up. conftest.py re-exports them, so tests import them from there
and the fixture is found everywhere."""
import threading
import types

import numpy as np
import pytest
from evdev import ecodes

from tuxtalk import engine, hotkeys, recording
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs


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


# The settings the hotkey loop passes along when Esc is pressed (esc).
_ESC_CFG = types.SimpleNamespace(beeps=False, notify=False, smart_quotes=False, language=None,
                                 auto_space=True, auto_period=True, injection_mode="type")


def esc():
    """Esc pressed, through the hotkey loop's handler."""
    event = types.SimpleNamespace(type=ecodes.EV_KEY, code=ecodes.KEY_ESC, value=1)
    hotkeys._handle_key_event(event, ecodes.KEY_F8, ecodes.KEY_F9, None, set(), [], _ESC_CFG, 0)


@pytest.fixture
def let_go(monkeypatch):
    """let_go(): a dictation's key comes up, and its audio goes to the
    dictation worker. Returns its recording."""
    from tuxtalk import pipeline, tray_link
    monkeypatch.setattr(rs.state, "is_recording", False)
    monkeypatch.setattr(recording, "indicator", None)
    monkeypatch.setattr(recording, "stop_stream_safely", lambda rec: None)
    monkeypatch.setattr(recording, "resume_media_players", lambda: None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *a: None)
    monkeypatch.setattr(live, "chunk_pause_seconds", 0)

    def stop():
        from conftest import next_recording    # conftest imports this module
        rec = next_recording()
        rec.frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
        rs.state.is_recording = True
        pipeline.stop_recording(False, False, False)
        return rec
    return stop
