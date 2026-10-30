"""Type at pause (TuxTalk Labs): while the user is still talking, each
phrase is typed when they pause. The audio callback cuts a phrase at a
pause and the dictation worker transcribes and types it; the mark between
two phrases is held back until the next one arrives, so the engine can
hear both together and judge it (a comma, a period, a question going on);
a phrase that corrects the one before ("no wait, four") is fixed with it
by the AI."""
import threading
from collections.abc import Callable

import numpy as np

from . import (
    beeps,
    chunking,
    delivery,
    dictation_worker,
    engine,
    focused_window,
    keyboard_output,
    last_dictation,
    notices,
    send_countdown,
    virtual_keyboard,
)
from . import live_settings as live
from . import notify as notify_module
from . import recording_state as rs
from .beeps import READY_BEEP
from .chunking import boundary_mark, word_count, words_in_context
from .key_commands import split_key_command
from .logger import setup_logger
from .normalize import safe_to_lowercase_first_word
from .window_classes import app_kind, is_document_window

logger = setup_logger(__name__)

# Chunked dictation (chunking.py). The audio callback adds blocks and cuts
# chunks, and the stop takes the rest; this lock keeps a block or a cut from
# landing in frames the stop has already taken.
chunk_lock = threading.Lock()

# Transcribes and types a phrase: pipeline.transcribe_and_inject, handed over
# by the pipeline as it loads (use_transcriber). The pipeline calls this module
# for what goes between two phrases, so importing it here would make each need
# the other. hotkeys and service_tick, the only modules that start a
# recording, import the pipeline first, so it's set before a phrase is cut.
# A test patching pipeline.transcribe_and_inject doesn't reach the phrases.
transcribe_phrase: Callable[..., None] | None = None


def use_transcriber(step: Callable[..., None]) -> None:
    """Have *step* transcribe and type each phrase cut at a pause."""
    global transcribe_phrase
    transcribe_phrase = step


def cut_chunk(rec, chunker):
    """The speaker paused: hand the audio of recording *rec* so far to the
    dictation worker.

    Runs on the audio thread, so it only swaps a list and queues a job; the
    transcription happens on the worker."""
    with chunk_lock:
        if rec.chunker is not chunker:
            return  # the recording ended while this block was arriving
        frames, rec.frames = rec.frames, []
        chunking.drop_tail_samples(frames, chunker.tail_samples())
        rec.chunks_submitted += 1
        number = rec.chunks_submitted
        pause = chunker.last_pause
        # Hands-free's "stop when quiet" waits from now until the phrase is
        # typed: it can wait its turn behind the last dictation, and an AI
        # rewrite can take seconds, while the user reads rather than talks.
        detector = rec.silence
        if detector is not None:
            detector.hold()
        # Queued before the lock is let go: otherwise the stop could slip in
        # between counting this chunk and queuing it, and queue the last chunk
        # ahead of it. put() on the unbounded queue never blocks.
        dictation_worker.submit(lambda: _transcribe_chunk(
            frames, rec.samplerate, number, pause, rec.message, detector))


def _transcribe_chunk(frames, rec_sr, number, pause, message, detector):
    """Transcribe and type chunk *number*, cut during the recording after a
    *pause* (seconds), with the settings as they are now, into *message*,
    as transcribe_phrase's *message*. *detector* is that recording's hands-free silence
    detector, held since the cut (cut_chunk), or None."""
    # Logged here, on the worker, to keep file writes off the audio thread.
    seconds = sum(len(f) for f in frames) / 2 / rec_sr
    logger.info(f"Chunk {number}: {seconds:.1f}s of audio, cut after a {pause:.2f}s pause")
    cfg = live.chunk_cfg
    try:
        transcribe_phrase(frames, rec_sr, cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                          cfg.auto_space, cfg.auto_period, cfg.injection_mode,
                          chunk_role="middle", message=message)
    finally:
        if detector is not None:
            detector.release()


