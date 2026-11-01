"""Synthetic audio and a fake engine and typing around the real recording and
pipeline, for the Type at pause tests (test_chunking.py and the files split
from it). Each test file wraps these in its own fixtures.

Audio is built like test_silence.py's: quiet room noise and louder "speech"
(noise plus a tone, with syllable gaps), fed in 20 ms blocks.
"""
from types import SimpleNamespace

import numpy as np

from conftest import next_recording
from tuxtalk import delivery, engine, tray_link
from tuxtalk import live_settings as live
from tuxtalk import pipeline as dictation_pipeline
from tuxtalk import recording as capture
from tuxtalk import recording_state as rs

SR = 16000
BLOCK = 320   # 20 ms at 16 kHz
rng = np.random.default_rng(1)


def noise(seconds, level=60.0, sr=SR):
    return (rng.normal(0, level, int(seconds * sr))).astype(np.int16)


def speech(seconds, level=3000.0, noise_level=60.0, sr=SR):
    """Syllables: 180 ms of voice, then a 70 ms gap where only the room is heard."""
    t = np.arange(int(seconds * sr)) / sr
    voiced = (t % 0.25) < 0.18
    tone = np.sin(2 * np.pi * 220 * t) * level * voiced
    return (tone + rng.normal(0, noise_level, t.size)).astype(np.int16)


def blocks(audio, block=BLOCK):
    for i in range(0, len(audio), block):
        yield audio[i:i + block]


def fake_pipeline(monkeypatch):
    """A fake engine and fake typing around the real pipeline.transcribe_and_inject.
    texts: what the engine 'hears', one per call, in call order (a chunk after
    a held-back one is transcribed alone, then together with it).
    Returns (texts, typed, beeps)."""
    from tuxtalk import history
    texts, typed, beeps = [], [], []
    monkeypatch.setattr(engine, "transcribe", lambda audio, lang: texts.pop(0))

    def fake_inject(text, mode, one_block=False, into=None, message=None):
        typed.append(text)
        rs.state.last_inserted_text = text
        if message is not None:
            rs.state.last_message = message
            message.add("ok", message.to)       # into the window its words went to
        return "ok"

    monkeypatch.setattr(delivery, "inject_text", fake_inject)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda enabled, *tone: beeps.append(tone))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    monkeypatch.setattr(live, "adapt_to_app", False)
    monkeypatch.setattr(live, "usage_stats", False)
    monkeypatch.setattr(history, "add_entry", lambda text, replaces=None: None)
    monkeypatch.setattr(rs.state, "continue_mid_sentence", False)
    monkeypatch.setattr(rs.state.current.message, "held_mark", None)
    monkeypatch.setattr(rs.state.current.message, "sentence_audio", [])
    monkeypatch.setattr(rs.state, "last_inserted_text", "")
    return texts, typed, beeps


def start_chunked_recording(monkeypatch):
    """A recording in progress at 16 kHz with chunking on and no real stream.
    The caller sets rs.state.is_recording back to False afterwards."""
    monkeypatch.setattr(live, "chunk_pause_seconds", 0.8)
    monkeypatch.setattr(live, "chunk_cfg", SimpleNamespace(
        beeps=True, smart_quotes=True, notify=False, language=None,
        auto_space=True, auto_period=False, injection_mode="type"))
    monkeypatch.setattr(capture, "indicator", None)
    monkeypatch.setattr(capture, "stop_stream_safely", lambda rec: None)
    monkeypatch.setattr(capture, "resume_media_players", lambda: None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *a: None)
    rs.state.is_recording = True
    next_recording(SR)


def feed_block(block):
    """One block from the microphone, through the real audio callback."""
    capture._sd_callback(rs.state.current, block.reshape(-1, 1), len(block), None, None)


def feed(audio):
    for b in blocks(audio):
        feed_block(b)


def frames_of(audio):
    """*audio* as the one frame a phrase hands the pipeline."""
    return [audio.tobytes()]


def type_chunk(frames, role, auto_period=False):
    """One Type at pause phrase through the real pipeline, as *role*
    ("first", "middle", "last")."""
    dictation_pipeline.transcribe_and_inject(frames, SR, True, True, False, None, True,
                                             auto_period, "type", chunk_role=role,
                                             message=rs.state.current.message)


def stop(auto_period=False):
    dictation_pipeline.stop_recording(beeps_on=True, smart_quotes=True, notify_on=False,
                                      language=None, auto_space=True,
                                      auto_period=auto_period, injection_mode="type")
