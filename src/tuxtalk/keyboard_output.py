"""Typing and pasting into the focused window, outside the Flatpak: keys
through TuxTalk's own virtual keyboard (virtual_keyboard.py) or, where it
has none, wtype; text through the clipboard and the paste shortcut the
window takes. output_backends.VirtualKeyboardOutputBackend is how the rest
of the service reaches this. A dictation's keys never go into a password
field (refuse_password_field)."""
import contextlib
import functools
import os
import shutil
import subprocess
import tempfile
import threading
import time
from typing import NamedTuple

from evdev import ecodes

from . import delivery_notices, focused_window, modifier_hotkeys, virtual_keyboard
from . import live_settings as live
from . import notify as notify_module
from . import recording_state as rs
from .key_commands import KEY_CODES
from .logger import setup_logger
from .normalize import LINE_BREAK
from .window_classes import SMART_NEWLINE_CLASSES_LOWER, is_terminal_class

logger = setup_logger(__name__)

# clipboard.ClipboardKeeper, made by the first paste that needs it (_keeper).
_clipboard_keeper = None

# Cached tool path lookups — avoids searching PATH on every text injection
_tool_cache = {}


def which(name):
    """Cached shutil.which() — tools don't move during a session."""
    if name not in _tool_cache:
        _tool_cache[name] = shutil.which(name)
    return _tool_cache[name]


# Milliseconds after each typed key event (config key_delay, set in main)
key_delay_ms = virtual_keyboard.KEY_DELAY_MS


def is_wayland_session() -> bool:
    """True when a Wayland compositor is available for wl-copy to talk to.

    Not desktop_detect.is_x11_session's question, which is about the session:
    this asks only whether wl-copy has a compositor to reach, which a
    compositor nested in an X11 session gives it too."""
    return bool(os.environ.get("WAYLAND_DISPLAY"))


# The wtype fallback types a whole utterance, so it needs far longer than a
# single keystroke. wtype has no per-key delay, so a fixed ceiling is enough.
WTYPE_TIMEOUT_S = 20.0

# How long wl-copy may take to hand the dictation to the compositor. It is
# usually a few milliseconds; a slower one is stuck, and nothing is pasted.
WL_COPY_READY_TIMEOUT_S = 1.0

# Shortcuts and single keys (Ctrl+V, Enter, Delete) keep ydotool's pace from
# before TuxTalk had its own keyboard: they're four key events at most, so
# speed doesn't show, and every app TuxTalk knows has taken them like this.
SHORTCUT_DELAY_MS = 12


def key_delay() -> int:
    """The wait after each key of typed text, backspaces and moves."""
    return max(1, min(50, key_delay_ms))


def send_keys(events, what: str = "keystroke", delay_ms: float = SHORTCUT_DELAY_MS,
              raise_if_partial: bool = False, claim=None, right_before=None) -> bool:
    """Press keys [(evdev code, 1 down / 0 up)] on TuxTalk's virtual keyboard.
    True only if all of them were sent: keystrokes that never happened must
    not reach the undo buffer.

    Each key waits while a Right Ctrl or Right Alt dictation key is down and
    not yet hidden from the desktop (modifier_hotkeys): pressed under it,
    letters are shortcuts (Ctrl+W closes the tab). Typing on the dictation
    worker can get to a key before the hotkey loop has read the press and
    hidden it.

    With *raise_if_partial*, a keyboard that failed partway raises
    virtual_keyboard.StoppedPartway instead of returning False, for a caller
    that must tell "none of the keys were sent" from "some of them were";
    and a wait that gave up raises virtual_keyboard.GaveUp: the text must
    not go out another way (wtype, the clipboard) either. *claim* is
    virtual_keyboard.send's; in a delivery that may only go into one window
    (delivery.inject_text's *into*), the claim checks it's still that one.
    *right_before* runs with the keyboard held right before the first key
    goes down, after every check and wait.
    """
    claim = _delivery_claim(claim)
    if this_thread.last_check is not None:
        claim = _then(claim, this_thread.last_check)
    if right_before is not None:
        claim = _last(claim, right_before)
    before_press = modifier_hotkeys.wait_while_held(focused_window.destination)
    try:
        sent = virtual_keyboard.send(events, delay_ms, before_press, claim)
    except virtual_keyboard.StoppedPartway as stop:
        if stop.reason is None:     # a wait that gave up said why already
            logger.error(f"{what}: the virtual keyboard stopped partway")
        if raise_if_partial:
            raise
        return False
    except virtual_keyboard.GaveUp:
        if raise_if_partial:
            raise
        return False        # nothing sent: the key stayed down (logged there)
    if not sent:
        logger.error(f"Couldn't send {what}: TuxTalk's virtual keyboard isn't working")
    else:
        this_thread.sends += 1
    return sent