def arm_chunker(rec):
    """Give recording *rec* a pause detector when chunked dictation is on.
    Never for a voice edit: its instruction needs the whole recording."""
    if live.chunk_pause_seconds > 0 and not rec.edit_mode:
        from .chunking import PauseDetector
        rec.chunker = PauseDetector(live.chunk_pause_seconds, rec.samplerate,
                                    speech_db=live.chunk_speech_db)
    # Started here, so the audio callback never has to start it.
    dictation_worker.worker()


# Silence put between two chunks when the engine hears them together, so the
# pause is still there to hear (the cut kept only 0.3 s of it).
JOINT_GAP_S = 0.5

# Seconds heard on each side of the pause for that: enough words for the
# engine to judge the seam, so a long phrase isn't transcribed a second time
# whole (a 45 s phrase took a second 45 s pass).
CONTEXT_S = 6.0

SENTENCE_ENDS = (".", "?", "!", "…")


def context_samples() -> int:
    return int(CONTEXT_S * engine.SAMPLE_RATE)


def corrects_across_pause(raw: str, chunk_role, message) -> bool:
    """True when this phrase corrects the one typed before the pause ("no
    wait, four" after "meet at three"), so the AI should hear both together.
    Needs AI self-corrections on and a phrase of this recording typed:
    the dictation worker types the recordings in order, so the undo record
    it takes back is that phrase."""
    if chunk_role is None or not (message.has_words and message.complete):
        return False
    if not (live.ai_corrections and live.ai_engine is not None
            and rs.state.last_inserted_text.strip()):
        return False
    if message.held_cue:
        return True
    from . import ai_cleanup
    return ai_cleanup.opens_with_correction(rs.state.last_inserted_text, raw)


def _erase_typed(text: str) -> bool:
    """Backspace over *text*, just typed by the phrase before the pause. False
    when it can't be done safely; then nothing more is erased."""
    focused = focused_window.window_class()
    if keyboard_output.not_where_typed(focused):
        logger.info("Correction across a pause skipped: the phrase is in another window")
        return False
    # The AI that found the correction can take seconds, time enough to
    # click into a password field of the same window.
    if focused_window.is_password():
        logger.info("Correction across a pause skipped: a password field has the focus")
        return False
    try:
        deleted = keyboard_output.output_backend().backspaces(len(text))
    except virtual_keyboard.StoppedPartway:
        logger.error("Correction across a pause stopped partway; typing the phrase as said")
        rs.state.last_inserted_text = ""
        last_dictation.forget_shown()
        notify_module.notify("A correction stopped partway", "Check the text before it.")
        return False
    if deleted:
        last_dictation.trim_shown(text, len(text))
        rs.state.last_inserted_text = ""
    return bool(deleted)


def correct_across_pause(text: str, message):
    """(text to type, text taken back) for a phrase of *message*'s recording
    that corrects the one before the pause.

    The AI hears both phrases as one ("Meet at three. No wait, four."). When
    it fixes the correction, the earlier phrase is backspaced and the fixed
    whole is typed in its place ("Meet at four."); otherwise the phrase is
    typed as said, a cue held back from the phrase before included, and
    nothing is taken back."""
    from . import ai_cleanup
    prev = rs.state.last_inserted_text
    cue, message.held_cue = message.held_cue, ""
    if cue and cue[-1] not in ".?!,;:":
        cue += ","
    if (cue.endswith(",") and text[:1].isupper()
            and safe_to_lowercase_first_word(text, live.words)):
        text = text[0].lower() + text[1:]      # "No wait, four", not "Four"
    said = f"{cue} {text}" if cue else text
    joined = prev + ("" if prev[-1:].isspace() else " ") + said
    fixed = ai_cleanup.fix_self_corrections(joined, live.ai_engine)
    if fixed == joined or not fixed.strip():
        return said, ""
    if message.cancelled:
        # Esc while the AI worked: the phrase before stays as it is.
        logger.info("Correction across a pause skipped: the recording was cancelled")
        return said, ""
    if not _erase_typed(prev):
        return said, ""
    logger.info("AI cleanup fixed a self-correction across a pause")
    return fixed, prev


