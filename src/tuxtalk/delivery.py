"""Putting a finished dictation into the focused window: paste or type,
as the window and the settings call for, with line breaks that don't send
a chat message (Shift+Enter) and never into a password field. The outcome
("ok", "partial", "failed", "password_field") says how much arrived, and
the undo buffer holds only what did."""
import os
import re
import time

from . import focused_window, keyboard_output, last_dictation, send_countdown, virtual_keyboard
from . import recording_state as rs
from .logger import setup_logger
from .normalize import LINE_BREAK
from .window_classes import (
    ELECTRON_PASTE_BROKEN_CLASSES,
    SMART_NEWLINE_CLASSES_LOWER,
    is_terminal_class,
)

logger = setup_logger(__name__)


def typed_breaks_for(text: str, window_class) -> str:
    """*text* ready to type into *window_class*. Typed line breaks go in as
    Shift+Enter, but terminals such as gnome-terminal treat Shift+Enter as
    Enter and would run the half-dictated command, so in a terminal every
    line break becomes a space. Pasting (Wayland) keeps them: a terminal takes
    pasted newlines as text."""
    if not is_terminal_class(window_class):
        return text
    joined = re.sub(r"[ \t]*(?:" + re.escape(LINE_BREAK) + r"|\n)+[ \t]*", " ", text)
    if joined != text:
        logger.info("Typing into a terminal: line breaks typed as spaces")
    return joined


def determine_injection_method(injection_mode: str) -> tuple[str, str]:
    """
    Decide between typing and pasting for this dictation.

    Args:
        injection_mode: User's configured mode ("type", "paste", or "auto")

    Returns:
        Tuple of (actual_mode, reason)
        - actual_mode: "type" or "paste" (what to actually use)
        - reason: str (explanation for logging)

    Auto pastes on Wayland: the paste keystroke is picked per paste from the
    focused window's class (Ctrl+Shift+V in a terminal), and Electron apps where
    paste is broken are typed into instead, in inject_text. It types on X11.
    """
    # Inside a Flatpak, clipboard-paste needs wl-copy (not bundled) and the libei
    # output backend types directly — always type, never attempt paste.
    if os.environ.get("FLATPAK_ID"):
        return ("type", "Flatpak: libei typing")

    # If not auto mode, use user's choice directly
    if injection_mode.lower() != "auto":
        mode = "paste" if injection_mode.lower() == "paste" else "type"
        return (mode, f"User selected {mode} mode")

    # Pasting needs wl-copy, which only talks to a Wayland compositor. On X11
    # every paste would fail and fall back to typing anyway.
    if not keyboard_output.is_wayland_session():
        return ("type", "Auto: not a Wayland session, typing")
    return ("paste", "Auto: paste")


def plan_line_breaks(text: str, one_paste: bool) -> list[str]:
    """How to deliver *text*'s line breaks: the chunks to paste, in order.

    One chunk means one paste with real newlines in it. More than one means
    paste each and send Shift+Enter between them.

    Terminals bind no soft-newline key — Enter runs the command — so the
    keystroke is simply ignored there and everything lands on one line. What a
    terminal does accept is a newline inside pasted text: bracketed paste (on by
    default in modern bash and zsh) inserts it literally instead of executing at
    it. So a terminal gets one paste carrying real newlines, and so do KDE's
    editors (SMART_NEWLINE_CLASSES_LOWER), where a pasted newline copies
    nothing from the line above (in a code mode Kate still indents it, as
    any paste). Not a plain Enter there: Kate's terminal panel would run it.

    Everywhere else keeps the keystroke, because in a chat input like Claude
    Desktop a literal newline SUBMITS the message instead of breaking the line.
    """
    normalised = text.replace("\n", LINE_BREAK)
    if one_paste:
        return [normalised.replace(LINE_BREAK, "\n")]
    return normalised.split(LINE_BREAK)