def _delivery_claim(claim):
    """*claim*, else (None) the claim of the delivery on this thread
    (_ThisThread): only into its window, and never into a password field,
    asked first, as focused_window.destination does. Both are asked in the
    claim, with the keyboard free (the accessibility bus has a quarter of a
    second to answer), after the clipboard and the layout are ready, and
    for every send: each line, line break and paste key of a dictation."""
    if claim is None and this_thread.into is not None:
        claim = functools.partial(focused_window.claim_into, this_thread.into)
    if not this_thread.no_password_field:
        return claim

    def claimed():
        refuse_password_field()
        return claim() if claim is not None else lambda: None
    return claimed


def refuse_password_field() -> None:
    """GaveUp("password_field") when the cursor is in a password field now:
    a dictation's next line, its line break or its paste key goes nowhere.
    A line break there could submit the form."""
    from . import atspi_helper
    if focused_window.is_password(budget=atspi_helper.RECHECK_BUDGET_S):
        logger.warning("Stopped typing: a password field has the focus now")
        raise virtual_keyboard.GaveUp("password_field")


def checks_before_keys():
    """The delivery's checks on this thread (_delivery_claim) as one call
    that raises GaveUp, for keys that go out another way than send_keys:
    the Flatpak's portal (output_backends._with_session). None when there
    are none."""
    claim = _delivery_claim(None)
    return None if claim is None else lambda: claim()()


def _then(claim, check):
    """*claim* (None for none), then *check*, which raises GaveUp to stop the
    keys: asked in the claim's take, with the keyboard held, right before
    the first key. Not before the claim, whose checks (which window has the
    focus) take a while: a key pressed meanwhile would be missed."""
    def claimed():
        take = claim() if claim is not None else None

        def checked_take():
            check()
            if take is not None:
                take()
        return checked_take
    return claimed


def _last(claim, fn):
    """*claim* (None for none), then *fn* once the claim's take has let the
    keys go ahead, right before the first key: never for keys it stopped."""
    def claimed():
        take = claim() if claim is not None else None

        def take_then_fn():
            if take is not None:
                take()
            fn()
        return take_then_fn
    return claimed


class _ThisThread(threading.local):
    """What send_keys knows of the delivery on this thread (delivery.inject_text):
    how many sends went out whole, so a wait that gives up after earlier
    lines were typed stopped it partway, not before it began; the only
    window it may go into, else None; whether it's a dictation, which never
    goes into a password field; whether its pastes keep the user's clipboard
    (_keeping); and a last check its keys must pass right
    before the first goes down, else None (a held send's space on its own
    may not go out with Alt or a dictation key down:
    send_countdown.give_back_space)."""
    sends = 0
    into = None
    no_password_field = False
    keep_clipboard = False
    last_check = None


this_thread = _ThisThread()


@contextlib.contextmanager
def _on_this_thread(**values):
    """this_thread's *values* while inside, then back as they were."""
    before = {name: getattr(this_thread, name) for name in values}
    for name, value in values.items():
        setattr(this_thread, name, value)
    try:
        yield
    finally:
        for name, value in before.items():
            setattr(this_thread, name, value)


def only_into(window):
    """Keys sent on this thread inside go only into *window*, a look taken
    when what they do was decided (this_thread.into): checked right before
    the first goes down, after every wait, on the virtual keyboard and
    through the Flatpak's portal alike, whose handshake can take minutes.
    Another window by then: virtual_keyboard.GaveUp("moved"), nothing sent."""
    return _on_this_thread(into=window)


