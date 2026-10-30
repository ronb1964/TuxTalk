"""Voice editing (TuxTalk Labs): change the selected text as the user
says ("make this more formal", "remove the commas", "make this bold"),
in code where an exact rule covers it (voice_edit_rules.py) and by the
local AI where not, then type the result over the selection. The
clipboard the selection was copied through is put back afterwards."""
import functools
import threading
import time

from . import (
    beeps,
    delivery,
    delivery_notices,
    dictation_text,
    engine,
    focused_window,
    keyboard_output,
    last_dictation,
    notices,
    recording,
    silence,
    type_at_pause,
    voice_commands,
    voice_edit_rules,
)
from . import live_settings as live
from . import recording_state as rs
from .beeps import READY_BEEP
from .logger import setup_logger
from .normalize import swiss_spelling
from .window_classes import is_terminal_class

logger = setup_logger(__name__)

# How long after typing the rewrite the user's clipboard is put back: after
# ClipboardKeeper's own restore (0.5 s) when "Restore clipboard" is on, and
# after the app has read a pasted rewrite.
EDIT_RESTORE_DELAY_S = 1.0


def focus_moved(focused_class, window=None) -> bool:
    """Whether another window has the focus than when the edit started, of
    class *focused_class*: another app, or, by *window* (focused_window.look's
    which-window part, None when not known), another window of the same app
    (two KWrite documents). A window that can't be found now counts as
    moved: it can't be checked."""
    now = focused_window.window_class()
    if now is None and focused_class is not None:
        logger.info("Voice edit: the focused window can't be found now, so it can't be checked")
    # KWin lowercases the class and X11 doesn't (focused_window.same_window).
    if (now or "").casefold() != (focused_class or "").casefold():
        return True
    return window is not None and focused_window.changed((focused_class, window),
                                                         focused_window.look())


def transcribe_and_edit(frames, rec_sr, beeps_on, language, injection_mode, message, where):
    """Voice editing: the recording, whose *message* it is, is an instruction
    ("make this more formal") for the text selected in the focused app, which
    the AI rewrites and the rewrite replaces. Ctrl+Z in the app undoes it.

    *where* is the window that had the focus when the recording stopped
    (focused_window.look), None when not known. The edit can wait its turn
    behind a dictation still being typed; another window that has the focus
    by then, its own selection perhaps, is left alone."""
    t0 = time.time()
    audio_f32 = recording.frames_f32(frames)
    if audio_f32.size == 0:
        return
    if rec_sr != engine.SAMPLE_RATE:
        audio_f32 = recording.resample(audio_f32, rec_sr, engine.SAMPLE_RATE)
    if silence.is_speechless_tap(audio_f32, engine.SAMPLE_RATE):
        return

    if not keyboard_output.is_wayland_session():
        # The selection is read with wl-copy / wl-paste, which have no X11
        # side; without this every edit would say "select some text first".
        notices.edit_problem("needs_wayland", beeps_on)
        return
    if where is not None and focused_window.changed(where, focused_window.look()):
        notices.edit_problem("focus_moved", beeps_on)
        return
    focused_class = focused_window.window_class()
    if is_terminal_class(focused_class):
        notices.edit_problem("terminal", beeps_on)
        return
    heard = []

    def instruction():
        if not heard:
            heard.append(engine.transcribe(audio_f32, language))
        return heard[0]
    edit_selection(instruction, beeps_on, injection_mode, focused_class, t0, message,
                   window=where[1] if where is not None else None)


def _only_into(focused_class, window):
    """virtual_keyboard.send's claim for keys that may only go into the
    window the edit started in, made right before the first key goes down.
    The checks before the keys (the password-field question can take a
    quarter of a second) and a wait for a held Right Ctrl leave time to
    switch windows."""
    return functools.partial(focused_window.claim_into, (focused_class, window))


def _not_pressed(focused_class, window, beeps_on) -> None:
    """Tell the user why keys meant for the selection didn't go in: the
    window changed right before them (the claim), or they couldn't be sent."""
    moved = focus_moved(focused_class, window)
    notices.edit_problem("focus_moved" if moved else "shortcut_failed", beeps_on)


