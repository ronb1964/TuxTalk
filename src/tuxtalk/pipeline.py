"""The dictation pipeline: from a recording's audio to text in the focused
window. Transcribe, drop what isn't speech, run what was said as a command
("undo that", "press enter", "edit that") or shape it into text, type it,
then press the key said after it or send it automatically. Type at pause
runs each phrase through here too (type_at_pause.py)."""
import re
import time
from typing import NamedTuple

from . import (
    beeps,
    delivery,
    delivery_notices,
    dictation_text,
    dictation_worker,
    engine,
    focused_window,
    keyboard_output,
    last_dictation,
    notices,
    recording,
    send_countdown,
    silence,
    type_at_pause,
    voice_commands,
    voice_edit,
)
from . import live_settings as live
from . import notify as notify_module
from . import recording_state as rs
from .beeps import CANCEL_BEEP, READY_BEEP
from .chunking import carry_question, hold_back_boundary
from .key_commands import auto_send, ends_unfinished, split_key_command
from .logger import setup_logger
from .window_classes import ELECTRON_PASTE_BROKEN_CLASSES, app_kind, is_document_window

logger = setup_logger(__name__)


def _shortened(text, limit):
    """*text* cut to *limit* characters for a notification, with … if cut."""
    return f"{text[:limit]}{'…' if len(text) > limit else ''}"


def press_said(key: str, chunk_role, after_words: bool = False, message=None) -> None:
    """Press the key said at the end of a dictation ("press enter"). In Type
    at pause that ends the message, and what's said next starts afresh; not
    pressed (a key stuck down), the message is still in the box, to carry
    on in and for "edit that" to reach whole. In Claude Desktop "press tab"
    types four spaces, as a tab said mid-sentence does there: its Tab key
    jumps to the Send button, and the next space typed would press it; after
    words, they go where the Tab would. A password field there gets the
    real Tab: spaces would be refused.

    *after_words*: words of *message* (the recording's) were delivered
    first, or tried, and the key goes only where they went, and only if all
    of them did (send_countdown.press_where_sent), as an automatic Enter
    does. Said alone it goes wherever the focus is: people type a password
    by hand, then say "press enter" to log in."""
    why = f'said "press {key.lower()}"'
    if (key == "TAB" and focused_window.window_class() in ELECTRON_PASTE_BROKEN_CLASSES
            and not focused_window.is_password()):
        outcome = (send_countdown.type_where_sent("    ", key, message, type_onto_dictation)
                   if after_words else type_onto_dictation("    ", "type"))
        if outcome not in (None, "ok"):
            logger.warning(f"Tab not typed as four spaces ({outcome})")
            if outcome == "key_held":
                notify_module.notify(*delivery_notices.key_not_pressed(key))
        return
    if after_words:
        pressed = send_countdown.press_where_sent(key, why, message, press_key)
    else:
        pressed = press_key(key, why)
    if pressed and chunk_role is not None:
        type_at_pause.start_afresh(message)


def _after_the_words(outcome: str, said: str, press: str, space: str, chunk_role,
                     beeps_on: bool, injection_mode: str, message) -> None:
    """What follows a dictation's words, by delivery.inject_text's *outcome*: a
    notice when they didn't all go in; else the key *said* after them, or
    the automatic send's Enter when *press*. *space* is the auto-space left
    off before the key."""
    if outcome in delivery_notices.NOTICES:
        notices.report_undelivered(outcome, beeps_on)
    elif said:
        press_said(said, chunk_role, after_words=True, message=message)
    elif press:
        send_automatically(space, injection_mode, message)
        if chunk_role is not None:
            type_at_pause.start_afresh(message)


def press_key(key: str, why: str, claim=None) -> bool:
    """keyboard_output.press_key, and the dictation behind the key forgotten
    with it, in one step on the dictation worker. An automatic send's Enter
    comes from a timer thread: pressed there, it could go down between a
    delivery's checks and its keys, and the forgetting take a dictation
    typed after it. From another thread it waits its turn, after what the
    worker is typing; a tap meanwhile still holds the send (*claim*)."""
    def press_and_forget():
        pressed = keyboard_output.press_key(key, why, claim=claim)
        if pressed:
            last_dictation.forget_after_key(key, why)
        return pressed
    return dictation_worker.call(press_and_forget)


