"""Live text (TuxTalk Labs): the words so far, shown in a small window
(live_text_window.py) while the user is still talking.

About twice a second the dictation so far is read again with the model
already loaded, on the dictation worker, with its log lines left out
(logger.quietly). Past SETTLE_AFTER_S of it, its start is read one last
time, at a gap between words, and those words stay as they are (_settle):
reading it all again would get slower with every read, and reading only
its newest part made the text shift as its start moved. What gets typed
doesn't change: that is still the whole audio, read once the user stops.
It's live text or Type at pause, never both (Preferences' While you talk;
config._migrate_settings): a recording typed at its pauses isn't read.

The typed text must never wait long on a preview. A read goes to the worker
only when it has nothing else to do (submit_if_idle), as it may still be
typing the dictation before, and does nothing if the recording stopped
while it waited. A read already running can't be stopped, so:
  - reads run only with the model on a graphics card, where a few seconds
    of speech take a fifth of a second on a desktop card;
  - each sees at most GROWTH_S more audio than the read before it, so none
    is much slower than the last, and a dictation's first read holds a few
    seconds at most, even after waiting for the worker;
  - a read that takes longer than STOP_AFTER_S, or fails, ends reads of
    that dictation;
  - two reads of SHORT_AUDIO_S or less whose transcription alone took that
    long, with no fast read of about as much audio between them, say the
    chip itself is slow, and end live text while that engine runs: a
    built-in graphics chip can be no faster than the processor, and the
    first read of every dictation would cost that again.
Nothing stops a read the driver itself stalls, but a stalled driver would
hold up the dictation's own transcription just the same.

Words said into a password field don't show: the check that keeps them
from being typed (focused_window.is_password) runs before and after every
read, and once it has found one, that dictation isn't read again, even
after the cursor leaves the field. One case gets past it: a recording
started in a password field while the worker is still typing the one
before gets no read, so no check, and if the cursor is in an ordinary
field by its first read, that audio shows. Typing has the same gap: it
checks the field only when it types, not where the recording started.
"""
import threading
import time

import numpy as np

from . import atspi_helper, dictation_text, dictation_worker, engine, focused_window
from . import recording, resampling
from . import live_settings as live
from . import recording_state as rs
from . import notify as notify_module
from . import screen_message, x11_desktop
from .logger import quietly, setup_logger

logger = setup_logger(__name__)

# How often the words are read again, at most.
INTERVAL_S = 0.4
# Said once a session when the box can't show, over the window being
# dictated into and as a notification: otherwise it looks like live text is
# broken.
ON_PROCESSOR = ("Live text is off: your speech model runs on the processor. "
                "Your text is still typed.")
TOO_SLOW = ("Live text is off for now: your graphics chip is too slow for it. "
            "Your text is still typed.")
NO_X_DISPLAY = ("Live text can't show here: its box needs XWayland, which this desktop "
                "doesn't run. Your text is still typed.")

# Less audio than this holds a word or two at most: not worth a read.
SHORTEST_S = 0.5
# A dictation whose read took longer than this, checks included, isn't
# read again: the next holds more audio, and the typed text could wait on
# it. On an RTX 4070 Super with Parakeet on Vulkan, a bare reread of 30 s
# of speech (about the most one hears, SETTLE_AFTER_S) took 0.09 s, and a
# day of real use with dictations over 100 s had no read over it (every
# read that is gets logged). Typed phrases, timed through the whole of Type
# at pause, averaged 0.19 s for 4 s and never passed 0.5 s for 5 s or less.
STOP_AFTER_S = 0.6
# Reads of no more than SHORT_AUDIO_S of audio whose transcription alone
# took over STOP_AFTER_S, with one engine and no fast read of about as much
# audio between them, before live text stays off while it runs. A longer
# read is slow on any card once a dictation grows long enough: that ends
# reads of it only.
SLOW_READS = 2
SHORT_AUDIO_S = 5.0
# The most audio a read may see beyond what the dictation's read before it saw.
GROWTH_S = SHORT_AUDIO_S
# The audio grows for the whole dictation. Once a read would hear more
# than SETTLE_AFTER_S, the quietest QUIET_S between SETTLE_FROM_S in and
# the last KEEP_LIVE_S splits it: what's before is read once more and kept,
# what's after is read on as usual. So no read hears much more than
# SETTLE_AFTER_S, and words kept never change on screen.
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
# What's known of the recording being read (_Dictation): made by its first
# read, on the worker, and let go with its audio once the recording ends.
# The service's loop reads it too, so each use takes it once into a local.
_dictation = None
# The processor was found doing the work, and the user was told.
_said_processor = False
# There's no window to show (no X display), and the user was told.
_said_no_display = False
# (engine.running(), how many short reads with it were too slow since a
# fast one of about as much audio, the seconds the last slow one heard);
# the engine as (model, device), so Power Saving letting it go doesn't
# reset it.
_slow = (None, 0, 0.0)