def _delete_selection(focused_class, window, beeps_on) -> bool:
    """"Delete this": Delete on the selection, still selected in
    *focused_class*. True when it was pressed. Transcribing can take
    seconds, and Delete must not reach a window the user has switched to
    meanwhile, so it needs a window to compare with."""
    if focused_class is None:
        logger.info("Voice edit: the focused window is unknown, so Delete can't be checked")
        notices.edit_problem("cant_check_window", beeps_on)
        return False
    if notices.refuse_password_field(beeps_on):
        return False
    if focus_moved(focused_class, window):
        notices.edit_problem("focus_moved", beeps_on)
        return False
    if not keyboard_output.send_keys([(111, 1), (111, 0)], what="delete",     # KEY_DELETE
                                     claim=_only_into(focused_class, window)):
        _not_pressed(focused_class, window, beeps_on)
        return False
    logger.info("Voice edit: deleted the selection")
    beeps.beep(beeps_on, *READY_BEEP)
    return True


def _format_selection(fmt, focused_class, window, beeps_on) -> bool:
    """Bold, italic or underline (*fmt*, voice_edit_rules.formatting) with
    the app's own shortcut on the selection, still selected. True when it
    was pressed."""
    if not voice_edit_rules.has_formatting_shortcuts(focused_class):
        logger.info(f"Voice edit: no formatting shortcuts in {focused_class!r}")
        notices.edit_problem("no_shortcuts", beeps_on)
        return False
    if notices.refuse_password_field(beeps_on):
        return False
    if focus_moved(focused_class, window):
        notices.edit_problem("focus_moved", beeps_on)
        return False
    claim = _only_into(focused_class, window)
    if not all(keyboard_output.send_format_shortcut(kind, claim) for kind in fmt):
        _not_pressed(focused_class, window, beeps_on)
        return False
    logger.info(f"Voice edit: {', '.join(fmt)} with the app's shortcut ({focused_class})")
    beeps.beep(beeps_on, *READY_BEEP)
    return True


def _nothing_selected(get_instruction, spoken, focused_class, beeps_on):
    """Nothing is selected. "select the last sentence" needs nothing
    selected: it makes the selection the next edit works on. Anything else
    says to select some text first. The clipboard is already back
    (copy_selection does that)."""
    if live.voice_navigation and not spoken:
        said = get_instruction()
        if said and voice_commands.handle_navigation(said, beeps_on, focused_class):
            return
    notices.edit_problem("no_selection", beeps_on)


def _put_clipboard_back(put_back, result):
    """Put back the clipboard the selection was copied through, a moment
    later (EDIT_RESTORE_DELAY_S) so a paste still under way finishes first.
    A pasted rewrite (*result*) may still be on the clipboard, whole or
    (pasted a line at a time) its last line; that may be replaced."""
    also = (result, *result.split("\n")) if result else ()
    if EDIT_RESTORE_DELAY_S:
        threading.Timer(EDIT_RESTORE_DELAY_S, put_back, kwargs={"also": also}).start()
    else:
        put_back(also=also)


# _edit_with_keys's answer for an instruction that needs the text rewritten.
_NOT_A_KEY_EDIT = object()


def _edit_with_keys(instruction, focused_class, window, beeps_on):
    """Deleting and formatting, which need no AI and press keys only: what
    the edit returned, or _NOT_A_KEY_EDIT for a rewrite."""
    if voice_edit_rules.is_delete(instruction):
        return _delete_selection(focused_class, window, beeps_on)
    fmt = voice_edit_rules.formatting(instruction)
    if fmt == voice_edit_rules.CANT_FORMAT:
        notices.edit_problem("cant_format", beeps_on)
        return None
    if fmt:
        return _format_selection(fmt, focused_class, window, beeps_on)
    return _NOT_A_KEY_EDIT


