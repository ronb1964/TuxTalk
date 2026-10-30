"""stop_recording is split: the hotkey loop only ends the recording, and the
slow half (transcribing and typing) runs on the dictation worker, so a key
pressed meanwhile is read at once.

The correctness hinge is that each recording keeps its audio in a Recording
of its own, which finish_capture hands to the worker, so a new recording can't
clobber the audio being transcribed.
"""
import threading
import time
import types

import numpy as np
import pytest

from tuxtalk import recording_state as rs
from tuxtalk import service_tick
from tuxtalk import tray_link
from tuxtalk import recording
from tuxtalk import pipeline


def test_finish_capture_snapshots_frames_surviving_next_recording(monkeypatch):
    monkeypatch.setattr(recording, "stop_stream_safely", lambda rec: None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *_a: None)
    monkeypatch.setattr(recording, "indicator", None)

    rs.state.current.frames = [b"hello-audio"]
    rs.state.current.press_t0 = time.time() - 1.0  # held well past MIN_HOLD_MS
    rs.state.is_recording = True
    rs.state.current.message.cancelled = False

    cap = recording.finish_capture(rs.state.current, beeps_on=False, notify_on=False)
    assert cap is not None
    frames, _rec_sr = cap
    assert frames == [b"hello-audio"]
    assert rs.state.is_recording is False

    # The next recording starts at once and its microphone delivers audio:
    # the snapshot the worker will transcribe must not take it in.
    monkeypatch.setattr(recording, "follow_system_default", lambda idx: idx)
    monkeypatch.setattr(recording, "open_input_stream", lambda rec, idx: None)
    monkeypatch.setattr("tuxtalk.type_at_pause.arm_chunker", lambda *a, **k: None)
    monkeypatch.setattr(recording, "pause_media_players", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    assert recording.start_recording(False, False, None)
    recording._sd_callback(rs.state.current, np.frombuffer(b"next", dtype=np.int16), 2, None, None)
    assert rs.state.current.frames == [b"next"]
    assert frames == [b"hello-audio"], "snapshot must survive a new recording"


@pytest.fixture
def streams(monkeypatch):
    """Microphone streams that never stop (mic_stream.close gives up on
    them), with no sound devices: each stream's audio callback, in the
    order the recordings opened them."""
    callbacks = []

    class Stream:
        def __init__(self, callback, **_kw):
            callbacks.append(callback)

        def start(self):
            pass
    monkeypatch.setattr(recording.sd, "InputStream", Stream)
    monkeypatch.setattr(recording.mic_stream, "input_samplerate", lambda idx, rate: rate)
    monkeypatch.setattr(recording.mic_stream, "close", lambda stream: False)   # stuck
    monkeypatch.setattr(recording, "follow_system_default", lambda idx: idx)
    monkeypatch.setattr(recording, "pause_media_players", lambda *a, **k: None)
    monkeypatch.setattr(recording, "resume_media_players", lambda: None)
    monkeypatch.setattr(recording, "indicator", None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *_a: None)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    return callbacks


def _block(data):
    return np.frombuffer(data, dtype=np.int16)


def _held_long_enough():
    rs.state.current.press_t0 = time.time() - 1.0


def test_a_stream_that_didnt_stop_never_feeds_the_next_recording(streams):
    """The audio system can leave a stream running after TuxTalk gave up
    stopping it (mic_stream.close's time limit), and its blocks keep
    coming. The next recording, started while the last one was still being
    typed, took them in: the end of the last dictation was heard, and
    typed, again at the start of the next."""
    assert recording.start_recording(False, False, None)
    first = rs.state.current
    _held_long_enough()
    streams[0](_block(b"last"), 2, None, None)
    assert recording.finish_capture(rs.state.current, beeps_on=False,
                                    notify_on=False) == ([b"last"], 16000)
    assert recording.start_recording(False, False, None)
    streams[0](_block(b"late"), 2, None, None)
    streams[1](_block(b"next"), 2, None, None)
    assert rs.state.current.frames == [b"next"]
    assert first.frames == [b"last"]


def test_a_block_still_arriving_at_the_stop_never_changes_the_audio_handed_on(streams):
    """A block the audio thread was still taking in when the key came up
    was added to the frames already handed to the dictation worker, so
    what it transcribed depended on timing."""
    assert recording.start_recording(False, False, None)
    _held_long_enough()
    streams[0](_block(b"said"), 2, None, None)
    inside, go = threading.Event(), threading.Event()

    class SlowBlock:
        def tobytes(self):
            inside.set()
            go.wait(5)
            return b"late"
    late = threading.Thread(target=streams[0], args=(SlowBlock(), 2, None, None))
    late.start()
    assert inside.wait(5)
    frames, _rate = recording.finish_capture(rs.state.current, beeps_on=False, notify_on=False)
    handed_on = list(frames)
    go.set()
    late.join(5)
    assert frames == handed_on == [b"said"]


def test_hands_free_finishing_a_recording_never_stops_the_next_one(streams, monkeypatch):
    """The audio thread can be partway through a block when the recording
    stops and the next one starts. Hands-free's "the speaker went quiet",
    heard in that block, stopped the new recording, and what the user went
    on to say was lost."""
    stopped = []
    monkeypatch.setattr(pipeline, "stop_recording", lambda *a: stopped.append(a))
    inside, go = threading.Event(), threading.Event()

    class FinishingDetector:
        nothing_said = False

        def feed(self, block):
            inside.set()
            go.wait(5)
            return True

        def tail_to_trim(self):
            return 0
    cfg = types.SimpleNamespace(beeps=False, smart_quotes=False, notify=False, language=None,
                                auto_space=True, auto_period=True, injection_mode="type")
    svc = types.SimpleNamespace(last_activity=0.0)
    assert recording.start_recording(False, False, None)
    _held_long_enough()
    rs.state.current.silence = FinishingDetector()
    quiet = threading.Thread(target=streams[0], args=(_block(b"hush"), 2, None, None))
    quiet.start()
    assert inside.wait(5)
    assert recording.finish_capture(rs.state.current, beeps_on=False, notify_on=False) is not None
    assert recording.start_recording(False, False, None)
    go.set()
    quiet.join(5)
    service_tick._take_stop_request(cfg, svc, time.time())
    assert stopped == [] and rs.state.is_recording
    rs.state.current.went_quiet = True          # the new recording's own hands-free stop
    service_tick._take_stop_request(cfg, svc, time.time())
    assert len(stopped) == 1


def test_finish_capture_returns_none_when_too_short(monkeypatch):
    monkeypatch.setattr(recording, "cancel_recording", lambda *a, **k: None)
    rs.state.current.press_t0 = time.time()  # ~0 ms held -> below MIN_HOLD_MS
    rs.state.is_recording = True
    assert recording.finish_capture(rs.state.current, beeps_on=False, notify_on=False) is None


def test_stop_recording_composes_finish_then_inject(monkeypatch):
    # stop_recording = finish_capture, then transcribe_and_inject on the worker.
    got = []
    monkeypatch.setattr(recording, "finish_capture", lambda rec, b, n: ([b"aud"], 16000))
    monkeypatch.setattr(pipeline, "transcribe_and_inject",
                        lambda frames, rec_sr, *a, **k: got.append((frames, rec_sr)))
    pipeline.stop_recording(beeps_on=False, smart_quotes=True, notify_on=False)
    assert got == [([b"aud"], 16000)]


def test_stop_recording_skips_inject_when_capture_none(monkeypatch):
    # Cancelled / too-short recording: no transcription or injection at all.
    got = []
    monkeypatch.setattr(recording, "finish_capture", lambda rec, b, n: None)
    monkeypatch.setattr(pipeline, "transcribe_and_inject", lambda *a, **k: got.append(1))
    pipeline.stop_recording(beeps_on=False, smart_quotes=True, notify_on=False)
    assert got == []
