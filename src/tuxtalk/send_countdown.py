"""Send each dictation automatically: Enter after a dictation, once a short
wait is over.

Sending can't be taken back, so the wait gives a moment to stop it, counting
down on screen ("Sending in 2 s"): a tap of the hold key (or the toggle key)
holds the send and the text stays in the box, to fix or add a screenshot to
first; holding the key dictates more onto it. The Enter goes only into the
window the text went to, and never into a password field.

The send waiting is app.state.pending_send, guarded by _send_lock; the
typing and the questions about the focus are app.py's.
"""
import threading
import time

from . import virtual_keyboard
from .logger import setup_logger

logger = setup_logger(__name__)

_send_lock = threading.Lock()
LABEL_TAG = "send-countdown"
HELD_NOTICE = "Not sent. Press Enter when you're ready."
SWITCHED_WINDOW_NOTICE = "Not sent: you switched windows."
PASSWORD_FIELD_NOT_SENT = "Not sent: the cursor is in a password field."

_delay = 2.0                # seconds before the Enter (Preferences)
_hold_key_name = "F8"       # named in the "Sending in 2 s" label


def configure(cfg) -> None:
    """Take the wait and the hold key's name from the settings *cfg*."""
    global _delay, _hold_key_name
    _delay = float(getattr(cfg, "press_enter_delay", 2.0))
    _hold_key_name = getattr(cfg, "hotkey", "") or "the hotkey"


def send_automatically(why: str, space: str = "", injection_mode: str = "type") -> None:
    """Press Enter for the dictation just typed, after the wait (hold).

    *space* is the auto-space left off so it wouldn't be sent; a held send
    types it, or what's said next sticks to the last word ("delay.All")."""
    from . import app
    delay = _delay
    if delay <= 0:
        app._press_key("ENTER", why)
        return
    pending = {"window": app.state.last_inserted_window, "why": why,
               "space": space, "mode": injection_mode}
    pending["timer"] = threading.Timer(delay, _send_when_due, args=(pending,))
    # The label counts down once a second: "Sending in 4 s", 3, 2, 1.
    pending["ticks"] = [threading.Timer(delay - left, _count_down, args=(pending, left))
                        for left in range(int(delay) - 1, 0, -1)]
    for timer in [pending["timer"], *pending["ticks"]]:
        timer.daemon = True
    with _send_lock:
        if app.state.pending_send is not None:
            _cancel_timers(app.state.pending_send)
        app.state.pending_send = pending
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
    from . import app, screen_message
    with _send_lock:
        if app.state.pending_send is not pending:
            return
    if _focus_left(pending):
        with _send_lock:
            if app.state.pending_send is not pending:
                return
            app.state.pending_send = None
        _cancel_timers(pending)
        logger.info("Not sent: another window has the focus now")
        screen_message.relabel(LABEL_TAG, SWITCHED_WINDOW_NOTICE)
        return
    screen_message.relabel(LABEL_TAG, _label(seconds_left))


def _focus_left(pending: dict) -> bool:
    """Whether another window than the one the text went to has the focus."""
    from . import app
    focused = app._query_focused_window_class()
    return (focused is not None and pending["window"] is not None
            and focused != pending["window"])


def _cancel_timers(pending: dict) -> None:
    for timer in [pending["timer"], *pending.get("ticks", ())]:
        timer.cancel()


def _send_when_due(pending: dict) -> None:
    """The wait is over and nothing held it: press Enter, but only in the
    window the text went to (_claim_send).

    A Right Ctrl or Right Alt dictation key down now, or going down before
    the Enter does, is waited for first (modifier_hotkeys), with the send
    pending until the Enter goes down: the hotkey loop reads a tap of it
    meanwhile, and the tap holds the send as it would have."""
    from . import app
    with _send_lock:
        if app.state.pending_send is not pending:
            return
    sent = app._press_key("ENTER", pending["why"], claim=lambda: _claim_send(pending))
    with _send_lock:
        unclaimed = app.state.pending_send is pending
        if unclaimed:
            app.state.pending_send = None
    if unclaimed and not sent:
        logger.info("Not sent: a dictation key stayed down, or Enter couldn't be pressed")
        show_notice(HELD_NOTICE)


def _claim_send(pending: dict):
    """The checks before a send's Enter, made with the send still pending,
    so a tap meanwhile still holds it: Enter in whatever the user switched
    to could send something else entirely. Returns what takes the send,
    right before the Enter goes down (virtual_keyboard.send's *claim*).
    Raises virtual_keyboard.GaveUp when it mustn't go."""
    from . import app
    if _focus_left(pending):
        _refuse_send(pending, "another window has the focus now", SWITCHED_WINDOW_NOTICE)
    # Tabbing from a username into the password box during the wait must
    # not submit the form: "press enter" said on its own is the only Enter a
    # password field gets.
    if app._focused_is_password():
        _refuse_send(pending, "a password field has the focus now", PASSWORD_FIELD_NOT_SENT)
    return lambda: _take_send(pending)


def _take_send(pending: dict) -> None:
    """Take *pending* for its Enter; GaveUp when a tap held it."""
    from . import app
    with _send_lock:
        if app.state.pending_send is not pending:
            raise virtual_keyboard.GaveUp("held")
        app.state.pending_send = None


def _refuse_send(pending: dict, why: str, notice: str) -> None:
    """End *pending* unsent and say why, unless a tap held it first."""
    from . import app
    with _send_lock:
        ours = app.state.pending_send is pending
        if ours:
            app.state.pending_send = None
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
    from . import app
    with _send_lock:
        pending, app.state.pending_send = app.state.pending_send, None
    if pending is None:
        return False
    _cancel_timers(pending)
    logger.info("Held the send: the dictation stays unsent")
    if pending.get("space"):
        app.state.owed_space = pending
    if announce:
        show_notice(HELD_NOTICE)
    else:
        from . import screen_message
        screen_message.hide(LABEL_TAG)
    return True


def _type_space_left_off(pending: dict) -> None:
    """Type the space a held send left off, in the window its text went to,
    keeping undo's record of the dictation whole."""
    from . import app
    space = pending.get("space")
    if not space:
        return
    if _focus_left(pending):
        return
    typed = app.state.last_inserted_text
    if app._inject_text(space, pending["mode"], time.time()) == "ok":
        app.state.last_inserted_text = typed + space


def type_owed_space() -> None:
    """The hotkey that held a send is up: type the space it left off."""
    from . import app
    pending, app.state.owed_space = app.state.owed_space, None
    if pending is not None:
        _type_space_left_off(pending)


def with_owed_space(text: str) -> str:
    """Put a held send's space in front of *text*, the dictation added onto
    it, when it goes to the same window."""
    from . import app
    pending, app.state.owed_space = app.state.owed_space, None
    if pending is None or not text or text[0].isspace() or _focus_left(pending):
        return text
    return pending["space"] + text