def send_automatically(space: str, injection_mode: str, message) -> None:
    """"Send each dictation automatically": Enter where *message* went
    (send_countdown). Held when a newer recording has begun since, as a
    press of the hold key during the countdown holds it: what's said there
    is added to this message and sent at its end. A newer one that was
    only a tap (or heard nothing) is over already: that's said, and the
    space left off goes in now, its key long up."""
    if send_countdown.send_automatically("Send each dictation automatically is on", message,
                                         press_key, type_onto_dictation, space,
                                         injection_mode):
        if rs.state.message.cancelled and not rs.state.is_recording:
            send_countdown.say_held()
            send_countdown.type_owed_space()


# delivery.inject_text's outcomes that are sure nothing was typed: it gave up before
# the first key (delivery._delivery_stopped). "failed" may follow some of it (wtype
# timing out), and "password_field" earlier lines.
_GAVE_UP_UNTYPED = frozenset({"moved", "key_held"})


def type_onto_dictation(text: str, injection_mode: str, into=None) -> str:
    """Type *text* right after the dictation, keeping undo's record of it
    whole; delivery.inject_text's outcome, and *into* is its. When none of
    it went in (a space refused because the focus moved), the dictation is
    in the app as it was, and so is everything that remembers it."""
    kept = (rs.state.last_inserted_text, rs.state.continue_mid_sentence, rs.state.nav_context,
            rs.state.shown_text, rs.state.last_inserted_window, rs.state.last_inserted_where)
    outcome = delivery.inject_text(text, injection_mode, into=into)
    if outcome == "ok":
        rs.state.last_inserted_text = kept[0] + text
    elif outcome in _GAVE_UP_UNTYPED:
        # The window too: typing takes it for the dictation's before the first key.
        (rs.state.last_inserted_text, rs.state.continue_mid_sentence, rs.state.nav_context,
         rs.state.shown_text, rs.state.last_inserted_window, rs.state.last_inserted_where) = kept
    return outcome


class _Job(NamedTuple):
    """One dictation's (or Type at pause phrase's) settings, as they were
    when its work was queued."""
    beeps_on: bool
    smart_quotes: bool
    notify_on: bool
    language: str | None
    auto_space: bool
    auto_period: bool
    injection_mode: str
    chunk_role: str | None
    message: send_countdown.Message


class _Words(NamedTuple):
    """A dictation's words shaped for typing, and what goes with them."""
    text: str
    raw: str                # as heard, with the key said after it taken off
    said: str               # the key said after the words ("ENTER", "TAB" or "")
    press: str              # the key pressed after them: *said*, or the automatic Enter
    space_left_off: str     # the auto-space left off before that key
    taken_back: str         # the phrase backspaced for a correction across a pause
    held_mark: str | None   # the mark held back for the next phrase to decide


def transcribe_and_inject(frames, rec_sr, beeps_on, smart_quotes, notify_on,
                          language=None, auto_space=True, auto_period=True,
                          injection_mode="type", chunk_role=None, message=None):
    """The slow half of stopping, on the dictation worker: transcribe the
    captured audio and type the text. It touches no GTK (the indicator was
    already hidden in recording.finish_capture), so it can run off the
    hotkey loop and the GTK loop both.

    *chunk_role* is set for chunked dictation (chunking.py): "middle" for a
    chunk cut at a pause while recording goes on (no beep, no notice, silence
    dropped quietly), "last" for the rest after the stop (the usual READY beep,
    even when it holds no speech, since earlier chunks were typed).

    *message* is the recording's (send_countdown.Message), taken when its
    work was queued, so a recording started meanwhile can't change where
    this one's Enter may go. By default the current recording's."""
    job = _Job(beeps_on, smart_quotes, notify_on, language, auto_space, auto_period,
               injection_mode, chunk_role, rs.state.message if message is None else message)
    print("Recording stopped. Transcribing…")
    t0 = time.time()
    logger.info("Recording stopped, starting transcription")
    try:
        # Once before the engine hears it, once after: a phrase can be
        # queued or transcribed when Esc cancels its recording.
        audio_f32 = None if _cancelled(job) else _audio_to_hear(frames, rec_sr, job)
        raw = None if audio_f32 is None else _hear(audio_f32, job, t0)
        if raw is None or _cancelled(job):
            return
        raw, in_password = _before_the_words(raw, audio_f32, job)
        if raw is None or _ran_as_command(raw, job):
            return
        raw, said = split_key_command(raw)
        press, cut_off = _key_to_press(raw, said, job)
        if said and not raw.strip():
            _press_alone(said, in_password, job)
            return
        words = _shape_text(raw, said, press, cut_off, job)
        # Again before the beep and the "Transcribed" notification, which
        # shows the words: the focus may have moved while the AI cleaned them.
        if words is None or (words.text and notices.refuse_password_field(beeps_on)):
            return
        _tell_done(words.text, job)
        if words.text:
            _deliver(words, audio_f32, job)
    except Exception as e:
        logger.error(f"Transcription error: {e}", exc_info=True)
        # The cancel beep and a notice whatever the notifications setting,
        # like notices.report_undelivered: nothing was typed, and the "done"
        # beep would sound like it worked.
        beeps.beep(beeps_on, *CANCEL_BEEP)
        notify_module.notify("Transcription failed", _shortened(str(e), 120))