def delivering(into):
    """A dictation's delivery on this thread: none of its keys go into a
    password field (refuse_password_field), and with *into*, a look, only
    into that window (only_into)."""
    return _on_this_thread(into=into, no_password_field=True)


def not_where_typed(focused, now=None) -> bool:
    """Whether the focused window, of class *focused*, is known to be another
    one than the last dictation or phrase went to, for undo, select and move,
    "edit that", "fix that word" and the lowercase carry-on. By the desktop's
    window id where it gives one, so two KWrite documents aren't taken for
    one, else by the class (focused_window.same_window). *now* is a look
    already taken, else one is taken here."""
    if focused_window.same_window((focused, None),
                                  (rs.state.last_inserted_window, None)) is False:
        return True
    now = focused_window.look() if now is None else now
    return focused_window.same_window(rs.state.last_inserted_where, now) is False


def press_key(key: str, why: str, claim=None) -> bool:
    """A real "ENTER" or "TAB" after a dictation (key_commands.py): Enter
    sends a chat message, runs a command or submits a form; Tab moves on to
    a form's next box. Undo can't reach what's behind either; the caller
    forgets the dictation once it's pressed (delivery.press_key). Not
    pressed, a key stuck down says so (an automatic send says it its own way).
    *claim* can still stop it right before it goes down (send_countdown)."""
    time.sleep(0.05)   # let the app take in the typed text first
    if not output_backend().press_keys([((), key)], claim=claim):
        logger.warning(f"{key.title()} not pressed ({why})")
        if claim is None and modifier_hotkeys.stuck():
            notify_module.notify(*delivery_notices.key_not_pressed(key))
        return False
    logger.info(f"Pressed {key.title()} ({why})")
    return True


def send_shift_enter():
    """Send Shift+Enter keystrokes to create line break without submitting."""
    return send_keys([(ecodes.KEY_LEFTSHIFT, 1), (ecodes.KEY_ENTER, 1), (ecodes.KEY_ENTER, 0),
                       (ecodes.KEY_LEFTSHIFT, 0)], what="shift+enter", raise_if_partial=True)


def ctrl_keys(letter: str, shift: bool = False) -> list:
    """Ctrl+*letter* (Ctrl+Shift with *shift*), on the key that is that letter
    on the user's keyboard layout. On AZERTY the US A key is Q, so Ctrl+A
    sent by position quit the app; on Dvorak the US V key is K."""
    from .keyboard_layout import shortcut_keycode
    key = shortcut_keycode(letter)
    held = [42, 29] if shift else [29]          # KEY_LEFTSHIFT, KEY_LEFTCTRL
    return ([(k, 1) for k in held] + [(key, 1), (key, 0)]
            + [(k, 0) for k in reversed(held)])


def send_select_all_delete():
    """Ctrl+A, then Backspace: "delete everything" clears the whole box,
    whatever put the text there."""
    if not send_keys(ctrl_keys("a"), what="select-all"):
        return False
    time.sleep(0.05)
    return send_keys([(14, 1), (14, 0)], what="delete")    # KEY_BACKSPACE


_OUTPUT_BACKEND = None


def output_backend():
    """Process-wide OutputBackend, chosen once by FLATPAK_ID: TuxTalk's own
    virtual keyboard (type_text below), or libei inside a Flatpak."""
    global _OUTPUT_BACKEND
    if _OUTPUT_BACKEND is None:
        from .output_backends import get_output_backend
        _OUTPUT_BACKEND = get_output_backend()
    return _OUTPUT_BACKEND


class Delivered(NamedTuple):
    """How far deliver_lines got."""
    parts: int      # parts confirmed in the app, from the first
    breaks: int     # line breaks confirmed after them
    stopped: str    # "" when all of it went in, else "part" or "break"

    def went_in(self, parts) -> bool:
        """Whether any of *parts* or a line break between them reached the
        app: an empty first part ("new line" said first) counts as done."""
        return bool(self.breaks or any(parts[:self.parts]))

    def landed(self, parts) -> str:
        """What of *parts* reached the app, its line breaks as "\n" (one
        Shift+Enter, one character for undo to backspace)."""
        text = "\n".join(parts[:self.parts])
        return text + "\n" if self.parts and self.breaks == self.parts else text


