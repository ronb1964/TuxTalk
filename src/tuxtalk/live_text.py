"""Live text (TuxTalk Labs): the words so far, shown in a small window
(live_text_window.py) while the user is still talking.

About twice a second the audio not yet typed is read again with the model
already loaded, on the dictation worker, with its log lines left out
(logger.quietly). With Type at pause that's the phrase still being said;
without it, the dictation so far. Past SETTLE_AFTER_S of it, its start is
read one last time, at a gap between words, and those words stay as they
are (_settle): reading it all again would get slower with every read, and
reading only its newest part made the text shift as its start moved. What
gets typed doesn't change: that is still the whole audio, read once the
user stops or pauses.

The typed text must never wait long on a preview. A read goes to the worker
only when it has nothing else to do (submit_if_idle), and does nothing if
the phrase it was handed was cut, or the recording stopped, while it
waited. A read already running can't be stopped, so:
  - reads run only with the model on a graphics card, where a few seconds
    of speech take a fifth of a second on a desktop card;
  - each sees at most GROWTH_S more audio than the read of the phrase
    before it, so none is much slower than the last, and a phrase's first
    read holds a few seconds at most, even after waiting for the worker;
  - a read that takes longer than STOP_AFTER_S ends reads of that phrase;
  - two reads of SHORT_AUDIO_S or less whose transcription alone took that
    long, with no fast read of about as much audio between them, say the
    chip itself is slow, and end live text while that engine runs: a
    built-in graphics chip can be no faster than the processor, and the
    first read of every phrase would cost that again.
Nothing stops a read the driver itself stalls, but a stalled driver would
hold up the dictation's own transcription just the same.

Words said into a password field never show: the check that keeps them
from being typed (focused_window.is_password) runs before and after every
read, and once it has found one, that phrase isn't read again, even after
the cursor leaves the field.
"""
import threading
import time

import numpy as np

from . import atspi_helper, dictation_text, dictation_worker, engine, focused_window
from . import recording, resampling
from . import live_settings as live
from . import recording_state as rs
from . import screen_message, type_at_pause, x11_desktop
from .logger import quietly, setup_logger

logger = setup_logger(__name__)

# How often the words are read again, at most.
INTERVAL_S = 0.4
# Said once a session over the window being dictated into, when the box
# can't show: otherwise it looks like live text is broken.
ON_PROCESSOR = ("Live text is off: your speech model runs on the processor. "
                "Your text is still typed.")
TOO_SLOW = ("Live text is off for now: your graphics chip is too slow for it. "
            "Your text is still typed.")

# Less audio than this holds a word or two at most: not worth a read.
SHORTEST_S = 0.5
# A phrase whose read took longer than this, checks included, isn't read
# again: the next holds about a second more audio, and the typed text could
# wait on it. Parakeet on Vulkan, on an RTX 4070 Super, took 0.19 s on
# average for 4 s of audio and never over 0.5 s for 5 s or less (501 typed
# phrases), so a desktop card stays well under it.
STOP_AFTER_S = 0.6
# Reads of no more than SHORT_AUDIO_S of audio whose transcription alone
# took over STOP_AFTER_S, with one engine and no fast read of about as much
# audio between them, before live text stays off while it runs. A longer
# read is slow on any card once a dictation without Type at pause grows
# long enough: that ends reads of it only.
SLOW_READS = 2
SHORT_AUDIO_S = 5.0
# The most audio a read may see beyond what the phrase's read before it saw.
GROWTH_S = SHORT_AUDIO_S
# Without Type at pause the audio grows for the whole dictation. Once a read
# would hear more than SETTLE_AFTER_S, the quietest QUIET_S between
# SETTLE_FROM_S in and the last KEEP_LIVE_S splits it: what's before is read
# once more and kept, what's after is read on as usual. So no read hears
# much more than SETTLE_AFTER_S, and words kept never change on screen.
SETTLE_AFTER_S = 30.0
SETTLE_FROM_S = 10.0
KEEP_LIVE_S = 5.0
QUIET_S = 0.25

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
# (recording, phrase number, seconds of audio) of the last read: each read
# sees at most GROWTH_S more of the phrase.
_seen = None
# (recording, phrase number, samples at the recording's rate whose words
# are kept, those words), once a long phrase's start has been settled.
_settled = None
# The processor was found doing the work, and the user was told.
_said_processor = False
# (engine.running(), how many short reads with it were too slow since a
# fast one of about as much audio, the seconds the last slow one heard); the engine as (model,
# device), so Power Saving letting it go doesn't reset it.
_slow = (None, 0, 0.0)


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
    """Whether the model runs on a graphics card; the first time it runs on
    the processor, the user is told why the box stays closed. Not while
    it's loading or resting (Power Saving): that's no reason to say so."""
    global _said_processor
    if engine.on_graphics_card():
        return True
    running = engine.running()
    if not _said_processor and engine.model is not None and running and running[1] == "cpu":
        _said_processor = True
        logger.info("Live text is on, but it needs the speech model on a graphics card")
        screen_message.show_over_active_window(ON_PROCESSOR)
    return False