def _cancelled(job: _Job) -> bool:
    """Whether the recording *job* is part of was cancelled."""
    if job.message.cancelled:
        logger.info("Not typed: the recording was cancelled")
    return job.message.cancelled


def _nothing_in_chunk(what: str, job: _Job) -> bool:
    """For a chunk, handle an empty or silent one; False for a whole dictation."""
    if job.chunk_role is None:
        return False
    logger.info(f"Chunk ({job.chunk_role}) {what}; nothing typed for it")
    if job.chunk_role == "last":
        type_at_pause.end_in_a_pause(job.message, job.beeps_on, job.auto_space, job.auto_period,
                                     job.injection_mode)
    return True


def _audio_to_hear(frames, rec_sr, job: _Job):
    """The recording as float audio at the engine's rate; None, the user
    told why, when there is nothing in it to transcribe."""
    audio_f32 = recording.frames_f32(frames)
    if audio_f32.size == 0:
        if not _nothing_in_chunk("had no audio", job):
            print("(No audio captured)")
        return None
    if rec_sr != engine.SAMPLE_RATE:
        audio_f32 = recording.resample(audio_f32, rec_sr, engine.SAMPLE_RATE)

    # A second or more of pure digital silence: the microphone is muted,
    # unplugged or dead, not a quiet room. Say so instead of typing nothing.
    if silence.is_dead_mic(audio_f32, engine.SAMPLE_RATE):
        beeps.beep(job.beeps_on, *CANCEL_BEEP)
        notices.warn_silent_mic()
        return None

    # A quick tap of the key with nothing said: speech engines can invent
    # a word ("Thank you.") in a fraction of a second of room noise.
    # Both stop with the cancel beep, as a too-short F8 tap and the
    # hands-free "nothing said" stop do: silence there read as broken.
    if silence.is_speechless_tap(audio_f32, engine.SAMPLE_RATE):
        if not _nothing_in_chunk("had no speech", job):
            print("(Short tap with no speech; nothing typed)")
            logger.info(f"Skipped {len(audio_f32) / engine.SAMPLE_RATE:.2f}s with no speech in it")
            beeps.beep(job.beeps_on, *CANCEL_BEEP)
        return None
    return audio_f32


def _hear(audio_f32, job: _Job, t0: float):
    """What the engine heard; None, with the cancel beep for a whole
    dictation, when it heard nothing."""
    raw = engine.transcribe(audio_f32, job.language)
    logger.info(f"TIMING: Transcription pipeline took {time.time() - t0:.2f}s")
    if job.chunk_role is not None:
        logger.info(f"Chunk ({job.chunk_role}): {len(audio_f32) / engine.SAMPLE_RATE:.1f}s, "
                    f"raw: {live.loggable(raw)}")
    if raw:
        return raw
    if not _nothing_in_chunk("was not recognised", job):
        print("(No speech recognized)")
        logger.info("Nothing recognized, so nothing was typed")
        beeps.beep(job.beeps_on, *CANCEL_BEEP)
    return None


def _before_the_words(raw: str, audio_f32, job: _Job):
    """(raw, in a password field) once what goes before the words is dealt
    with; raw is None when a password field refuses them."""
    rest, said = split_key_command(raw)
    key_only = not rest.strip() and said
    # A password field gets nothing: not the words, nor what Type at
    # pause would type first (the mark held back from the last phrase, a
    # held "No wait.", backspaces for a correction), and the local AI
    # never sees them. Only "press enter" or "press tab" on its own still
    # presses the key, there and not where the phrases before went:
    # people type the password by hand, then say it to log in, or move
    # between the boxes.
    in_password = focused_window.is_password()
    if in_password:
        job.message.held_mark, job.message.held_cue = None, ""
        if not key_only:
            notices.password_field_refused(job.beeps_on)
            return None, True

    # Chunked dictation: the chunk before this one held back its final
    # mark; decide it now, before a command acts on what was typed.
    # Only "press tab" after the pause leaves the box without one.
    if job.chunk_role is not None and job.message.held_mark is not None:
        if key_only == "ENTER":
            # Only "press enter" after the pause: the sentence before it
            # ends here (its held-back mark, or the auto-period), with no
            # space to go out in the sent message.
            type_at_pause.flush_boundary(job.message, job.auto_period, False, job.injection_mode)
        elif not key_only:
            raw = type_at_pause.resolve_boundary(job.message, audio_f32, job.language,
                                                 job.injection_mode, raw, job.auto_period)
    return raw, in_password


