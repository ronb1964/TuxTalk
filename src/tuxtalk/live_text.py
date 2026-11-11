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
second on a desktop card. A read that takes longer than STOP_AFTER_S ends
reads of that phrase. A read of a few seconds of audio (SHORT_AUDIO_S) that
alone took that long says the chip itself is slow, and the second ends
live text while that engine runs: a built-in graphics chip can be no faster
than the processor, and the first read of every phrase would cost that
again. Nothing stops a read the driver itself stalls, but a stalled driver
would hold up the dictation's own transcription just the same.

Words said into a password field never show: the check that keeps them
from being typed (focused_window.is_password) runs before and after every
read, and once it has found one, that phrase isn't read again, even after
the cursor leaves the field.
"""
import threading
import time

from . import atspi_helper, dictation_text, dictation_worker, engine, focused_window
from . import recording, resampling
from . import live_settings as live
from . import recording_state as rs
from . import type_at_pause, x11_desktop
from .logger import quietly, setup_logger

logger = setup_logger(__name__)

# How often the words are read again, at most.
INTERVAL_S = 1.0
# Less audio than this holds a word or two at most: not worth a read.
SHORTEST_S = 0.5
# A phrase whose read took longer than this isn't read again: the next
# holds about a second more audio, and the typed text could wait on it.
STOP_AFTER_S = 0.25
# Slow reads of no more than SHORT_AUDIO_S of audio, with one engine, before
# live text stays off while it runs. A longer read is slow on any card once
# a dictation without Type at pause grows long enough: that ends only it.
SLOW_READS = 2
SHORT_AUDIO_S = 5.0
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
# (engine.running(), how many short reads with it were too slow); the engine
# as (model, device), so Power Saving letting the model go doesn't reset it.
_slow = (None, 0)


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
    if not _on_graphics_card() or _model_too_slow():
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


def _model_too_slow():
    """Whether SLOW_READS short reads with the engine running now were too slow."""
    running, count = _slow
    return count >= SLOW_READS and running == engine.running()


def _note_slow_read(took, heard):
    """Log a read over STOP_AFTER_S (*took*, checks included), and count it
    against the engine when *heard* (audio seconds, transcription seconds)
    shows the transcription alone was slow on a few seconds of audio."""
    global _slow
    running, count = _slow
    if heard is not None and heard[0] <= SHORT_AUDIO_S and heard[1] > STOP_AFTER_S:
        if running != engine.running():
            running, count = engine.running(), 0
        count += 1
        _slow = (running, count)
    if count >= SLOW_READS and running == engine.running():
        logger.info(f"Live text stays off while this engine runs: a read took {took:.2f}s, "
                    "too long to show the words without holding up the typing")
    else:
        logger.info(f"Live text: a read took {took:.2f}s, so that phrase isn't read again")


def _same(pair, rec, phrase):
    """Whether *pair* (_skip or _shown) is *phrase* of this very recording."""
    return pair is not None and pair[0] is rec and pair[1] == phrase


def _close_after_a_cut(rec):
    """Type at pause typed the phrase the window shows: its words go."""
    shown = _shown          # once: the worker may change it meanwhile
    if shown is not None and shown[0] is rec and shown[1] != rec.chunks_submitted:
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
            words, heard = _hear(rec, frames, rate, phrase)
            fresh = (words is not None and _current(rec, phrase)
                     and _may_show(rec, phrase, atspi_helper.RECHECK_BUDGET_S))
        took = time.monotonic() - started
        if took > STOP_AFTER_S:
            # Noted even when the phrase ended meanwhile: those are the
            # reads the typed text waited on.
            _note_slow_read(took, heard)
            _stop_reading(rec, phrase)
        elif fresh:
            _show(rec, phrase, words)
    finally:
        _reading.clear()


def _hear(rec, frames, rate, phrase):
    """(what *frames* say, "" for nothing; (audio seconds, transcription
    seconds)), or (None, None) when they mustn't be read: the phrase was
    typed or is private, or there's too little audio."""
    if not _current(rec, phrase) or not _may_show(rec, phrase):
        return None, None
    audio = _newest_audio(frames, rate)
    if audio is None:
        return None, None
    started = time.monotonic()
    words = engine.transcribe(audio, getattr(live.settings, "language", None)) or ""
    return words, (audio.size / engine.SAMPLE_RATE, time.monotonic() - started)


def _may_show(rec, phrase, budget=None):
    """False, with the window closed, in a password field (and the phrase
    not read again) or where the box would end Cinnamon's show desktop.
    *budget* is how long the accessibility bus may take to answer."""
    if focused_window.is_password(budget):
        _stop_reading(rec, phrase)
        return False
    if x11_desktop.popup_ends_show_desktop():
        _close()
        return False
    return True


def _stop_reading(rec, phrase):
    """No more reads of *phrase*, and its words come down. A read finishing
    after the recording ended keeps nothing: hide() let it go already."""
    global _skip
    if _current(rec, phrase):
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