def _model_too_slow():
    """Whether SLOW_READS short reads with the engine running now were too slow."""
    running, count, _heard = _slow
    return count >= SLOW_READS and running == engine.running()


def _judge_engine(heard):
    """Count a short read (*heard*: audio seconds, transcription seconds)
    whose transcription was slow against the engine running. A fast one
    starts the count again only when it heard at least as much as the last
    slow one: each phrase starts with reads shorter than the one that was
    slow, and those say nothing about it. None (nothing was read) changes
    nothing."""
    global _slow
    if heard is None or heard[0] > SHORT_AUDIO_S:
        return
    running, count, slow_audio = _slow
    if running != engine.running():
        count, slow_audio = 0, 0.0
    if heard[1] > STOP_AFTER_S:
        _slow = (engine.running(), count + 1, heard[0])
    # "As much" stops one read's step short of the limit: a hiccup on a 5 s read
    # (a first read held back) would otherwise linger all session, waiting
    # for another read of exactly 5 s.
    elif heard[0] >= min(slow_audio, SHORT_AUDIO_S - INTERVAL_S):
        _slow = (engine.running(), 0, 0.0)


def _log_slow_read(took):
    """Say why the box went away after a read that took *took* seconds."""
    if _model_too_slow():
        logger.info(f"Live text stays off while this engine runs: a read took {took:.2f}s, "
                    "too long to show the words without holding up the typing")
        screen_message.show_over_active_window(TOO_SLOW)
    else:
        logger.info(f"Live text: a read took {took:.2f}s, so that phrase isn't read again")


def _same(pair, rec, phrase):
    """Whether *pair* (_skip, _shown, _seen or _settled) is *phrase* of this
    very recording."""
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
        # The checks log too (a slow accessibility bus), on every read here.
        with quietly():
            words, heard = _hear(rec, frames, rate, phrase)
            fresh = (words is not None and _current(rec, phrase)
                     and _may_show(rec, phrase, atspi_helper.RECHECK_BUDGET_S))
        took = time.monotonic() - started
        # Judged even when the phrase ended meanwhile: a slow one is a read
        # the typed text waited on.
        _judge_engine(heard)
        if took > STOP_AFTER_S:
            _log_slow_read(took)
            _stop_reading(rec, phrase)
        elif fresh:
            _show(rec, phrase, words)
    finally:
        _let_go_if_ended(rec, phrase)
        _reading.clear()


def _let_go_if_ended(rec, phrase):
    """hide() may have run while the read did, and the read then noted
    what it saw or showed: keep no recording that has ended, nor its audio.
    Only an ended recording: after a cut, _close_after_a_cut needs _shown."""
    global _seen, _shown, _settled
    if rs.state.current is rec and rs.state.is_recording:
        return
    seen, shown, settled = _seen, _shown, _settled
    if seen is not None and _same(seen, rec, phrase):
        _seen = None
    if shown is not None and _same(shown, rec, phrase):
        _shown = None
    if settled is not None and _same(settled, rec, phrase):
        _settled = None


