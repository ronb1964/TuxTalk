"""Things said instead of text: undo ("delete that", "clear everything"),
"fix that word", select and move by voice (TuxTalk Labs), and working out
what "edit that" should change. Each acts only on the window the last
dictation went to."""
import os
import re
import time

from . import beeps, focused_window, keyboard_output, last_dictation, notices, virtual_keyboard
from . import live_settings as live
from . import notify as notify_module
from . import recording_state as rs
from .beeps import CANCEL_BEEP, READY_BEEP
from .logger import setup_logger
from .undo import calculate_undo_length, detect_undo_command
from .window_classes import is_terminal_class

logger = setup_logger(__name__)


def handle_undo(raw: str, beeps_on: bool, notify_on: bool) -> bool:
    """Check if raw text is an undo command and execute it.

    Returns True if an undo was handled (caller should return early).
    """
    detected = detect_undo_command(raw)
    if not detected:
        return False

    undo_type, count = detected
    # Deleting changes the text a voice move remembered (nav_context).
    rs.state.nav_context = None
    label = undo_type if count == 1 else f"{count} {undo_type}s"
    logger.info(f"Undo command detected: {label}")
    if undo_type == 'everything':
        _clear_text_box(beeps_on, notify_on)
        return True

    if not rs.state.last_inserted_text:
        logger.info("Undo requested but no previous text to undo")
        beeps.beep(beeps_on, *CANCEL_BEEP)
        if notify_on:
            notify_module.notify("Nothing to undo", "")
        return True

    # Backspacing the last dictation's length in a different window would
    # delete text there that TuxTalk never typed. Unknown windows go ahead,
    # as before.
    where = focused_window.look()
    if keyboard_output.not_where_typed(where[0], where):
        logger.info(f"Undo refused: the last dictation went to {rs.state.last_inserted_window!r}, "
                    f"not {where[0]!r}")
        notices.edit_problem("undo_other_window", beeps_on)
        return True

    delete_count = calculate_undo_length(rs.state.last_inserted_text, undo_type, count)
    logger.info(f"Undo: deleting {delete_count} characters (last text was "
                f"{len(rs.state.last_inserted_text)} chars, count={count})")
    if delete_count > 0:
        _backspace_last(delete_count, label, beeps_on, notify_on, where)
    else:
        logger.info("Nothing to undo for this scope")
        beeps.beep(beeps_on, *CANCEL_BEEP)
    return True


def _clear_text_box(beeps_on, notify_on):
    """'Clear everything': empty the whole text box (Ctrl+A, Backspace),
    TuxTalk's typing or not. In a terminal Ctrl+A jumps to the start of the
    line and Backspace there deletes nothing, so the line is cleared the
    shell's way: Ctrl+E to its end, Ctrl+U back to the prompt (bash, zsh,
    fish; Ctrl+A is also screen's prefix key)."""
    backend = keyboard_output.output_backend()
    where = focused_window.look()
    if is_terminal_class(where[0]):
        cleared = _sent_into(where, lambda: backend.press_keys([(("CTRL",), "E"),
                                                                (("CTRL",), "U")]))
    else:
        cleared = _sent_into(where, backend.select_all_delete)
    if cleared == "moved":
        notices.edit_problem("focus_moved", beeps_on)
    elif cleared:
        rs.state.last_inserted_text = ""
        last_dictation.forget_shown()
        rs.state.continue_mid_sentence = False
        beeps.beep(beeps_on, *READY_BEEP)
        if notify_on:
            notify_module.notify("Cleared the text box", "")
    else:
        logger.warning("Could not clear the text box (text injection unavailable)")
        beeps.beep(beeps_on, *CANCEL_BEEP)
        if notify_on:
            notify_module.notify("Couldn't clear the text box", "")


def _sent_into(where, send):
    """*send*(), a call of the output backend, with its keys going only into
    *where*, the look the command was checked against (keyboard_output.only_into):
    True when they went in, "moved" when another window had the focus by
    the time they would have, else False."""
    try:
        with keyboard_output.only_into(where):
            if send():
                return True
    except virtual_keyboard.GaveUp as stop:
        # The portal's way of saying it (output_backends._with_session).
        return "moved" if stop.reason == "moved" else False
    # The virtual keyboard's: it doesn't say why nothing was sent.
    return "moved" if focused_window.changed(where, focused_window.look()) else False