class _Dictation:
    """The recording being read and what its reads found: whether it
    mustn't be read again (said into a password field, or too slow or
    failing to read), the seconds of audio the last read heard (each read
    sees at most GROWTH_S more), and, once a long dictation's start has
    been settled, the samples at its rate whose words are kept and those
    words."""

    def __init__(self, rec):
        self.rec = rec
        self.stopped = False
        self.seen = 0.0
        self.kept_at = 0
        self.kept = ""


def tick(now=None):
    """From the service's loop on each pass: start a read when one is due."""
    global _last_read
    now = time.time() if now is None else now
    rec = rs.state.current
    if not _wanted(rec):
        return
    if window is None:
        _say_no_display()
        return
    if _reading.is_set() or now - _last_read < INTERVAL_S:
        return
    if not _on_graphics_card() or _model_too_slow():
        return
    frames = list(rec.frames)
    if not frames or _stopped(rec):
        return
    _last_read = now
    _reading.set()
    if not dictation_worker.submit_if_idle(lambda: _read(rec, frames, rec.samplerate)):
        _reading.clear()


def _wanted(rec):
    """Whether live text is on and *rec*, the recording running, is dictation
    not typed at its pauses. A pause detector is armed only as a recording
    starts: one started with Type at pause on keeps it (rec.chunker) when
    live text is turned on during it, and would cut its audio under a read."""
    return (live.live_text and rs.state.is_recording
            and rec is not None and not rec.edit_mode and rec.chunker is None)


def _say_no_display():
    """The box can't be made without an X display (live_text_window.make):
    say so the first time, or live text would just seem broken."""
    global _said_no_display
    if not _said_no_display:
        _said_no_display = True
        _tell(NO_X_DISPLAY)


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
        _tell(ON_PROCESSOR)
    return False


def _model_too_slow():
    """Whether SLOW_READS short reads with the engine running now were too slow."""
    running, count, _heard = _slow
    return count >= SLOW_READS and running == engine.running()


def _judge_engine(heard):
    """Count a short read (*heard*: audio seconds, transcription seconds)
    whose transcription was slow against the engine running. A fast one
    starts the count again only when it heard at least as much as the last
    slow one: each dictation starts with reads shorter than the one that was
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
        _tell(TOO_SLOW)
    else:
        logger.info(f"Live text: a read took {took:.2f}s, so that dictation isn't read again")


def _tell(text):
    """Float *text* over the window being dictated into, and send it as a
    notification too: a screen reader reads that one, and the notification
    history keeps it. The notification goes on a thread of its own: a
    notification service that hangs would hold up the typing for seconds."""
    screen_message.show_over_active_window(text)
    _in_background(notify_module.notify, "Live text", text)


def _in_background(job, *args):
    """Run *job* on a thread of its own that won't keep TuxTalk from quitting."""
    threading.Thread(target=job, args=args, daemon=True, name="live-text-notice").start()


def _stopped(rec):
    """Whether *rec* mustn't be read again."""
    known = _dictation
    return known is not None and known.rec is rec and known.stopped


def _dictation_of(rec):
    """What's known of *rec*: a fresh _Dictation on its first read."""
    global _dictation
    known = _dictation
    if known is None or known.rec is not rec:
        known = _dictation = _Dictation(rec)
    return known


def _current(rec):
    """Whether *rec* is still the recording running."""
    return rs.state.current is rec and rs.state.is_recording


def _read(rec, frames, rate):
    """On the dictation worker: read *frames* of *rec* and show the words.
    A read that fails ends that dictation's reads, as a slow one does: the
    next would likely fail too, each one holding up the typed text, and the
    words up would stay frozen. The worker logs the error."""
    dictation = _dictation_of(rec)
    try:
        _read_and_show(dictation, frames, rate)
    except Exception:
        _stop_reading(dictation)
        raise
    finally:
        _let_go_if_ended(dictation)
        _reading.clear()


