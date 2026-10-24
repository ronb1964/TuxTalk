"""Send each dictation automatically: Enter after a dictation, once a short
wait is over.

Sending can't be taken back, so the wait gives a moment to stop it, counting
down on screen ("Sending in 2 s"): a tap of the hold key (or the toggle key)
holds the send and the text stays in the box, to fix or add a screenshot to
first; holding the key dictates more onto it. The Enter goes only into the
window the text went to, and never into a password field.

The send waiting is _pending, guarded by _send_lock. The typing is app.py's:
the dictation hands over what presses a key and what types more onto it.
"""
import threading

from . import focused_window, virtual_keyboard
from .logger import setup_logger

logger = setup_logger(__name__)

_send_lock = threading.Lock()
LABEL_TAG = "send-countdown"
HELD_NOTICE = "Not sent. Press Enter when you're ready."
SWITCHED_WINDOW_NOTICE = "Not sent: you switched windows."
PASSWORD_FIELD_NOT_SENT = "Not sent: the cursor is in a password field."

_delay = 2.0                # seconds before the Enter (Preferences)
_hold_key_name = "F8"       # named in the "Sending in 2 s" label

# The send counting down to its Enter, until it presses it or a tap of a
# hotkey holds it, else None: {"window", "why", "press", "type", "space",
# "mode", "timer", "ticks"}.
_pending = None
# The send a tap held, while the space it left off waits for the hotkey that
# held it to come up (type_owed_space), else None. Typed while the key is
# down, GNOME without the extension sees the key too and the space is lost.
_owed = None


def configure(cfg) -> None:
    """Take the wait and the hold key's name from the settings *cfg*."""
    global _delay, _hold_key_name
    _delay = float(getattr(cfg, "press_enter_delay", 2.0))
    _hold_key_name = getattr(cfg, "hotkey", "") or "the hotkey"


def send_automatically(why: str, window, press, type_more, space: str = "",
                       injection_mode: str = "type") -> None:
    """Press Enter for the dictation just typed, after the wait (hold), if
    the window it went to (*window*, a focused_window.look() taken then)
    still has the focus. *press*(key, why, claim=None) presses it, as
    app._press_key; *type_more*(text, mode) types onto the dictation.

    *space* is the auto-space left off so it wouldn't be sent; a held send
    types it, or what's said next sticks to the last word ("delay.All")."""
    global _pending
    pending = {"window": window, "why": why, "press": press, "type": type_more,
               "space": space, "mode": injection_mode}
    delay = _delay
    if delay <= 0:
        if _focus_left(pending):
            logger.info("Not sent: another window has the focus now")
            show_notice(SWITCHED_WINDOW_NOTICE)
        else:
            press("ENTER", why)
        return
    pending["timer"] = threading.Timer(delay, _send_when_due, args=(pending,))
    # The label counts down once a second: "Sending in 4 s", 3, 2, 1.
    pending["ticks"] = [threading.Timer(delay - left, _count_down, args=(pending, left))
                        for left in range(int(delay) - 1, 0, -1)]
    for timer in [pending["timer"], *pending["ticks"]]:
        timer.daemon = True
    with _send_lock:
        if _pending is not None:
            _cancel_timers(_pending)
        _pending = pending
    for timer in [pending["timer"], *pending["ticks"]]:
        timer.start()
    logger.info(f"Sending in {delay:g}s ({why})")
    from . import screen_message
    screen_message.show_over_active_window(
        _label(delay), seconds=delay, warning=False, tag=LABEL_TAG, low=True)


def _label(seconds_left: float) -> str:
    return f"Sending in {seconds_left:g} s. Tap {_hold_key_name} to hold it."


def _count_down(pending: dict, seconds_left: int) -> None:
    """One second less on the "Sending in" label, unless that send is over.
    Switching windows ends it here, so the label doesn't count down to a send
    that won't happen."""
    global _pending
    from . import screen_message
    with _send_lock:
        if _pending is not pending:
            return
    if _focus_left(pending):
        with _send_lock:
            if _pending is not pending:
                return
            _pending = None
        _cancel_timers(pending)
        logger.info("Not sent: another window has the focus now")
        screen_message.relabel(LABEL_TAG, SWITCHED_WINDOW_NOTICE)
        return
    screen_message.relabel(LABEL_TAG, _label(seconds_left))