def _backspace_last(delete_count, label, beeps_on, notify_on, where):
    """Delete the last *delete_count* characters of the last dictation in
    *where*, the window undo was checked in, and shrink the undo buffer only
    once the deletion is confirmed; otherwise it falls out of step with the
    document and the next undo backspaces over the user's own text."""
    try:
        deleted = _sent_into(where, lambda: keyboard_output.output_backend()
                             .backspaces(delete_count))
    except virtual_keyboard.StoppedPartway:
        # Some of the text is gone and some isn't, by an unknown amount, so
        # the buffer no longer matches the document. Forgetting it is the
        # only safe choice: a later undo then deletes nothing.
        logger.error("Undo stopped partway: an unknown number of backspaces landed; "
                     "undo buffer cleared")
        rs.state.last_inserted_text = ""
        last_dictation.forget_shown()
        rs.state.continue_mid_sentence = False
        beeps.beep(beeps_on, *CANCEL_BEEP)
        if notify_on:
            notify_module.notify("Undo stopped partway", "Some of the text may still be there.")
        return
    if deleted == "moved":
        notices.edit_problem("focus_moved", beeps_on)
        return
    if not deleted:
        logger.error("Undo failed: backspaces were not delivered; buffer left unchanged")
        beeps.beep(beeps_on, *CANCEL_BEEP)
        if notify_on:
            notify_module.notify("Undo failed", "The text couldn't be deleted.")
        return
    last_dictation.trim_shown(rs.state.last_inserted_text, delete_count)
    if delete_count >= len(rs.state.last_inserted_text):
        rs.state.last_inserted_text = ""
        rs.state.continue_mid_sentence = False
    else:
        rs.state.last_inserted_text = rs.state.last_inserted_text[:-delete_count]
        remaining = rs.state.last_inserted_text.rstrip()
        if remaining and not remaining.endswith(('.', '?', '!', '\u2026')):
            rs.state.continue_mid_sentence = True
            logger.info("Mid-sentence continuation enabled for next dictation")
        else:
            rs.state.continue_mid_sentence = False
    beeps.beep(beeps_on, *READY_BEEP)
    if notify_on:
        notify_module.notify(f"Undid the last {label}", "")


# "fix that word": the whole dictation is one of these (normalized: lower case,
# no punctuation). Fix a Word opens on the dictation before it.
_FIX_WORD_PHRASES = frozenset({
    "fix that", "fix that word", "fix a word", "fix the word", "fix this word",
    "fix the last word", "fix that please", "fix that word please",
})


def handle_fix_word(raw: str, beeps_on: bool) -> bool:
    """"fix that word": open Fix a Word over the app, on the newest dictation.
    True when the dictation was that command."""
    said = " ".join(re.sub(r"[^\w\s]", " ", raw.lower()).split())
    if said not in _FIX_WORD_PHRASES:
        return False
    logger.info("Voice command: fix that word")
    # Right after a dictation, in its window, Fix a Word can also fix it in
    # the text: it gets exactly what was typed, through a file only this
    # user can read (the runtime folder), which it deletes once read.
    args = []
    focused = focused_window.window_class()
    typed = last_dictation.on_screen()
    handoff = None
    if typed.strip() and not keyboard_output.not_where_typed(focused):
        handoff = _hand_over(typed, focused)
    if handoff:
        args = ["--in-place", handoff]
        # The text is about to change: undo and voice moves can't trust
        # their counts any more.
        rs.state.last_inserted_text = ""
        last_dictation.forget_shown()
        rs.state.nav_context = None
    try:
        from .explain import start_helper_window
        window = start_helper_window("tuxtalk.fix_word_dialog", *args)
        beeps.beep(beeps_on, *READY_BEEP)
    except Exception as e:
        logger.warning(f"Could not open Fix a Word: {e}")
        if handoff:
            _remove_handoff(handoff)
        beeps.beep(beeps_on, *CANCEL_BEEP)
        notify_module.notify("Fix a Word didn't open", "Choose it in the TuxTalk menu instead.")
        return True
    if handoff:
        _remove_when_closed(window, handoff)
    return True