def deliver_lines(parts, put, before_break_s: float, after_break_s: float,
                  send_break=None) -> Delivered:
    """Deliver *parts* one by one with put(part), a Shift+Enter between each
    two (or send_break()), waiting *before_break_s* and *after_break_s*
    around each break.

    Stops at the first part or line break that fails: going on after a gap
    would run the later lines into the earlier ones. In a dictation, a
    password field stops it right before the part or line break that would
    go into it (refuse_password_field)."""
    done = breaks = 0
    for i, part in enumerate(parts):
        if part and not put(part):
            return Delivered(done, breaks, "part")
        done = i + 1
        if i < len(parts) - 1:
            time.sleep(before_break_s)
            if not (send_break or send_shift_enter)():
                return Delivered(done, breaks, "break")
            breaks += 1
            time.sleep(after_break_s)
    return Delivered(done, breaks, "")


def type_text(text: str) -> bool:
    """Type text, its line-break markers as Shift+Enter, or in KDE's editors
    as a pasted newline (_paste_newline). Returns True if it was actually
    typed into the focused app, False if none of it was (deliver_lines).
    Raises virtual_keyboard.StoppedPartway when a later line or a line
    break failed after earlier lines went in: told "Couldn't type the
    dictation", the user would paste it all again over them."""
    in_kde_editor = (focused_window.window_class() or "").lower() in SMART_NEWLINE_CLASSES_LOWER
    lines = text.split(LINE_BREAK)
    # The line breaks there are pastes, and so is a line holding an emoji:
    # the user's clipboard is kept from the first and comes back after the
    # last, whatever the "Restore clipboard" setting. That's off so a pasted
    # dictation can be pasted again, and these pastes are only parts of one.
    with _on_this_thread(keep_clipboard=in_kde_editor and len(lines) > 1):
        delivered = deliver_lines(lines, type_text_raw, 0.05, 0.05,
                                  send_break=_paste_newline if in_kde_editor else None)
    if delivered.stopped and delivered.went_in(lines):
        raise virtual_keyboard.StoppedPartway(
            f"{delivered.parts} of {len(lines)} lines",
            "a line break failed" if delivered.stopped == "break" else "a line failed")
    return not delivered.stopped


def _paste_newline() -> bool:
    """A line break in KDE's editors, where Shift+Enter is Insert Smart
    Newline: "$45", new line, "$25" came out as "$$25". A plain Enter won't
    do either, since in Kate's terminal panel it runs the line; a pasted
    newline is a plain one in both, as the paste path delivers it."""
    return paste_text("\n") if is_wayland_session() else paste_text_x11("\n")