def inject_text(text: str, injection_mode: str, one_block: bool = False, into=None,
                message=None) -> str:
    """Insert *text* into the active app (deliver_text), then remember the
    window it landed in, for undo, "fix that word" and select and move.
    *into*, a look taken earlier, is the only window it may go into: "moved"
    when another has the focus by the time it would. Words of *message*
    (send_countdown.Message) are added to it, with where they went, for an
    automatic Enter or a key said after them.

    The focus is checked once more after typing because the report from
    before can be out of date. On KDE, clicking the desktop reports
    plasmashell; wl-copy's clipboard window then hands the focus back to the
    last real window, and the text lands there, where "delete last
    sentence" must work. No automatic Enter follows it there: a focus that
    moved while the words went in can't be told from the user switching.
    """
    before = focused_window.destination()
    sends_before, landed = keyboard_output.this_thread.sends, None
    keyboard_output.this_thread.into = into
    try:
        # Checked again right before each key, after the waits for the paste
        # (wl-copy) or a dictation key held (keyboard_output.send_keys).
        if into is not None:
            focused_window.claim_into(into, before)
        outcome = deliver_text(text, injection_mode, one_block, where=before)
    except (virtual_keyboard.StoppedPartway, virtual_keyboard.GaveUp,
            keyboard_output.PasswordFieldNow) as stop:
        typed_some = keyboard_output.this_thread.sends != sends_before
        outcome = _delivery_stopped(stop, typed_some=typed_some)
    finally:
        keyboard_output.this_thread.into = None
    if outcome in ("ok", "partial"):
        after = focused_window.look()
        if after[0]:
            if after[0].casefold() != (rs.state.last_inserted_window or "").casefold():
                logger.info(f"The text went to {after[0]!r}, not {rs.state.last_inserted_window!r}")
            rs.state.last_inserted_window = after[0]
        rs.state.last_inserted_where = (rs.state.last_inserted_window, after[1])
        if outcome == "ok":
            landed = send_countdown.landed(before, after)
    if message is not None:
        message.add(outcome, landed)
    rs.state.deliveries += 1
    return outcome


def deliver_text(text: str, injection_mode: str, one_block: bool = False, *, where) -> str:
    """Determine the best injection method and insert text into the active app.

    Pastes or types as determine_injection_method decides, typing whatever a
    failed paste could not deliver.

    Returns how it went: "ok" (all of it arrived), "partial" (some chunks of a
    multi-part dictation arrived, then it stopped), "failed" (none did) or
    "password_field" (the focus is in one, so the rest wasn't sent).
    The caller tells the user about anything but "ok", so a dictation that
    never arrived doesn't fail silently.

    The focus may have moved into a password field since the text was made:
    *where* (focused_window.destination, asked right before) says, and so
    does a check before each further chunk of a multi-part delivery. No check
    closes the milliseconds between the last answer and the keys after it.

    *one_block* (voice editing) pastes text with line breaks in one go, real
    newlines and all. Line by line with Enter in between, an editor continues
    each "- " list line and the dashes pile up. Typed, the breaks still go in
    as Shift+Enter, since a plain Enter would send a chat message; that holds
    for every typed line break, not only voice editing's.
    """
    if where.password:
        return "password_field"
    # Which window this text goes into, so select and move by voice only
    # trusts "what I just said" in that same window.
    rs.state.last_inserted_window = focused_class = where.wm_class
    if focused_class in ELECTRON_PASTE_BROKEN_CLASSES:
        # A tab as four spaces, however it's typed: a real Tab moves the
        # focus from Claude Desktop's box to the Send button, and the next
        # space typed sends the message.
        text = text.replace("\t", "    ")
    # Determine injection method (auto mode does smart detection)
    detection_start = time.time()
    actual_mode, reason = determine_injection_method(injection_mode)
    detection_time = time.time() - detection_start
    if detection_time > 0.1:
        logger.warning(f"Injection method detection slow: {detection_time:.2f}s")

    injection_start = time.time()

    # Electron-paste-broken override: in an Electron app where a synthetic
    # Ctrl+V silently fails on Wayland, type instead of pasting. Only for
    # auto and paste: "type" types anyway.
    if (injection_mode != "type" and focused_class in ELECTRON_PASTE_BROKEN_CLASSES
            and not os.environ.get("FLATPAK_ID")):
        outcome = _type_for_electron(text, focused_class, injection_start)
        if outcome is not None:
            return outcome

    use_paste = (actual_mode == "paste")
    logger.info(f"Injection mode: configured={injection_mode!r}, actual={actual_mode!r}, "
                f"reason={reason}")

    # Tracks whether text actually reached the focused app. Only a confirmed
    # injection updates the undo buffer; otherwise a later "undo last word"
    # would backspace characters the user's document never received.
    inject_ok = True
    if use_paste and not one_block and (LINE_BREAK in text or "\n" in text):
        outcome = _paste_in_parts(text, focused_class)
        if outcome is not None:
            return outcome
    elif not (use_paste and _paste_whole(text, injection_start)):
        text, inject_ok = _type_all(text, injection_start)

    return _remember_delivered(text, inject_ok)


