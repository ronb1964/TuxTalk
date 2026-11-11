"""Live text (TuxTalk Labs): the words so far, shown in a small window
(live_text_window.py) while the user is still talking.

About once a second the audio not yet typed is read again with the model
already loaded, on the dictation worker, with its log lines left out
(logger.quietly). With Type at pause that's the phrase still being said;
without it, the dictation so far, its last MAX_AUDIO_S at most. What gets
typed doesn't change: that is still the whole audio, read once the user
stops or pauses.

The typed text must never wait on a preview. A read goes to the worker
only when it has nothing else to do (submit_if_idle), and does nothing if
the phrase it was handed was cut, or the recording stopped, while it
waited. A read already running can't be stopped, so each is kept short: one
that takes longer than READ_BUDGET_S ends live text for the rest of that
phrase. The typed text then waits for one read at most.

Words said into a password field never show: the check that keeps them
from being typed (focused_window.is_password) runs before every read, and
the box closes while the cursor is in one.
"""
import threading
import time

from . import dictation_worker, engine, focused_window, recording, resampling
from . import live_settings as live
from . import recording_state as rs
from . import type_at_pause, x11_desktop
from .logger import quietly, setup_logger

logger = setup_logger(__name__)

# How often the words are read again, at most.
INTERVAL_S = 1.0
# Less audio than this holds a word or two at most: not worth a read.
SHORTEST_S = 0.5
# The most a read may take. On a graphics card a minute of speech is read
# in under a fifth of a second; on a processor a long phrase takes more.
READ_BUDGET_S = 0.5
# The newest audio a read looks at, which bounds how long a read takes
# without Type at pause, where the audio grows for the whole dictation.
MAX_AUDIO_S = 60.0

# The window, made on GTK's thread at the service's start (app); None where
# it can't be shown without taking the keyboard (no X display).
window = None
# A read is waiting or running on the worker.
_reading = threading.Event()
_last_read = 0.0
# (recording, phrase number) a read took too long for, and the one whose
# words the window shows; the phrase number is rec.chunks_submitted.
_too_slow = None
_shown = None


def tick(now=None):
    """From the service's loop on each pass: start a read when one is due."""
    global _last_read
    now = time.time() if now is None else now
    rec = rs.state.current
    if not _wanted(rec):
        return
    _close_after_a_cut(rec)
    if _reading.is_set() or now - _last_read < INTERVAL_S:
        return
    with type_at_pause.chunk_lock:
        frames, phrase = list(rec.frames), rec.chunks_submitted
    if not frames or (rec, phrase) == _too_slow:
        return
    _last_read = now
    _reading.set()
    if not dictation_worker.submit_if_idle(lambda: _read(rec, frames, rec.samplerate, phrase)):
        _reading.clear()


def _wanted(rec):
    """Whether live text is on and *rec*, the recording running, is dictation."""
    return (window is not None and live.live_text and rs.state.is_recording
            and rec is not None and not rec.edit_mode)


def _close_after_a_cut(rec):
    """Type at pause typed the phrase the window shows: its words go."""
    if _shown is not None and _shown[0] is rec and _shown[1] != rec.chunks_submitted:
        _close()


def _current(rec, phrase):
    """Whether *phrase* of *rec* is still the audio being said."""
    return rs.state.current is rec and rs.state.is_recording and rec.chunks_submitted == phrase


def _read(rec, frames, rate, phrase):
    """On the dictation worker: transcribe *frames*, phrase *phrase* of
    *rec*, and show the words, unless it's too late or too private."""
    global _too_slow
    try:
        if not _current(rec, phrase):
            return
        if focused_window.is_password() or x11_desktop.popup_ends_show_desktop():
            _close()
            return
        audio = _newest_audio(frames, rate)
        if audio is None:
            return
        started = time.monotonic()
        with quietly():
            words = engine.transcribe(audio, getattr(live.settings, "language", None))
        took = time.monotonic() - started
        if took > READ_BUDGET_S:
            _too_slow = (rec, phrase)
            _close()
            logger.info(f"Live text stops for this phrase: reading it took {took:.1f}s")
            return
        if _current(rec, phrase):           # not typed or stopped while it was read
            _show(rec, phrase, words)
    finally:
        _reading.clear()


def _newest_audio(frames, rate):
    """The last MAX_AUDIO_S of *frames* at the engine's rate, or None when
    it's too short to hold a word."""
    audio = recording.frames_f32(frames)[-int(MAX_AUDIO_S * rate):]
    if audio.size < SHORTEST_S * rate:
        return None
    if rate != engine.SAMPLE_RATE:
        audio = resampling.resample(audio, rate, engine.SAMPLE_RATE)
    return audio


def _show(rec, phrase, words):
    """Put *words* up, without the fillers when they'd be left out of the
    typed text; nothing heard closes the window, so no old words stay."""
    global _shown
    if words and live.remove_fillers:
        from .cleanup import clean_fillers_and_repeats
        words = clean_fillers_and_repeats(words)
    if not words:
        _close()
        return
    _shown = (rec, phrase)
    window.show(words, rec, phrase)


def _close():
    """Take the window down; the next read opens it again."""
    global _shown
    _shown = None
    window.hide()


def hide():
    """The recording ended: the window goes (the text is typed now)."""
    global _last_read, _shown
    _last_read = 0.0
    _shown = None
    if window is not None:
        window.hide()