def type_text_raw(text: str) -> bool:
    """Type *text* on TuxTalk's virtual keyboard, with the keys of the active
    layout (keyboard_layout). True only if it was typed.

    Text the layout has no keys for (an emoji, Latin text on a Ukrainian
    layout) is pasted instead, which works on any layout. Without a virtual
    keyboard (no access to /dev/uinput), wtype, where the desktop has one.
    Raises virtual_keyboard.StoppedPartway when the keyboard failed partway:
    some of the text is in the app, and nothing may type it again; and
    virtual_keyboard.GaveUp when typing stopped for a dictation key held
    down (send_keys)."""
    from . import keyboard_layout
    events = None
    try:
        events = keyboard_layout.layout_key_events(text)
    except keyboard_layout.UntypableText as e:
        layout, blocker = e.args
        logger.info(f"The {layout} keyboard layout can't type {live.loggable(blocker)}; "
                    "pasting instead")
        if is_wayland_session():
            return paste_text(text)
        return paste_text_x11(text)
    except (OSError, AttributeError) as e:
        # No libxkbcommon, or one older than 1.0 without the calls it needs.
        logger.error(f"Couldn't load libxkbcommon to work out the keys: {e}")
    if events is not None:
        started = time.time()
        delay = key_delay()
        if send_keys(events, what=f"{len(text)} chars", delay_ms=delay, raise_if_partial=True):
            logger.info(f"Typed {len(text)} chars in {time.time() - started:.2f}s "
                        f"({delay} ms a key event)")
            return True
    if which("wtype"):
        if this_thread.no_password_field:
            refuse_password_field()     # no claim of send_keys's asks it here
        # The real exit code, so the undo buffer tracks only typed text; a
        # bounded wait, so a stalled compositor can't freeze dictation; the
        # text on stdin ("-"), as every user can read a command line. wtype
        # reads stdin in its locale's encoding, and under a C locale stops at
        # the first letter outside ASCII, dropping the rest without an error.
        proc = subprocess.Popen(["wtype", "-"], stdin=subprocess.PIPE,
                                env=dict(os.environ, LC_ALL="C.UTF-8"))
        try:
            proc.communicate(text.encode("utf-8"), timeout=WTYPE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            # Kill and reap, so it cannot come back to life and type into
            # whatever the user has focused by then.
            proc.kill()
            proc.communicate()
            logger.error(f"wtype timed out after {WTYPE_TIMEOUT_S}s; killed the process")
            return False
        return proc.returncode == 0
    # Not put on the clipboard instead: that would replace what the user
    # copied, unmarked for clipboard managers to keep. The failure notice
    # points to Recent Dictations, which holds it.
    logger.error("Could not type text: no virtual keyboard or wtype")
    return False


def send_paste_keystroke(focused_class: str | None, primary_holds_text: bool = False,
                         right_before=None) -> bool:
    """Send the paste shortcut for *focused_class*. True if it was sent.

    A terminal gets Ctrl+Shift+V (Ctrl+V is readline's quoted-insert there and
    shows nothing) and other known apps Ctrl+V. An unknown window gets
    Shift+Insert when the primary selection holds the text too: GUI toolkits
    paste the clipboard on Shift+Insert and terminals paste the primary
    selection, so it lands either way. Without that, Ctrl+V, which suits most
    apps. *right_before* as for send_keys."""
    if is_terminal_class(focused_class):
        keys = ctrl_keys("v", shift=True)
        logger.info(f"Paste: Ctrl+Shift+V (terminal class={focused_class!r})")
    elif focused_class is None and primary_holds_text:
        keys = [(42, 1), (110, 1), (110, 0), (42, 0)]  # Shift+Insert (KEY_INSERT=110)
        logger.info("Paste: Shift+Insert (window unknown; clipboard and primary selection set)")
    else:
        keys = ctrl_keys("v")
        logger.info(f"Paste: Ctrl+V (class={focused_class!r})")
    if not send_keys(keys, what="paste", raise_if_partial=True, right_before=right_before):
        # The keystroke never reached the desktop: the text is sitting on the
        # clipboard but never made it into the app.
        logger.error("Paste keystroke failed — text was not inserted")
        return False
    return True


def _keeping() -> bool:
    """Whether a paste keeps the user's clipboard to put back: with
    "Restore clipboard" on, and in a dictation typed into KDE's editors
    (type_text)."""
    return live.restore_clipboard or this_thread.keep_clipboard


def _keeper(board):
    """The clipboard.ClipboardKeeper, made by the first paste that keeps the
    user's clipboard, on *board*: a session pastes on one kind for its life."""
    from . import clipboard
    global _clipboard_keeper
    if _clipboard_keeper is None:
        _clipboard_keeper = clipboard.ClipboardKeeper(board)
    return _clipboard_keeper


def restore_clipboard_now() -> None:
    """Before the service exits: put the user's clipboard back now if a
    restore is still pending (clipboard.ClipboardKeeper.restore_now)."""
    if _clipboard_keeper is not None:
        _clipboard_keeper.restore_now()


def paste_text_x11(text: str) -> bool:
    """Paste on an X11 session, for text the keyboard layout can't type.

    paste_text needs wl-copy, which only talks to Wayland. Here the service
    puts the text on the X11 clipboard through its own GTK loop and then sends
    the same paste keystroke. It's only the fallback for untypable text
    (Latin words on a Ukrainian layout, an emoji); ordinary X11 dictation is
    still typed. The user's clipboard comes back afterwards as in
    paste_text (_keeping). True only if the text reached the app.
    """
    from . import x11_clipboard
    keeper = _keeper(x11_clipboard.X11Board) if _keeping() else None
    if keeper is not None:
        keeper.before_paste()
    if not x11_clipboard.offer_dictation(text):
        # The clipboard never changed: only an earlier dictation's restore,
        # called off by before_paste(), is left to do.
        logger.error("X11 paste: couldn't put the text on the clipboard; nothing was inserted")
        if keeper is not None:
            keeper.paste_failed()
        return False
    try:
        time.sleep(0.08)    # let the selection settle before the app asks for it
        return send_paste_keystroke(focused_window.window_class(),
                                    right_before=x11_clipboard.reads_from_now)
    finally:
        if keeper is not None:
            keeper.after_paste(text)


def _wl_copy_dictation(text) -> bool:
    """Put *text* on the clipboard with wl-copy and wait until it holds it:
    False when it doesn't, so nothing is pasted. --sensitive keeps the
    dictation out of clipboard managers' history (Klipper saves every entry
    to disk) where wl-copy supports it.

    Its stderr goes to a file, not a pipe: the copy wl-copy leaves serving
    the clipboard keeps stderr open, and reading a pipe to its end would
    wait for that copy to quit."""
    from . import clipboard
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(clipboard.wl_copy_command(), stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=errors)
        try:
            return _hand_to_wl_copy(proc, text, errors)
        finally:
            # Only a wl-copy that never finished handing off is still ours to
            # stop, and reaped: let go, it would set the clipboard after the
            # paste was given up. The background copy serving it is not ours.
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def _hand_to_wl_copy(proc, text, errors):
    """Give *text* to the wl-copy process *proc*, whose stderr is the file
    *errors*, and wait until the clipboard holds it: False when it doesn't."""
    # wl-copy forks a background copy to serve the clipboard once the
    # compositor has accepted the selection, and the process we started then
    # exits 0. Until it exits the clipboard may still hold the old contents,
    # and pasting would insert those. The time limit covers the writing too:
    # a wl-copy that stopped reading would hold a long dictation's write.
    try:
        proc.communicate(text.encode("utf-8"), timeout=WL_COPY_READY_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        logger.error(f"wl-copy did not set the clipboard within "
                     f"{WL_COPY_READY_TIMEOUT_S}s — skipping paste")
        return False
    if proc.returncode != 0:
        errors.seek(0)
        err = errors.read().decode(errors="replace").strip()
        logger.error(f"wl-copy failed (exit {proc.returncode}): {err} — "
                     "clipboard not set, skipping paste")
        return False
    return True


def _send_paste(text, keeping):
    """With the clipboard set, the paste key. Where the window is unknown the
    primary selection gets the text too, so the key works in a terminal as
    well. *keeping*: the user's clipboard is being kept to put back."""
    from . import clipboard
    focused_class = focused_window.window_class()
    primary_holds_text = False
    if focused_class is None:
        if keeping:
            _clipboard_keeper.before_primary()
        primary_holds_text = clipboard.copy_to_primary(text, WL_COPY_READY_TIMEOUT_S)
    keystroke_start = time.time()
    if not send_paste_keystroke(focused_class, primary_holds_text):
        return False
    logger.info(f"TIMING: paste keystroke took {time.time() - keystroke_start:.3f}s")
    time.sleep(0.05)        # for the paste to register
    return True


def paste_text(text: str) -> bool:
    """Wayland paste: put *text* on the clipboard with wl-copy, then send the
    paste keystroke (send_paste_keystroke picks Ctrl+V, Ctrl+Shift+V or
    Shift+Insert), and put the user's clipboard back afterwards where it's
    kept (_keeping).

    Returns True only when the text actually reached the focused app: the undo
    buffer records it, and a paste over an unset clipboard would insert
    whatever the user copied earlier. *text* must not contain §SHIFT_ENTER§.
    """
    # wl-copy talks to a Wayland compositor. On an X11 login it fails instantly,
    # and firing Ctrl+V anyway pastes the previous clipboard contents.
    if not is_wayland_session():
        logger.info("Paste unavailable: not a Wayland session — falling back to typing")
        return False

    try:
        if which("wl-copy"):
            # Copy text to clipboard
            paste_start = time.time()
            logger.info(f"TIMING: Starting paste operation for {len(text)} chars")
            # Optionally keep what the user had copied, to put back afterwards
            # (issue #7). Off by default: a dictation left on the clipboard can
            # be re-pasted if it landed in the wrong window.
            from . import clipboard
            keeping = _keeping()
            if keeping:
                _keeper(clipboard.WaylandBoard).before_paste()
            handed_off = False
            try:
                if not _wl_copy_dictation(text):
                    return False
                handed_off = True
                wl_copy_time = time.time() - paste_start
                logger.info(f"TIMING: wl-copy set the clipboard in {wl_copy_time:.3f}s")
                if not _send_paste(text, keeping):
                    return False
                total_paste_time = time.time() - paste_start
                logger.info(f"TIMING: Total paste operation time: {total_paste_time:.3f}s")
                return True
            finally:
                # Restore even after a failed paste, so the user's clipboard
                # isn't lost either way. A clipboard never set may still hold
                # an earlier paste, whose restore before_paste() called off.
                if keeping and handed_off:
                    _clipboard_keeper.after_paste(text)
                elif keeping:
                    _clipboard_keeper.paste_failed()
    except (virtual_keyboard.StoppedPartway, virtual_keyboard.GaveUp):
        raise       # the paste may have happened, or must not: nothing may deliver it now
    except subprocess.TimeoutExpired as e:
        logger.error(f"Paste injection timeout: {e}")
    except Exception as e:
        logger.error(f"Paste injection failed: {e}")
    return False


def send_backspaces(count: int) -> bool:
    """Send *count* backspace keypresses. True only if they were delivered.

    The caller updates the undo buffer from this result, so a wrong answer here
    desynchronises the buffer from the document and the next undo eats the
    user's own writing. Raises virtual_keyboard.StoppedPartway when the
    keyboard failed after some of the backspaces had already landed.
    """
    if count <= 0:
        return True
    # KEY_BACKSPACE = 14, pressed and released once per character.
    try:
        return send_keys([(14, 1), (14, 0)] * count, what=f"{count} backspaces",
                         delay_ms=key_delay(), raise_if_partial=True)
    except virtual_keyboard.GaveUp:
        return False        # none sent, and there's no other way to send them


def nav_keycode(name: str) -> int:
    """The key code for a key name of send_chords: a letter (Ctrl+A) is
    looked up on the active layout, as Ctrl shortcuts are matched by letter."""
    if name in KEY_CODES:
        return KEY_CODES[name]
    from .keyboard_layout import shortcut_keycode
    return shortcut_keycode(name.lower())


def send_chords(chords, claim=None) -> bool:
    """Press [(modifiers, key)] in order. Consecutive keys with the same
    modifiers share one press of them (Shift held once across forty Lefts,
    not pressed forty times), which halves the events and never leaves a
    modifier flapping. True only if they were delivered. *claim* is
    virtual_keyboard.send's."""
    events = []
    held = ()
    for mods, key in list(chords) + [((), None)]:
        if tuple(mods) != held:
            events += [(nav_keycode(m), 0) for m in reversed(held)]
            events += [(nav_keycode(m), 1) for m in mods]
            held = tuple(mods)
        if key is not None:
            code = nav_keycode(key)
            events += [(code, 1), (code, 0)]
    return send_keys(events, what=f"{len(chords)} navigation keys", delay_ms=key_delay(),
                     claim=claim)


def send_copy_keystroke(claim=None) -> bool:
    """Ctrl+C. Terminals never get here (voice_edit.transcribe_and_edit turns them away:
    Ctrl+C would stop the running program). True if it was sent. *claim* is
    virtual_keyboard.send's."""
    return send_keys(ctrl_keys("c"), what="copy", claim=claim)


_FORMAT_LETTERS = {"bold": "b", "italic": "i", "underline": "u"}


def send_format_shortcut(kind: str, claim=None) -> bool:
    """Ctrl+B, Ctrl+I or Ctrl+U, on the key that is that letter on the user's
    keyboard layout. Sent only to word processors (voice_edit_rules).
    *claim* is virtual_keyboard.send's."""
    return send_keys(ctrl_keys(_FORMAT_LETTERS[kind]), what=kind, claim=claim)
