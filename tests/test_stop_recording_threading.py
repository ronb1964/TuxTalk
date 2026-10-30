"""stop_recording is split: the hotkey loop only ends the recording, and the
slow half (transcribing and typing) runs on the dictation worker, so a key
pressed meanwhile is read at once.

The correctness hinge is that each recording keeps its audio in a Recording
of its own, which finish_capture hands to the worker, so a new recording can't
clobber the audio being transcribed.
"""
import time

import numpy as np

from tuxtalk import recording_state as rs
from tuxtalk import tray_link
from tuxtalk import recording
from tuxtalk import pipeline


def test_finish_capture_snapshots_frames_surviving_next_recording(monkeypatch):
    monkeypatch.setattr(recording, "stop_stream_safely", lambda: None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *_a: None)
    monkeypatch.setattr(recording, "indicator", None)

    rs.state.current.frames = [b"hello-audio"]
    rs.state.current.press_t0 = time.time() - 1.0  # held well past MIN_HOLD_MS
    rs.state.is_recording = True
    rs.state.current.message.cancelled = False

    cap = recording.finish_capture(beeps_on=False, notify_on=False)
    assert cap is not None
    frames, _rec_sr = cap
    assert frames == [b"hello-audio"]
    assert rs.state.is_recording is False

    # The next recording starts at once and its microphone delivers audio:
    # the snapshot the worker will transcribe must not take it in.
    monkeypatch.setattr(recording, "follow_system_default", lambda idx: idx)
    monkeypatch.setattr(recording, "open_input_stream", lambda idx: None)
    monkeypatch.setattr("tuxtalk.type_at_pause.arm_chunker", lambda *a, **k: None)
    monkeypatch.setattr(recording, "pause_media_players", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    assert recording.start_recording(False, False, None)
    recording._sd_callback(rs.state.current, np.frombuffer(b"next", dtype=np.int16), 2, None, None)
    assert rs.state.current.frames == [b"next"]
    assert frames == [b"hello-audio"], "snapshot must survive a new recording"


def test_a_stream_that_didnt_stop_never_feeds_the_next_recording(monkeypatch):
    """The audio system can leave a stream running after TuxTalk gave up
    stopping it (mic_stream.close's time limit), and its blocks keep
    coming. The next recording, started while the last one was still being
    typed, took them in: the end of the last dictation was heard, and
    typed, again at the start of the next."""
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

    assert recording.start_recording(False, False, None)
    first = rs.state.current
    first.press_t0 = time.time() - 1.0
    callbacks[0](np.frombuffer(b"last", dtype=np.int16), 2, None, None)
    assert recording.finish_capture(beeps_on=False, notify_on=False) == ([b"last"], 16000)
    assert recording.start_recording(False, False, None)
    callbacks[0](np.frombuffer(b"late", dtype=np.int16), 2, None, None)
    callbacks[1](np.frombuffer(b"next", dtype=np.int16), 2, None, None)
    assert rs.state.current.frames == [b"next"]
    assert first.frames == [b"last"]


def test_finish_capture_returns_none_when_too_short(monkeypatch):
    monkeypatch.setattr(recording, "cancel_recording", lambda *a, **k: None)
    rs.state.current.press_t0 = time.time()  # ~0 ms held -> below MIN_HOLD_MS
    rs.state.is_recording = True
    assert recording.finish_capture(beeps_on=False, notify_on=False) is None


def test_stop_recording_composes_finish_then_inject(monkeypatch):
    # stop_recording = finish_capture, then transcribe_and_inject on the worker.
    got = []
    monkeypatch.setattr(recording, "finish_capture", lambda b, n: ([b"aud"], 16000))
    monkeypatch.setattr(pipeline, "transcribe_and_inject",
                        lambda frames, rec_sr, *a, **k: got.append((frames, rec_sr)))
    pipeline.stop_recording(beeps_on=False, smart_quotes=True, notify_on=False)
    assert got == [([b"aud"], 16000)]


def test_stop_recording_skips_inject_when_capture_none(monkeypatch):
    # Cancelled / too-short recording: no transcription or injection at all.
    got = []
    monkeypatch.setattr(recording, "finish_capture", lambda b, n: None)
    monkeypatch.setattr(pipeline, "transcribe_and_inject", lambda *a, **k: got.append(1))
    pipeline.stop_recording(beeps_on=False, smart_quotes=True, notify_on=False)
    assert got == []