def _ran_as_command(raw: str, job: _Job) -> bool:
    """Whether the whole dictation was a command, and was run instead of typed."""
    return (voice_commands.handle_undo(raw, job.beeps_on, job.notify_on)
            # Select and move by voice ("select the last sentence"), TuxTalk Labs.
            or voice_commands.handle_navigation(raw, job.beeps_on)
            # "fix that word": Fix a Word on the dictation before this one.
            or voice_commands.handle_fix_word(raw, job.beeps_on)
            # "edit that, make it shorter": voice editing without its key, on
            # what was just typed (TuxTalk Labs, with voice editing on).
            or voice_edit.handle_spoken_edit(raw, job.chunk_role, job.beeps_on,
                                             job.injection_mode, job.message))


def _key_to_press(raw: str, said: str, job: _Job):
    """(key to press after the words, the "and" the automatic send was held
    back by). "... press enter" types the words, then a real Enter that
    sends the message or runs the command; "... press tab" moves on to the
    next box (key_commands.py). Or Enter every time, when "Send each
    dictation automatically" is on, but only at the end of the recording,
    not each phrase of Type at pause."""
    if said or not live.press_enter_after or job.chunk_role == "middle":
        return said, ""
    sending, cut_off = auto_send(raw, is_document_window(focused_window.window_class()))
    return ("ENTER" if sending else ""), cut_off


def _shape_text(raw: str, said: str, press: str, cut_off: str, job: _Job):
    """The words as they'll be typed (_Words); None when a phrase is only a
    correction cue, held for the next one."""
    # Normalize text (voice commands, punctuation, spacing), plainer for a
    # terminal or code editor ("Plain text in terminals and code editors").
    kind = app_kind(focused_window.window_class()) if live.adapt_to_app else ""
    # A pause is not a sentence end, so a middle chunk gets no auto-period
    # of its own; the last chunk (or type_at_pause.flush_boundary) ends the sentence.
    # "press enter" at the end of one does end it: the message is sent.
    # Cut off on "and": the sentence isn't over either, so no period, and
    # what's said next carries it on in lowercase. A form box left with
    # "press tab" gets none: "John", not "John.".
    chunk_period = (job.auto_period and (job.chunk_role != "middle" or said == "ENTER")
                    and not cut_off and said != "TAB")
    # A correction said after a pause ("meet at three" ... "no wait,
    # four") goes to the AI together with the phrase it corrects.
    across = type_at_pause.corrects_across_pause(raw, job.chunk_role, job.message)
    text = dictation_text.prepare_text(raw, job.smart_quotes, chunk_period, job.auto_space,
                                       app_kind=kind, ai=not across)
    if cut_off:
        # The engine may have put its own period after the "and".
        text = re.sub(r"\.(\s*)$", r"\1", text)
        rs.state.continue_mid_sentence = True
    taken_back = ""
    if across:
        corrected = _correction_or_held_cue(text, job)
        if corrected is None:
            return None
        text, taken_back = corrected
    if job.message.question_open and job.chunk_role is not None:
        job.message.question_open = False
        text = carry_question(text)
    held_mark = None
    if job.chunk_role == "middle" and live.chunk_context_marks and not said:
        text, held_mark = hold_back_boundary(text, raw)
    space_left_off = ""
    if press:
        # No auto-space before Enter or Tab: it would end up in the sent
        # message, or at the end of the box.
        # Kept for a send that's held, which types it after all.
        core = text.rstrip(" ")
        space_left_off, text = text[len(core):], core
    return _Words(text, raw, said, press, space_left_off, taken_back, held_mark)


def _press_alone(said: str, in_password: bool, job: _Job) -> None:
    """Only "press enter" or "press tab" was said: press it."""
    if job.chunk_role != "middle":
        beeps.beep(job.beeps_on, *READY_BEEP)
    press_said(said, job.chunk_role, after_words=job.message.has_words and not in_password,
               message=job.message)