def _hand_over(typed, focused):
    """A file holding what was typed, for Fix a Word to fix in the text, or
    None when it couldn't be written. Only this user can read it (the runtime
    folder, mode 600); the window deletes it once read, and a name of its own
    keeps a later request's file apart. Never in /tmp, which may be on
    disk: without a private runtime folder there's no hand-over."""
    import json
    import tempfile
    from .single_instance import private_session_dir
    folder = private_session_dir()
    if folder is None:
        logger.info("Fix a Word can't fix it in the text: no private runtime folder")
        return None
    try:
        # The uninstall finds a file left behind by this name.
        fd, path = tempfile.mkstemp(dir=folder, prefix="tuxtalk-fix-word-", suffix=".json")
    except OSError as e:
        logger.info(f"Fix a Word can't fix it in the text: {e}")
        return None
    try:
        with os.fdopen(fd, "w") as f:
            # Which of the app's windows, where the desktop says (two
            # KWrite documents), for the fix to go only there.
            json.dump({"text": typed, "window": focused,
                       "where": rs.state.last_inserted_where[1]}, f)
    except (OSError, TypeError, ValueError) as e:
        logger.info(f"Fix a Word can't fix it in the text: {e}")
        _remove_handoff(path)
        return None
    return path


def _remove_handoff(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass                    # the window read it and deleted it
    except OSError as e:
        logger.info(f"Couldn't remove Fix a Word's copy of the dictation: {e}")


def _remove_when_closed(window, path):
    """Delete *path* once Fix a Word's process *window* ends, should it end
    before reading it (GTK failing to load, say): the dictation mustn't stay
    in the runtime folder until logout."""
    import threading

    def wait():
        window.wait()
        _remove_handoff(path)
    threading.Thread(target=wait, daemon=True, name="fix-word-handoff").start()


def handle_navigation(raw: str, beeps_on: bool, where=None) -> bool:
    """Select and move by voice (TuxTalk Labs). True when the whole dictation
    was one of voice_navigation's commands, done or refused with a notice;
    False to type it as text. *where* is the window the keys may go into, a
    look (a voice edit's, taken when it started), else the one looked at here."""
    if not live.voice_navigation:
        return False
    from . import voice_navigation as nav
    command = nav.parse(raw)
    if command is None:
        return False
    logger.info(f"Voice command: {command.name}")
    if where is None:
        where = focused_window.look()
    if is_terminal_class(where[0]):
        notices.edit_problem("nav_terminal", beeps_on)
        return True
    # Right after a dictation the cursor is at its end; after an exact move,
    # nav_context says where in it. Only in the window it was typed into:
    # dictating into a chat, then saying "select the last sentence" in Writer
    # would otherwise select the chat message's length of Writer's text.
    if keyboard_output.not_where_typed(where[0], where):
        left, right = None, ""
    elif rs.state.last_inserted_text:
        left, right = rs.state.last_inserted_text, ""
    else:
        left, right = rs.state.nav_context or (None, "")
    plan = nav.plan(command, left, right)
    if plan is None:
        # One message for "just restarted" and "dictated somewhere else since":
        # both come down to the one rule (it teaches the rule, too).
        notices.edit_problem("nothing_said", beeps_on)
        return True
    try:
        pressed = _sent_into(where, lambda: keyboard_output.output_backend()
                             .press_keys(plan.chords))
    except Exception as e:
        logger.error(f"Voice command: pressing the keys failed: {e}")
        pressed = False
    # The cursor has moved (or may have): undo's character count no longer
    # matches the document, and "delete last word" would eat the wrong text.
    rs.state.last_inserted_text = ""
    last_dictation.forget_shown()
    rs.state.nav_context = ((plan.left, plan.right) if pressed is True and plan.left is not None
                            else None)
    if pressed is True:
        beeps.beep(beeps_on, *READY_BEEP)
    else:
        notices.edit_problem("focus_moved" if pressed == "moved" else "shortcut_failed",
                             beeps_on)
    return True


def spoken_edit_target():
    """What "edit that" changes: the last dictation on screen, every phrase
    of its recording since the last Enter with Type at pause, as "fix that
    word" gets it (last_dictation.on_screen); "" when there's nothing fresh
    in the focused window."""
    typed = last_dictation.on_screen()
    if not typed.strip() or keyboard_output.not_where_typed(focused_window.window_class()):
        return ""
    return typed


# "edit that" this soon after TuxTalk pressed Enter meant the sent dictation.
EDIT_AFTER_SEND_S = 60


def refused_as_sent(beeps_on: bool) -> bool:
    """"edit that" with nothing just typed: True when TuxTalk pressed Enter a
    moment ago, said with a notice (typing "Edit that, make it formal" into
    the box would only add to the confusion); False to type the words."""
    sent = rs.state.sent
    if sent is None or time.time() - sent["at"] >= EDIT_AFTER_SEND_S:
        return False
    notices.edit_problem("already_sent_auto" if sent["automatic"] else "already_sent", beeps_on)
    return True