def _type_boundary(message, pending: dict, tail: str, injection_mode: str) -> None:
    """Type what goes between a held-back chunk and what follows it, as part
    of *message*, the one the chunk is in."""
    if not tail:
        return
    last_dictation.remember_said(tail, True, message)
    # Only into the phrase's window: the context pass before this can take a while.
    ok = delivery.inject_text(tail, injection_mode, into=pending["where"],
                              message=message) == "ok"
    last_dictation.remember_shown(tail, True, ok, message)
    if ok:
        # Undo and "delete last sentence" then see the whole chunk.
        rs.state.last_inserted_text = pending["typed"] + tail


# A phrase this short that the context pass hears as "press enter" is taken
# as "press enter": alone it's barely a second of audio, and Parakeet heard
# "Press center." where both together read "... to say. Press enter."
ENTER_IN_CONTEXT_MAX_WORDS = 3

# A phrase this short (seconds) is typed as the context pass heard it when
# that pass heard fewer words: alone, the engine can make a sentence up
# around a word or two (chunking.words_in_context).
SHORT_CHUNK_S = 2.0


def resolve_boundary(message, audio_f32, language, injection_mode, raw="",
                     auto_period=True) -> str:
    """The next chunk of *message*'s recording has arrived: decide the mark
    held back after the last one, from the engine hearing both chunks
    together, and type it. Returns
    the chunk's text: "press enter" when only the context pass heard that,
    and for a very short chunk the words the context pass heard."""
    pending, message.held_mark = message.held_mark, None
    if focused_window.changed(pending["where"], focused_window.look()):
        # The user switched windows during the pause: that phrase's mark
        # belongs at its end, in the other window, not at the start of this
        # one ("? What's next?"). This window starts afresh.
        logger.info("Chunk boundary: another window has the focus now; "
                    "its mark stays with its phrase")
        rs.state.continue_mid_sentence = False
        return raw
    mark, joint = _heard_in_context(pending, audio_f32, language)
    if (raw and len(raw.split()) <= ENTER_IN_CONTEXT_MAX_WORDS
            and not split_key_command(raw)[1] and split_key_command(joint)[1] == "ENTER"):
        logger.info(f"Heard \"press enter\" in context ({live.loggable(raw)} alone)")
        raw = "press enter"
        # The sentence ends here, as it would after "press enter" said
        # clearly (flush_boundary): its mark, and no space to send.
        mark = mark or ("." if auto_period else "")
        _type_boundary(message, pending, mark, injection_mode)
        rs.state.continue_mid_sentence = False
        return raw
    if joint and len(audio_f32) <= SHORT_CHUNK_S * engine.SAMPLE_RATE:
        raw = _short_phrase_in_context(raw, pending["raw"], joint)
    _type_boundary(message, pending, (mark + " ") if mark else " ", injection_mode)
    # Lowercase on after a comma or no mark; a sentence end starts afresh.
    rs.state.continue_mid_sentence = mark not in SENTENCE_ENDS
    # "do you think ... feature" (pause) "to make it work better": the engine
    # heard a question before the pause and a sentence going on across it,
    # so the question ends where this phrase does.
    message.question_open = pending["mark"] == "?" and mark not in SENTENCE_ENDS
    return raw


def _heard_in_context(pending: dict, audio_f32, language):
    """(the mark between the held-back phrase and this one, what the engine
    heard of both together). The mark is the held-back one when the engine,
    hearing both, judges nothing different or can't be asked."""
    mark, joint = pending["mark"], ""
    try:
        gap = np.zeros(int(JOINT_GAP_S * engine.SAMPLE_RATE), dtype=np.float32)
        joint = engine.transcribe(np.concatenate(
            [pending["audio"], gap, audio_f32[:context_samples()]]), language) or ""
        found = boundary_mark(pending["raw"], joint)
        logger.info(f"Chunk boundary: {pending['mark']!r} alone, {found!r} in context")
        if found is not None:
            mark = found
    except Exception as e:
        logger.warning(f"Couldn't hear the chunk boundary in context: {e}")
    return mark, joint


def _short_phrase_in_context(raw: str, before: str, joint: str) -> str:
    """A very short phrase as the context pass heard it, when that heard
    fewer words of it than the engine did alone; else *raw*. Whether to
    press a key stays resolve_boundary's "press enter" rule's call."""
    heard = words_in_context(before, joint) or ""
    if (0 < word_count(heard) < word_count(raw)
            and split_key_command(heard)[1] == split_key_command(raw)[1]):
        logger.info(f"Short phrase: {live.loggable(raw)} alone, {live.loggable(heard)} "
                    "in context; typing what the context heard")
        return heard
    return raw


