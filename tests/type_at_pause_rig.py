"""Audio and a fake engine and typing around the real recording and
pipeline, for the Type at pause tests (test_chunking.py and the files split
from it). Each test file wraps these in its own fixtures.

Audio is quiet room noise and a person reading (tests/data/README.md), fed in
20 ms blocks: what is speech is a voice detector's call, so it has to be a
voice.
"""
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import audio_rng, next_recording
from tuxtalk import delivery, engine, resampling, tray_link
from tuxtalk import live_settings as live
from tuxtalk import pipeline as dictation_pipeline
from tuxtalk import recording as capture
from tuxtalk import recording_state as rs

SR = 16000
BLOCK = 320   # 20 ms at 16 kHz
rng = audio_rng(1)


def noise(seconds, level=60.0, sr=SR):
    return (rng.normal(0, level, int(seconds * sr))).astype(np.int16)


def _reading():
    """The test sentence without the quiet before and after it, as floats
    at its own loudness 1: from its first to its last 32 ms that is within
    26 dB of its loud part."""
    with wave.open(str(Path(__file__).with_name("data") / "librispeech-7902-96591-0018.wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float64)
    n = len(x) // 512
    r = np.sqrt(np.mean(x[:n * 512].reshape(n, 512) ** 2, axis=1))
    on = np.nonzero(r > np.percentile(r, 95) / 20)[0]
    x = x[on[0] * 512:(on[-1] + 1) * 512]
    return x / np.sqrt(np.mean(x ** 2))


READING = _reading()


def speech(seconds, level=1800.0, noise_level=60.0, sr=SR):
    """*seconds* of a person reading, over and over, at int16 RMS *level*,
    in room noise of *noise_level*. It never stops for longer than a
    breath, so a pause is only where a test puts one."""
    n = int(seconds * sr)
    voice = READING if sr == SR else resampling.resample(READING, SR, sr)
    reading = np.tile(voice, n // len(voice) + 1)[:n] * level
    return np.clip(reading + rng.normal(0, noise_level, n), -32768, 32767).astype(np.int16)


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


# Notifications on: ending the recording after a command still adds none.
SERVICE_CFG = SimpleNamespace(beeps=True, notify=True)


def service_tick_runs():
    """One pass of the dictation service's tick, as its loop runs it."""
    from tuxtalk import service_tick
    service_tick.tick(SERVICE_CFG, service_tick.ServiceState(SERVICE_CFG))


@pytest.fixture
def recording_on(chat, monkeypatch):
    """A Type at pause recording under way, its phrases typed as they come.
    Returns (recording, phrase); phrase(*heard) is one phrase cut at a pause:
    what the engine hears of it, and of it with the phrase before when that
    held back its final mark."""
    monkeypatch.setattr(capture, "indicator", None)
    monkeypatch.setattr(capture, "stop_stream_safely", lambda rec: None)
    monkeypatch.setattr(capture, "resume_media_players", lambda: None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *a: None)
    monkeypatch.setattr(rs.state, "is_recording", True)
    rec = next_recording()

    def phrase(*heard):
        chat[1].extend(heard)
        type_chunk(frames_of(speech(1.0)), "middle")
    return rec, phrase