def _read_and_show(dictation, frames, rate):
    """Transcribe *frames* and show the words, unless it's too late or too
    private. The checks count against STOP_AFTER_S with the transcription:
    the typed text waits for all of it."""
    started = time.monotonic()
    # The checks log too (a slow accessibility bus), on every read here.
    with quietly():
        words, heard = _hear(dictation, frames, rate)
        fresh = (words is not None and _current(dictation.rec)
                 and _may_show(dictation, atspi_helper.RECHECK_BUDGET_S))
    took = time.monotonic() - started
    # Judged even when the dictation ended meanwhile: a slow one is a
    # read the typed text waited on.
    _judge_engine(heard)
    if took > STOP_AFTER_S:
        _log_slow_read(took)
        _stop_reading(dictation)
    elif fresh:
        _show(dictation.rec, words)


def _let_go_if_ended(dictation):
    """hide() may have run while the read did, and the read then noted
    what it saw: keep no recording that has ended, nor its audio."""
    global _dictation
    if not _current(dictation.rec) and _dictation is dictation:
        _dictation = None


def _hear(dictation, frames, rate):
    """(what *frames* say, the words kept from its start included, "" for
    nothing; (audio seconds, transcription seconds)), or (None, None) when
    they mustn't be read: the recording ended or is private, or there's too
    little audio."""
    if not _current(dictation.rec) or not _may_show(dictation):
        return None, None
    total = sum(len(block) // 2 for block in frames)
    unsettled = (total - dictation.kept_at) / rate
    allowed = dictation.seen + GROWTH_S
    audio = _newest_audio(frames, rate, min(allowed, unsettled))
    if audio is None:
        return None, None
    seconds = audio.size / engine.SAMPLE_RATE
    dictation.seen = seconds
    language = getattr(live.settings, "language", None)
    started = time.monotonic()
    # Settled only from a read that heard all that isn't kept yet: words
    # skipped between the kept ones would never come back.
    if seconds > SETTLE_AFTER_S and unsettled <= allowed:
        audio = _settle(dictation, audio, total, rate, language)
    words = engine.transcribe(audio, language) or ""
    return _joined(dictation.kept, words), (seconds, time.monotonic() - started)


def _settle(dictation, audio, total, rate, language):
    """Read *audio* (at the engine's rate, the end of *total* samples at
    *rate*) up to its quietest moment and keep those words with the ones
    kept before; return the audio after it."""
    cut = _quietest(audio, SETTLE_FROM_S, audio.size / engine.SAMPLE_RATE - KEEP_LIVE_S)
    kept = _joined(dictation.kept, engine.transcribe(audio[:cut], language) or "")
    rest = audio.size - cut
    dictation.kept_at = total - round(rest * rate / engine.SAMPLE_RATE)
    dictation.kept = kept
    return audio[cut:]


def _quietest(audio, start_s, end_s):
    """The middle of the quietest QUIET_S of *audio* between *start_s* and
    *end_s*, in samples: in a stretch of talking, a gap between words."""
    size = int(QUIET_S * engine.SAMPLE_RATE)
    start = int(start_s * engine.SAMPLE_RATE)
    span = audio[start:int(end_s * engine.SAMPLE_RATE)]
    blocks = span[:span.size // size * size].reshape(-1, size)
    return start + int(np.argmin(np.square(blocks).sum(axis=1))) * size + size // 2


def _joined(kept, words):
    """The words kept, then the ones after them."""
    return " ".join(part for part in (kept, words) if part)


def _may_show(dictation, budget=None):
    """False, with the window closed, in a password field (and the
    dictation not read again) or where the box would end Cinnamon's show
    desktop. *budget* is how long the accessibility bus may take to answer."""
    if focused_window.is_password(budget):
        _stop_reading(dictation)
        return False
    if x11_desktop.popup_ends_show_desktop():
        _close()
        return False
    return True


def _stop_reading(dictation):
    """No more reads of *dictation*, and its words come down. One that has
    ended already was let go (hide), so this keeps nothing of it."""
    dictation.stopped = True
    _close()


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


def _show(rec, words):
    """Put *words* up, without the fillers when they'd be left out of the
    typed text; nothing heard closes the window, so no old words stay."""
    # The cleanup rules, as the typed text gets them; the AI only runs then.
    words = dictation_text.clean_up(words, ai=False) if words else words
    if not words:
        _close()
        return
    window.show(words, rec)


def _close():
    """Take the window down; the next read opens it again."""
    window.hide()


def hide():
    """The recording ended: the window goes (the text is typed now)."""
    global _last_read, _dictation
    _last_read = 0.0
    _dictation = None
    if window is not None:
        window.hide()