def flush_boundary(message, auto_period: bool, auto_space: bool, injection_mode: str) -> None:
    """*message*'s dictation ended in a pause: type the mark held back after
    its last chunk, or the auto-period when the engine wrote none, in its
    window only."""
    pending, message.held_mark = message.held_mark, None
    if pending is None:
        return
    now = focused_window.look()
    if focused_window.changed(pending["where"], now):
        return  # the end of this sentence isn't in the window that has focus
    kind = app_kind(now[0]) if live.adapt_to_app else ""
    mark = pending["mark"] or ("." if auto_period and not kind else "")
    _type_boundary(message, pending, mark + (" " if auto_space and not kind else ""),
                   injection_mode)


def start_afresh(message, ended=True) -> None:
    """Type at pause pressed Enter or Tab mid-recording, or "edit that"
    rewrote what was typed: what's said next in *message*'s recording reaches
    back across nothing (no held-back mark, correction, lowercase start or
    screen copy). After Enter or Tab (*ended*) the message starts over,
    and its screen copy starts in whichever window it's typed into next; a
    rewrite stays in the unsent one."""
    if ended:
        message.start_over()
        message.shown_window = None
    message.held_mark, message.question_open, message.held_cue = None, False, ""
    rs.state.continue_mid_sentence = False
    if message.shown is not None:
        message.shown = ""


def end_in_a_pause(message, beeps_on, auto_space, auto_period, injection_mode) -> None:
    """Type at pause's recording ended in a pause, with nothing said after
    it: a held "no wait" is typed as said, the last phrase gets its mark,
    and "Send each dictation automatically" sends *message* if all of it
    went in."""
    unfinished_edit = _drop_unfinished_edit(message, beeps_on)
    if message.held_cue:
        cue, message.held_cue = message.held_cue, ""
        if auto_period and cue[-1] not in ".?!":
            cue += "."
        delivery.inject_text(cue + (" " if auto_space else ""), injection_mode, message=message)
    cut_off = _cut_off(message)
    sends = _may_send(message)
    if sends and not cut_off:
        flush_boundary(message, auto_period, False, injection_mode)
        delivery.send_automatically(" " if auto_space else "", injection_mode, message)
        start_afresh(message)
    else:
        flush_boundary(message, auto_period and not cut_off, auto_space, injection_mode)
        if sends:
            send_countdown.say_held_back(cut_off, message)
    if not live.chunk_context_marks:
        rs.state.continue_mid_sentence = False   # it belongs to this recording
    if cut_off:
        rs.state.continue_mid_sentence = True    # what's said next carries it on
    if not unfinished_edit:                   # that one beeped its cancel
        beeps.beep(beeps_on, *READY_BEEP)


def _drop_unfinished_edit(message, beeps_on) -> bool:
    """"edit that", a pause, and nothing more: what to change never came,
    and *message* isn't sent as it was. True when that's what happened."""
    if message.edit_waiting is None:
        return False
    message.edit_waiting = None
    notices.edit_problem("edit_that_what", beeps_on)
    message.add("failed", None)
    return True


def _may_send(message) -> bool:
    """Whether an automatic Enter is set up after *message*: the setting on,
    and all of it typed outside a text editor (send_automatically then
    checks the window). Not whether it was cut off ("and")."""
    return (live.press_enter_after and message.has_words and message.complete
            and not is_document_window(focused_window.window_class()))


def _cut_off(message) -> str:
    """The word *message* was cut off on as the key came up ("and"), with
    "Send each dictation automatically" on, or "". Then it's held back with
    no period, as a whole dictation is (key_commands.auto_send)."""
    cut_off = message.ends_on if live.press_enter_after else ""
    if cut_off:
        logger.info(f'Held back the Enter: the dictation ended on "{cut_off}"')
        if message.held_mark is not None:
            message.held_mark["mark"] = ""
    return cut_off