def _hear(rec, frames, rate, phrase):
    """(what *frames* say, the words kept from its start included, "" for
    nothing; (audio seconds, transcription seconds)), or (None, None) when
    they mustn't be read: the phrase was typed or is private, or there's too
    little audio."""
    if not _current(rec, phrase) or not _may_show(rec, phrase):
        return None, None
    total = sum(len(block) // 2 for block in frames)
    kept_at, kept = _kept(rec, phrase)
    unsettled = (total - kept_at) / rate
    allowed = _allowed(rec, phrase)
    audio = _newest_audio(frames, rate, min(allowed, unsettled))
    if audio is None:
        return None, None
    seconds = audio.size / engine.SAMPLE_RATE
    _remember_seen(rec, phrase, seconds)
    language = getattr(live.settings, "language", None)
    started = time.monotonic()
    # Settled only from a read that heard all that isn't kept yet: words
    # skipped between the kept ones would never come back.
    if seconds > SETTLE_AFTER_S and unsettled <= allowed:
        audio, kept = _settle(rec, phrase, audio, total, rate, language)
    words = engine.transcribe(audio, language) or ""
    return _joined(kept, words), (seconds, time.monotonic() - started)


def _kept(rec, phrase):
    """(samples at the recording's rate whose words are kept, those words)
    for *phrase* of *rec*; (0, "") before any are."""
    settled = _settled
    if settled is not None and _same(settled, rec, phrase):
        return settled[2], settled[3]
    return 0, ""


def _settle(rec, phrase, audio, total, rate, language):
    """Read *audio* (at the engine's rate, the end of *total* samples at
    *rate*) up to its quietest moment and keep those words; return (the
    audio after it, all the words kept)."""
    global _settled
    cut = _quietest(audio, SETTLE_FROM_S, audio.size / engine.SAMPLE_RATE - KEEP_LIVE_S)
    _kept_at, kept = _kept(rec, phrase)
    kept = _joined(kept, engine.transcribe(audio[:cut], language) or "")
    rest = audio.size - cut
    _settled = (rec, phrase, total - round(rest * rate / engine.SAMPLE_RATE), kept)
    return audio[cut:], kept


def _quietest(audio, start_s, end_s):
    """The middle of the quietest QUIET_S of *audio* between *start_s* and
    *end_s*, in samples: in a stretch of talking, a gap between words."""
    window = int(QUIET_S * engine.SAMPLE_RATE)
    start = int(start_s * engine.SAMPLE_RATE)
    span = audio[start:int(end_s * engine.SAMPLE_RATE)]
    blocks = span[:span.size // window * window].reshape(-1, window)
    return start + int(np.argmin(np.square(blocks).sum(axis=1))) * window + window // 2


def _joined(kept, words):
    """The words kept, then the ones after them."""
    return " ".join(part for part in (kept, words) if part)


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


def _allowed(rec, phrase):
    """Seconds of audio the next read of *phrase* may see."""
    seen = _seen
    before = seen[2] if seen is not None and _same(seen, rec, phrase) else 0.0
    return before + GROWTH_S


def _remember_seen(rec, phrase, seconds):
    """Note how much of *phrase* the read now starting sees."""
    global _seen
    _seen = (rec, phrase, seconds)


def _newest_audio(frames, rate, seconds):
    """The last *seconds* of *frames* (int16 blocks) at the engine's rate,
    or None when it's too short to hold a word. Only the newest blocks are
    converted: the whole recording would cost more with every read."""
    keep = int(seconds * rate)
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
    global _last_read, _shown, _skip, _seen, _settled
    _last_read = 0.0
    _shown = _skip = _seen = _settled = None
    if window is not None:
        window.hide()