def _delivery_stopped(stop, typed_some: bool) -> str:
    """The outcome of a delivery that *stop* ended early, after forgetting
    what undo, "select that" and the next dictation's capital count on.

    StoppedPartway: an unknown part of the text is in the app; typing or
    pasting it again would double that part. PasswordFieldNow: the lines
    before it are in the app, but in which form (pasted, typed after a
    failed paste) isn't tracked. GaveUp: a Right Ctrl or Right Alt dictation
    key stayed down, the window changed while typing waited for it
    (modifier_hotkeys), or it isn't the one the text had to go into
    (focused_window.claim_into); typing on any other way (wtype, the clipboard) would
    do what the wait was there to prevent. *typed_some*: earlier lines were
    typed before it gave up."""
    rs.state.last_inserted_text = ""
    rs.state.continue_mid_sentence = False
    rs.state.nav_context = None
    last_dictation.forget_shown()
    if isinstance(stop, keyboard_output.PasswordFieldNow):
        logger.warning("Stopped typing: a password field has the focus now")
        return "password_field"
    if isinstance(stop, virtual_keyboard.GaveUp) and not typed_some:
        return stop.reason          # logged where it gave up
    if stop.reason:
        logger.warning(f"Typing stopped partway through the dictation ({stop.reason})")
    else:
        logger.error("The virtual keyboard stopped partway through the dictation")
    return "partial"


def _paste_whole(text, injection_start):
    """Paste *text* in one go; True if it went in."""
    if not keyboard_output.paste_text(text):
        return False
    injection_time = time.time() - injection_start
    logger.info(f"Text injected via paste: {len(text)} chars in {injection_time:.2f}s")
    if injection_time > 1.0:
        logger.warning(f"Paste injection slow: {injection_time:.2f}s")
    return True


def _remember_delivered(text, inject_ok):
    """Keep what was delivered for undo, and return the outcome."""
    # Track injected text for undo — but ONLY when injection actually
    # succeeded. Line-break markers are stored as '\n' (one keystroke on
    # screen = one character in the buffer) so undo backspace counts stay in
    # sync with what was typed. A failed typing run may have typed part of
    # the text before it stopped, so the buffer matches neither this
    # dictation nor the last one: empty it, and undo then deletes nothing.
    if inject_ok:
        rs.state.last_inserted_text = text.replace(LINE_BREAK, "\n")
        logger.debug(f"Kept {len(rs.state.last_inserted_text)} chars for undo")
    else:
        rs.state.last_inserted_text = ""
        logger.warning("Injection failed — undo buffer cleared")
    return "ok" if inject_ok else "failed"


def _type_for_electron(text, focused_class, injection_start):
    """Type *text* into an Electron app where a synthetic paste fails, line
    breaks as Shift+Enter between the parts. The outcome, or None when
    nothing landed and the normal path may try again."""
    logger.info(f"Electron-paste-broken override: class={focused_class!r}, typing")
    parts = text.replace("\n", LINE_BREAK).split(LINE_BREAK)
    delivered = keyboard_output.deliver_lines(parts, keyboard_output.type_text_raw, 0.03, 0.03)
    if not delivered.stopped:
        injection_time = time.time() - injection_start
        logger.info(f"TIMING: Electron typing completed in {injection_time:.2f}s")
        # Kept for undo in the typed form: tabs already four spaces, each
        # line break one "\n", so backspace counts match what was sent.
        rs.state.last_inserted_text = "\n".join(parts)
        logger.debug(f"Kept {len(rs.state.last_inserted_text)} chars for undo (typed)")
        return "ok"
    if not delivered.parts:
        # Nothing landed, so the normal injection path can safely try again.
        logger.warning("Typing failed, falling through to normal injection")
        return None
    # Partial delivery: parts already in the document must not be sent
    # again by the fallback, or the user gets a duplicate copy.
    rs.state.last_inserted_text = delivered.landed(parts)
    logger.warning(f"Typing stopped after {delivered.parts} chunk(s) — "
                   "not re-injecting, undo buffer holds only what landed")
    return "password_field" if delivered.stopped == "password_field" else "partial"