def _rewritten(selected, instruction, beeps_on):
    """*selected* changed as *instruction* says, in code or by the AI, ready
    to type; None when there's nothing to type (and the user has been told)."""
    from . import ai_cleanup
    # Deleting and formatting press keys only, so any length will do; a
    # rewrite is typed back, and the AI's context holds only so much.
    if len(selected) > ai_cleanup.MAX_EDIT_CHARS:
        notices.edit_problem("too_long", beeps_on)
        return None
    # "make it formal and remove the commas" is two changes, made one after
    # the other, each in code or by the AI (split_steps).
    steps = voice_edit_rules.split_steps(instruction)
    if len(steps) > 1:
        logger.info(f"Voice edit: {len(steps)} changes, one at a time")
    text = _apply_steps(selected, steps, beeps_on)
    if text is None:
        return None
    result = text if text != selected else None
    if result and live.swiss_spelling:
        result = swiss_spelling(result)
    if result:
        result = dictation_text.numbers_to_digits(result)
    # Exact: for an edit done in code, spacing may be the whole change
    # (indenting one line); the AI's spacing-only answers are dropped above.
    if not result or result == selected:
        notices.edit_problem("no_answer", beeps_on)
        return None
    return result


def _apply_steps(selected, steps, beeps_on):
    """Make each change of *steps* to *selected* in turn, in code where it
    can be (voice_edit_rules.mechanical_edit) and by the AI where not: the
    changed text, or None when the AI was needed and couldn't help (and the
    user has been told why)."""
    ai = live.ai_engine
    text = selected
    for step in steps:
        done = voice_edit_rules.mechanical_edit(text, step)
        if done is not None:
            logger.info("Voice edit done in code, without the AI"
                        + (f" ({live.loggable(step)})" if len(steps) > 1 else ""))
        elif ai is None:
            notices.edit_problem("not_installed", beeps_on)
            return None
        elif not ai.is_ready():
            notices.edit_problem("not_ready", beeps_on)
            return None
        else:
            done = ai.edit(text, step)
            if done and done.split() == text.split():
                # Only spacing changed ("select all" got the line ends
                # trimmed): not an edit anyone asked for.
                done = None
            if done is None and len(steps) > 1:
                # One change the AI had nothing for needn't cost the others.
                logger.info(f"Voice edit: no change for {live.loggable(step)}")
        if done is not None:
            text = done
    return text


def _type_rewrite(result, injection_mode, message, beeps_on, into):
    """Type or paste *result* over the selection, which is still active, in
    one block (see delivery.inject_text), as part of *message*, and only
    into *into* (a look, or None when the window isn't known); True when it
    all went in."""
    from . import history
    try:
        outcome = delivery.inject_text(result, injection_mode, one_block=True, into=into,
                                       message=message)
    except Exception as e:
        logger.error(f"Voice edit: typing the rewrite failed: {e}", exc_info=True)
        outcome = "failed"
        message.add(outcome, None)      # what's in the box isn't known now
    if outcome == "password_field":
        history.forget(result)
    if outcome in delivery_notices.NOTICES:
        notices.report_undelivered(outcome, beeps_on)
    else:
        beeps.beep(beeps_on, *READY_BEEP)
    return outcome == "ok"


