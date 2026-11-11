"""Live text (TuxTalk Labs): the words so far, shown in a small window
(live_text_window.py) while the user is still talking.

About once a second the audio not yet typed is read again with the model
already loaded, on the dictation worker, with its log lines left out
(logger.quietly). With Type at pause that's the phrase still being said;
without it, the dictation so far, its last MAX_AUDIO_S at most. What gets
typed doesn't change: that is still the whole audio, read once the user
stops or pauses.

The typed text must never wait long on a preview. A read goes to the worker
only when it has nothing else to do (submit_if_idle), and does nothing if
the phrase it was handed was cut, or the recording stopped, while it
waited. A read already running can't be stopped, so reads run only with
the model on a graphics card, where a minute of speech takes a fifth of a
second, and a phrase stops being read once a read of it takes longer than
STOP_AFTER_S. Each read holds about a second more audio than the one
before, so none runs much past half a second.

Words said into a password field never show: the check that keeps them
from being typed (focused_window.is_password) runs before and after every
read, and once it has found one, that phrase isn't read again, even after
the cursor leaves the field.
"""
import threading
import time

from . import dictation_text, dictation_worker, engine, focused_window, recording, resampling
from . import live_settings as live
from . import recording_state as rs
from . import type_at_pause, x11_desktop
from .logger import quietly, setup_logger

logger = setup_logger(__name__)

# How often the words are read again, at most.
INTERVAL_S = 1.0
# Less audio than this holds a word or two at most: not worth a read.
SHORTEST_S = 0.5
# A phrase whose read took longer than this isn't read again: the next,
# a second longer, could hold the typed text up past half a second.
STOP_AFTER_S = 0.25
# The newest audio a read looks at, which bounds how long a read takes
# without Type at pause, where the audio grows for the whole dictation.
MAX_AUDIO_S = 60.0

# The window, made on GTK's thread at the service's start (app); None where
# it can't be shown without taking the keyboard (no X display).
window = None
# A read is waiting or running on the worker.
_reading = threading.Event()
_last_read = 0.0
# (recording, phrase number) not to read again, said into a password field
# or too slow to read, and the one whose words the window shows; the phrase
# number is rec.chunks_submitted. Both are let go when the recording ends.
_skip = None
_shown = None
# The processor was found doing the work, and the log said so.
_said_processor = False


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
    if not _on_graphics_card():
        return
    with type_at_pause.chunk_lock:
        frames, phrase = list(rec.frames), rec.chunks_submitted
    if not frames or _same(_skip, rec, phrase):
        return
    _last_read = now
    _reading.set()
    if not dictation_worker.submit_if_idle(lambda: _read(rec, frames, rec.samplerate, phrase)):
        _reading.clear()


def _wanted(rec):
    """Whether live text is on and *rec*, the recording running, is dictation."""
    return (window is not None and live.live_text and rs.state.is_recording
            and rec is not None and not rec.edit_mode)


def _on_graphics_card():
    """Whether the model runs on a graphics card; the first time it doesn't,
    the log says why the box stays closed."""
    global _said_processor
    if engine.on_graphics_card():
        return True
    if not _said_processor:
        _said_processor = True
        logger.info("Live text is on, but it needs the speech model on a graphics card")
    return False


def _same(pair, rec, phrase):
    """Whether *pair* (_skip or _shown) is *phrase* of this very recording."""
    return pair is not None and pair[0] is rec and pair[1] == phrase


def _close_after_a_cut(rec):
    """Type at pause typed the phrase the window shows: its words go."""
    shown = _shown          # once: the worker may change it meanwhile
    if shown is not None and shown[0] is rec and not _same(shown, rec, rec.chunks_submitted):
        _close()


def _current(rec, phrase):
    """Whether *phrase* of *rec* is still the audio being said."""
    return rs.state.current is rec and rs.state.is_recording and rec.chunks_submitted == phrase


def _read(rec, frames, rate, phrase):
    """On the dictation worker: transcribe *frames*, phrase *phrase* of
    *rec*, and show the words, unless it's too late or too private. The
    checks count against STOP_AFTER_S with the transcription: the typed
    text waits for all of it."""
    started = time.monotonic()
    try:
        # The checks log too (a slow accessibility bus), once a second here.
        with quietly():
            words = _hear(rec, frames, rate, phrase)
            fresh = words is not None and _current(rec, phrase) and _may_show(rec, phrase)
        took = time.monotonic() - started
        if took > STOP_AFTER_S:
            _stop_reading(rec, phrase)
            logger.info(f"Live text stops for this phrase: reading it took {took:.2f}s")
        elif fresh:
            _show(rec, phrase, words)
    finally:
        _reading.clear()


def _hear(rec, frames, rate, phrase):
    """What *frames* say ("" for nothing), or None when they mustn't be
    read: the phrase was typed or is private, or there's too little audio."""
    if not _current(rec, phrase) or not _may_show(rec, phrase):
        return None
    audio = _newest_audio(frames, rate)
    if audio is None:
        return None
    return engine.transcribe(audio, getattr(live.settings, "language", None)) or ""


def _may_show(rec, phrase):
    """False, with the window closed, in a password field (and the phrase
    not read again) or where the box would end Cinnamon's show desktop."""
    if focused_window.is_password():
        _stop_reading(rec, phrase)
        return False
    if x11_desktop.popup_ends_show_desktop():
        _close()
        return False
    return True


def _stop_reading(rec, phrase):
    """No more reads of *phrase*, and its words come down."""
    global _skip
    _skip = (rec, phrase)
    _close()


def _newest_audio(frames, rate):
    """The last MAX_AUDIO_S of *frames* (int16 blocks) at the engine's rate,
    or None when it's too short to hold a word. Only the newest blocks are
    converted: the whole recording would cost more with every read."""
    keep = int(MAX_AUDIO_S * rate)
    newest, size = [], 0
    for block in reversed(frames):
        newest.append(block)
        size += len(block) // 2
        if size >= keep:
            break
    audio = recording.frames_f32(newest[::-1])[-keep:]
    if audio.size < SHORTEST_S * rate:
        return None
    if rate != engine.SAMPLE_RATE:
        audio = resampling.resample(audio, rate, engine.SAMPLE_RATE)
    return audio


def _show(rec, phrase, words):
    """Put *words* up, without the fillers when they'd be left out of the
    typed text; nothing heard closes the window, so no old words stay."""
    global _shown
    # The cleanup rules, as the typed text gets them; the AI only runs then.
    words = dictation_text.clean_up(words, ai=False) if words else words
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
    global _last_read, _shown, _skip
    _last_read = 0.0
    _shown = _skip = None
    if window is not None:
        window.hide()