def _paste_part(part) -> bool:
    """Paste one part of a hybrid paste, then give it a moment to land."""
    logger.info(f"Pasting a part: {len(part)} chars")
    if not keyboard_output.paste_text(part):
        return False
    time.sleep(0.08)
    return True


def _paste_in_parts(text, focused_class):
    """Smart hybrid paste: text with line breaks pasted part by part with
    Shift+Enter between, and whatever a failed paste couldn't deliver typed.
    None when the paste put all of it in, else the outcome of typing the rest
    or where it stopped."""
    # A terminal honours a newline inside a paste, but treats Shift+Enter
    # like Enter, so it gets one chunk carrying real newlines and no
    # keystrokes at all; so do KDE's editors (plan_line_breaks).
    parts = plan_line_breaks(text, is_terminal_class(focused_class)
                             or (focused_class or "").lower() in SMART_NEWLINE_CLASSES_LOWER)
    logger.info(f"Smart hybrid mode: {len(parts)} part(s)")
    delivered = keyboard_output.deliver_lines(parts, _paste_part, 0, 0.05)
    if not delivered.stopped:
        logger.info(f"Smart hybrid paste completed: {len(parts)} chunks")
        return None
    if delivered.stopped == "password_field":
        # Not asked again: a second answer that comes back "no" (a slow
        # app, out of time) would type the rest into the password field.
        # The chunks before reached the document, and so did the line
        # break after the last of them: the check comes after it.
        rs.state.last_inserted_text = delivered.landed(parts)
        logger.warning(f"Stopped after {delivered.parts}/{len(parts)} chunks: a password field "
                       "has the focus now")
        return "password_field"
    # Only the rest is typed: delivering the whole text again would
    # duplicate the chunks that landed, which undo can't clean up.
    if delivered.stopped == "part":
        logger.warning(f"Paste failed on part {delivered.parts + 1}; will type the remainder")
    else:
        logger.warning(f"Line break failed after part {delivered.parts}; "
                       "will type the remainder")
    return _type_rest(parts, delivered)


def _type_rest(parts, delivered):
    """Type the parts a hybrid paste stopped before, with the line break
    that failed first when that's where it stopped: the outcome, with what
    went in kept for undo."""
    remainder = ((LINE_BREAK if delivered.stopped == "break" else "")
                 + LINE_BREAK.join(parts[delivered.parts:]))
    # A terminal's single chunk carries real newlines for bracketed
    # paste. Typed, a newline is a plain Enter and Shift+Enter is no
    # safer in a terminal, so typed_breaks_for turns them into spaces
    # there; other apps get Shift+Enter.
    remainder = typed_breaks_for(remainder.replace("\n", LINE_BREAK),
                                 focused_window.window_class())
    if not keyboard_output.output_backend().type_text(remainder):
        # The chunks before the failure reached the document, and so did
        # the line break after the last of them unless that break is what
        # failed. Recording only those makes undo delete too little rather
        # than too much if part of the remainder was typed as well.
        rs.state.last_inserted_text = delivered.landed(parts)
        logger.warning(
            f"Injection stopped after {delivered.parts}/{len(parts)} chunks \u2014 "
            f"undo buffer holds only what landed"
        )
        return "partial" if delivered.parts else "failed"
    # Undo counts what's on screen: in a terminal the typed rest has spaces
    # where the dictation had line breaks, fewer of them than it had.
    rs.state.last_inserted_text = (delivered.landed(parts) + remainder).replace(LINE_BREAK, "\n")
    return "ok"


def _type_all(text, injection_start):
    """Type *text*: (the text as typed, whether it all went in)."""
    # Typed, a real newline is a plain Enter, which sends a chat message or
    # runs a terminal's half-typed command. A snippet's line breaks are real
    # newlines, so they go in as Shift+Enter like a spoken "new line".
    text = typed_breaks_for(text.replace("\n", LINE_BREAK),
                            focused_window.window_class())
    backend = keyboard_output.output_backend()
    # The Flatpak's keyboard path types "…" as "..." and "café" as "cafe";
    # recording that form below keeps undo's backspace count right.
    text = backend.typeable(text)
    inject_ok = backend.type_text(text)
    injection_time = time.time() - injection_start
    logger.info(f"Text injected via typing: {len(text)} chars in {injection_time:.2f}s")
    if injection_time > 1.0:
        logger.warning(f"Typing injection slow: {injection_time:.2f}s")
    return text, inject_ok