def _tell_done(text: str, job: _Job) -> None:
    """The ready beep and the "Transcribed" notification, at the end of a
    dictation (not after each Type at pause phrase)."""
    if job.chunk_role == "middle":
        return
    beeps.beep(job.beeps_on, *READY_BEEP)
    if job.notify_on:
        notify_module.notify("Transcribed", _shortened(text, 80))


def _correction_or_held_cue(text: str, job: _Job):
    """(text, the phrase taken back) for a phrase that corrects the one
    before the pause; None when it's only the cue ("No wait."), held for
    the next phrase, which has the corrected value."""
    from . import ai_cleanup
    if job.chunk_role == "middle" and ai_cleanup.is_bare_cue(text):
        job.message.held_cue = text.strip()
        logger.info("Chunk is only a correction cue; waiting for the next phrase")
        return None
    return type_at_pause.correct_across_pause(text, job.message)


def _deliver(words: _Words, audio_f32, job: _Job) -> None:
    """Type the words into the active application, then what follows them.
    They go into the Recent Dictations list first, so they can be recovered
    even if typing lands in the wrong window or fails outright."""
    send_countdown.type_owed_space(before=words.text)
    text = words.text
    chunked = job.chunk_role is not None
    said_before = last_dictation.remember_said(text, chunked, job.message.serial,
                                               words.taken_back)
    try:
        outcome = delivery.inject_text(text, job.injection_mode, message=job.message)
    except Exception as e:
        # The transcription worked; only getting it into the window
        # didn't, so this is reported as undelivered, not as a
        # "Transcription error".
        logger.error(f"Injection error: {e}", exc_info=True)
        outcome = "failed"
        job.message.add(outcome, None)
    if outcome == "password_field":
        last_dictation.unsay(said_before)
    last_dictation.remember_shown(rs.state.last_inserted_text, chunked, outcome == "ok",
                                  job.message.serial)
    job.message.ends_on = ends_unfinished(words.raw)
    _after_the_words(outcome, words.said, words.press, words.space_left_off, job.chunk_role,
                     job.beeps_on, job.injection_mode, job.message)
    if outcome == "ok":
        _carry_on_after(words, text, audio_f32, job)
    if live.usage_stats:
        # After typing, so saving the counts never delays the text.
        # Counts only (words, seconds dictating); see stats.py.
        from .stats import count_words, record
        record(count_words(text), len(audio_f32) / engine.SAMPLE_RATE)


def _carry_on_after(words: _Words, text: str, audio_f32, job: _Job) -> None:
    """A Type at pause phrase went in: how the next phrase carries on from it."""
    if job.chunk_role == "middle" and not live.chunk_context_marks and not words.press:
        # Without the context pass the engine's own mark decides: after
        # a comma or none, the next chunk carries on lowercase.
        rs.state.continue_mid_sentence = not text.rstrip().endswith(type_at_pause.SENTENCE_ENDS)
    if words.held_mark is not None:
        # Only the end of the phrase is kept: the engine hears it with
        # the start of the next one to decide the mark between them.
        job.message.held_mark = {"mark": words.held_mark,
                                 "audio": audio_f32[-type_at_pause.context_samples():],
                                 "raw": words.raw, "typed": rs.state.last_inserted_text,
                                 "where": rs.state.last_inserted_where}


def stop_recording(
    beeps_on: bool,
    smart_quotes: bool,
    notify_on: bool,
    language: str | None = None,
    auto_space: bool = True,
    auto_period: bool = True,
    injection_mode: str = "type",
):
    """Stop recording, and transcribe and type it on the dictation worker,
    after whatever an earlier recording is still typing. Only the stop
    itself (finish_capture) runs here, so the hotkey loop goes back to
    reading keys at once: a dictation key pressed while text is still being
    typed starts the next recording."""
    edit_mode, rs.state.edit_mode = rs.state.edit_mode, False
    cap = recording.finish_capture(beeps_on, notify_on)
    if cap is None:
        return
    frames, rec_sr = cap
    message = rs.state.message
    if edit_mode:
        where = focused_window.look()       # the window the edit is for
        dictation_worker.submit(lambda: voice_edit.transcribe_and_edit(
            frames, rec_sr, beeps_on, language, injection_mode, message, where))
        return
    # Type at pause: the rest is the recording's last phrase, typed after
    # those already queued.
    chunk_role = "last" if rs.state.chunks_submitted else None
    if chunk_role:
        frames = recording.drop_speechless_rest(frames)
    dictation_worker.submit(lambda: transcribe_and_inject(
        frames, rec_sr, beeps_on, smart_quotes, notify_on, language, auto_space,
        auto_period, injection_mode, chunk_role=chunk_role, message=message))