def _focus_left(pending: dict) -> bool:
    """Whether another window than the one the text went to has the focus:
    another app, or another window of the same app where the desktop tells
    them apart (focused_window.py)."""
    return focused_window.changed(pending["window"], focused_window.look())


def _cancel_timers(pending: dict) -> None:
    for timer in [pending["timer"], *pending.get("ticks", ())]:
        timer.cancel()


def _send_when_due(pending: dict) -> None:
    """The wait is over and nothing held it: press Enter, but only in the
    window the text went to (_claim_send).

    A Right Ctrl or Right Alt dictation key down now, or going down before
    the Enter does, is waited for first (modifier_hotkeys), with the send
    pending until the Enter goes down: the hotkey loop reads a tap of it
    meanwhile, and the tap holds the send as it would have. A wait that
    ended in another window says so, as a switch during the countdown does."""
    global _pending
    with _send_lock:
        if _pending is not pending:
            return
    sent = pending["press"]("ENTER", pending["why"], claim=lambda: _claim_send(pending))
    with _send_lock:
        unclaimed = _pending is pending
        if unclaimed:
            _pending = None
    if unclaimed and not sent:
        if _focus_left(pending):
            logger.info("Not sent: another window has the focus now")
            show_notice(SWITCHED_WINDOW_NOTICE)
        else:
            logger.info("Not sent: a dictation key stayed down, or Enter couldn't be pressed")
            show_notice(HELD_NOTICE)


def _claim_send(pending: dict):
    """The checks before a send's Enter, made with the send still pending,
    so a tap meanwhile still holds it: Enter in whatever the user switched
    to could send something else entirely. Returns what takes the send,
    right before the Enter goes down (virtual_keyboard.send's *claim*).
    Raises virtual_keyboard.GaveUp when it mustn't go."""
    if _focus_left(pending):
        _refuse_send(pending, "another window has the focus now", SWITCHED_WINDOW_NOTICE)
    # Tabbing from a username into the password box during the wait must
    # not submit the form: "press enter" said on its own is the only Enter a
    # password field gets.
    if focused_window.is_password():
        _refuse_send(pending, "a password field has the focus now", PASSWORD_FIELD_NOT_SENT)
    return lambda: _take_send(pending)


def _take_send(pending: dict) -> None:
    """Take *pending* for its Enter; GaveUp when a tap held it."""
    global _pending
    with _send_lock:
        if _pending is not pending:
            raise virtual_keyboard.GaveUp("held")
        _pending = None


def _refuse_send(pending: dict, why: str, notice: str) -> None:
    """End *pending* unsent and say why, unless a tap held it first."""
    global _pending
    with _send_lock:
        ours = _pending is pending
        if ours:
            _pending = None
    if ours:
        logger.info(f"Not sent: {why}")
        show_notice(notice)
    raise virtual_keyboard.GaveUp("moved")


def show_notice(text: str) -> None:
    """Say over the window, where the countdown was, what became of the send."""
    from . import screen_message
    screen_message.show_over_active_window(text, warning=False, tag=LABEL_TAG, low=True)


def hold(announce: bool) -> bool:
    """Stop a counting-down send; True when there was one. *announce* says
    "Not sent" on screen at once (a tap of the toggle key); a press of the
    hold key waits to see whether it's a tap or more dictation."""
    global _pending, _owed
    with _send_lock:
        pending, _pending = _pending, None
    if pending is None:
        return False
    _cancel_timers(pending)
    logger.info("Held the send: the dictation stays unsent")
    if pending.get("space"):
        _owed = pending
    if announce:
        show_notice(HELD_NOTICE)
    else:
        from . import screen_message
        screen_message.hide(LABEL_TAG)
    return True


def owes_space() -> bool:
    """Whether a held send's space waits for its hotkey to come up."""
    return _owed is not None


def type_owed_space() -> None:
    """The hotkey that held a send is up: type the space it left off, in the
    window its text went to."""
    global _owed
    pending, _owed = _owed, None
    if pending is not None and not _focus_left(pending):
        pending["type"](pending["space"], pending["mode"])


def with_owed_space(text: str) -> str:
    """Put a held send's space in front of *text*, the dictation added onto
    it, when it goes to the same window."""
    global _owed
    pending, _owed = _owed, None
    if pending is None or not text or text[0].isspace() or _focus_left(pending):
        return text
    return pending["space"] + text