def edit_selection(get_instruction, beeps_on, injection_mode, focused_class, t0, message,
                   spoken=False, window=None):
    """Change the text selected in *focused_class* as the instruction
    *get_instruction()* says (called only once a selection is found), and
    type the result over it, as part of *message*. *spoken*: the instruction
    came after "edit that" in a dictation, which selected the text itself.
    *window* says which of the app's windows it is, where the desktop says
    (focus_moved). True when the change went in."""
    from . import clipboard, history
    # Before copying the selection or hearing the instruction; checked
    # again before the rewrite replaces it.
    if notices.refuse_password_field(beeps_on):
        return
    # The selection first, so "select some text first" comes up even when
    # nothing was said. Ctrl+C in a terminal switched to during the password
    # check would stop the program running there.
    copy_sent = []

    def send_copy():
        copy_sent.append(keyboard_output.send_copy_keystroke(_only_into(focused_class, window)))
        return copy_sent[-1]
    try:
        selected, put_back = clipboard.copy_selection(send_copy)
    except clipboard.ClipboardUnavailable:
        notices.edit_problem("no_clipboard", beeps_on)
        return
    if copy_sent == [False]:
        _not_pressed(focused_class, window, beeps_on)
        return
    if not selected:
        _nothing_selected(get_instruction, spoken, focused_class, beeps_on)
        return
    result = None
    try:
        instruction = get_instruction()
        if not instruction:
            notices.edit_problem("no_instruction", beeps_on)
            return
        if voice_commands.handle_navigation(instruction, beeps_on, focused_class):
            return
        logger.info(f"Voice edit: {live.loggable(instruction)} ({len(selected)} characters)")
        keys = _edit_with_keys(instruction, focused_class, window, beeps_on)
        if keys is not _NOT_A_KEY_EDIT:
            return keys
        result = _rewritten(selected, instruction, beeps_on)
        if result is None or notices.refuse_password_field(beeps_on):
            return
        logger.info(f"Voice edit result: {live.loggable(result)} "
                    f"(took {time.time() - t0:.2f}s)")
        history.add_entry(result)
        into = (focused_class, window)
        if focused_class is None:
            # Typed unchecked, as ordinary dictation is: refusing here would
            # turn voice editing off wherever the window can't be found.
            logger.info("Voice edit: the focused window is unknown, so typing goes ahead unchecked")
            into = None
        elif focus_moved(focused_class, window):
            # Typing now would put the rewrite into whatever window has focus.
            notices.edit_problem("window_changed", beeps_on)
            return
        return _type_rewrite(result, injection_mode, message, beeps_on, into)
    finally:
        _put_clipboard_back(put_back, result)


def handle_spoken_edit(raw: str, chunk_role, beeps_on: bool, injection_mode: str,
                       message) -> bool:
    """Voice editing by voice alone (TuxTalk Labs): a dictation that starts
    with "edit that" selects what TuxTalk just typed and changes it as the
    rest says, as Shift+F8 does for a selection. True when it was handled
    (done, or refused with a notice); False to type the words as text, which
    is also what happens when nothing was just typed to edit."""
    if not live.voice_editing:
        return False
    from . import voice_navigation as nav
    waiting, message.edit_waiting = message.edit_waiting, None
    if waiting is not None:
        instruction, target = raw, waiting
    else:
        instruction = voice_edit_rules.spoken_edit(raw)
        if instruction is None:
            return False
        target = voice_commands.spoken_edit_target(message)
        if not target:
            return voice_commands.refused_as_sent(beeps_on)
    logger.info(f"Voice command: edit that ({len(target)} characters)")
    if not keyboard_output.is_wayland_session():
        notices.edit_problem("needs_wayland", beeps_on)
        return True
    # Taken before selecting: the select keys, Delete or the rewrite go only
    # into this window, and it must be the one the text was typed into. A
    # click into another document (during the pause, or just now) would have
    # as many characters of its text selected and rewritten.
    where = focused_window.look()
    if keyboard_output.not_where_typed(where[0], where):
        notices.edit_problem("focus_moved", beeps_on)
        return True
    focused_class = where[0]
    if is_terminal_class(focused_class):
        notices.edit_problem("terminal", beeps_on)
        return True
    if not instruction.strip():
        if chunk_role == "middle":
            # "edit that", then a pause: the next phrase says what to change.
            message.edit_waiting = target
            logger.info("Voice edit: waiting for the next phrase to say what to change")
        else:
            notices.edit_problem("edit_that_what", beeps_on)
            message.add("failed", None)     # asked to change it: not sent as it is
        return True
    plan = nav.plan(nav.parse("select that"), target, "")
    try:
        selected = plan is not None and keyboard_output.output_backend().press_keys(
            plan.chords, claim=_only_into(*where))
    except Exception as e:
        logger.error(f"Voice edit: selecting the text failed: {e}")
        selected = False
    # The text is selected now (and then replaced): undo's count and the
    # screen copy no longer match it, and nothing in it reaches the next phrase.
    rs.state.last_inserted_text = ""
    type_at_pause.start_afresh(message, ended=False)
    last_dictation.forget_shown()
    if not selected:
        notices.edit_problem("shortcut_failed", beeps_on)
    elif edit_selection(lambda: instruction, beeps_on, injection_mode, focused_class,
                        time.time(), message, spoken=True, window=where[1]):
        return True
    # Not changed as asked: no automatic Enter sends it as it was.
    message.add("failed", None)
    return True
