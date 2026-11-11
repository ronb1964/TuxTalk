"""Live text (TuxTalk Labs): the words so far, shown in a small window
(live_text_window.py) while the user is still talking.

About once a second, when the dictation worker has nothing else to do, the
audio not yet typed is read again with the model already loaded, quietly
(engine.quietly). With Type at pause that's the phrase still being said;
without it, the whole dictation so far. What gets typed doesn't change: that
is still the same model on the same audio, once the user stops or pauses.
A read never waits in the worker's queue ahead of real work: it's only
started when the worker is idle, and one at a time.
"""
import threading
import time

from . import dictation_worker, engine, recording, resampling, type_at_pause
from . import live_settings as live
from . import recording_state as rs
from .logger import setup_logger

logger = setup_logger(__name__)

# How often the words are read again, at most; slower when a read takes
# longer (only one runs at a time).
INTERVAL_S = 1.0
# Less audio than this holds a word or two at most: not worth a read.
SHORTEST_S = 0.5

# The window, made on GTK's thread at the service's start (app), or None
# where it can't be shown (no X display) or before then.
window = None
# A read is waiting or running on the worker.
_reading = threading.Event()
_last_read = 0.0


def tick(now=None):
    """From the service's loop on each pass: start a read when one is due."""
    global _last_read
    now = time.time() if now is None else now
    rec = rs.state.current
    if not _due(rec, now):
        return
    with type_at_pause.chunk_lock:
        frames = list(rec.frames)
    if len(frames) == 0:
        return
    _last_read = now
    _reading.set()
    dictation_worker.submit(lambda: _read(rec, frames, rec.samplerate))


def _due(rec, now):
    """Whether a read should start now for *rec*, the recording running."""
    return (window is not None and live.live_text and rs.state.is_recording
            and rec is not None and not rec.edit_mode and not _reading.is_set()
            and now - _last_read >= INTERVAL_S and dictation_worker.idle())


def _read(rec, frames, rate):
    """On the dictation worker: transcribe *frames* and show the words, if
    *rec* is still the recording running."""
    try:
        audio = recording.frames_f32(frames)
        if audio.size < SHORTEST_S * rate:
            return
        if rate != engine.SAMPLE_RATE:
            audio = resampling.resample(audio, rate, engine.SAMPLE_RATE)
        cfg = live.chunk_cfg
        with engine.quietly():
            words = engine.transcribe(audio, cfg.language if cfg is not None else None)
        if words:
            window.show(words, rec)
    finally:
        _reading.clear()


def hide():
    """The recording ended: the window goes (the text is typed now)."""
    global _last_read
    _last_read = 0.0
    if window is not None:
        window.hide()
