"""Send each dictation automatically: Enter after a dictation, once a short
wait is over.

Sending can't be taken back, so the wait gives a moment to stop it, counting
down on screen ("Sending in 2 s"): a tap of the hold key (or the toggle key)
holds the send and the text stays in the box, to fix or add a screenshot to
first; holding the key dictates more onto it.

Where the Enter may go is one rule, kept here: only where the words went.
In another window it would send whatever is in that box, or run the command
on a terminal's line; in a password field it submits the form, and in one
of TuxTalk's own windows it presses the default button.

  1. Where the words go is noted before they're delivered and looked at
     again once they're in (landed). A window that changed meanwhile means
     they may be in two places, and no send is set up.
  2. Right before the Enter goes down, after every wait (the countdown, a
     Right Ctrl or Right Alt dictation key held, the keyboard taken back
     from other typing, the Flatpak's portal getting ready), one claim
     (_claim) makes every check again: the send wasn't held, the same
     window has the focus before the password check and after it, the
     cursor isn't in a password field, and no other key went out on
     TuxTalk's keyboard while the checks were made. In the Flatpak, keys go
     through the desktop's portal (output_backends.LibeiOutputBackend),
     which this last check can't count.

Anything else, nothing is sent and the notice says why (_why_not). What
happens where the desktop can't say which window of an app has the focus is
a decision of its own: SEND_WHEN_ONLY_THE_APP_IS_KNOWN.

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
UNKNOWN_WINDOW_NOTICE = "Not sent: this desktop doesn't say which window has the focus."
OWN_WINDOW_NOTICE = "Not sent: a TuxTalk window has the focus."

# Why an Enter wasn't sent: (what the log says after "Not sent: ", the notice).
_NOT_SENT = {
    "switched": ("another window has the focus now", SWITCHED_WINDOW_NOTICE),
    "moved": ("the focus moved while the words were delivered", SWITCHED_WINDOW_NOTICE),
    "password": ("a password field has the focus now", PASSWORD_FIELD_NOT_SENT),
    "own_window": ("a TuxTalk window has the focus", OWN_WINDOW_NOTICE),
    "typed_meanwhile": ("other keys went out while it was checked", HELD_NOTICE),
    "unknown_window": ("which window has the focus isn't known", UNKNOWN_WINDOW_NOTICE),
    "not_pressed": ("a dictation key stayed down, or the key couldn't be pressed", HELD_NOTICE),
}

# The open decision: where the desktop doesn't say which window has the
# focus, whether an automatic Enter goes when nothing says it's another app.
# The name says the common case; True also sends where not even the app is
# known (see _when_the_window_is_unknown).
SEND_WHEN_ONLY_THE_APP_IS_KNOWN = True

_delay = 2.0                # seconds before the Enter (Preferences)
_hold_key_name = "F8"       # named in the "Sending in 2 s" label

# The send counting down to its Enter, until it presses it or a tap of a
# hotkey holds it, else None: {"to", "why", "press", "type", "space",
# "mode", "timer", "ticks", "refused"}.
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


# --- Where the Enter may go ---------------------------------------------------

def landed(before, after):
    """Where an automatic Enter may go after words were delivered: *before*
    (a focused_window.destination() taken before them), or None when
    *after* (a look once they're in) is known to be another window."""
    return None if focused_window.changed(before, after) else before


def _when_the_window_is_unknown() -> str | None:
    """None to send, or why not, where the desktop can't say which window
    has the focus: COSMIC tells only the app, and so does GNOME with the
    extension from 0.18 or earlier, still loaded until the user logs out and
    in after an update; a desktop that says nothing about the focus (GNOME
    without the extension), or a tray too busy to answer, leaves even the
    app unknown. A key said after the words ("... press enter") follows the
    same choice (press_where_sent).

    Two choices, not made yet; until they are, it sends as it always has:
      SEND_WHEN_ONLY_THE_APP_IS_KNOWN True: send when nothing says it's
        another app, also where not even the app is known. Another window
        of the same app can get the Enter, and where nothing is known, any
        window.
      False: never send there; the words stay, and the notice says to press
        Enter. On COSMIC, and where the desktop says nothing about the
        focus, nothing is ever sent automatically."""
    return None if SEND_WHEN_ONLY_THE_APP_IS_KNOWN else "unknown_window"


def _why_not(sent_to, now) -> str | None:
    """Why an automatic Enter mustn't go into *now* (a destination) for
    words delivered to *sent_to* (landed()'s answer), or None when it may.
    A key of _NOT_SENT."""
    if sent_to is None:
        return "moved"
    if now.password:
        return "password"
    # Preferences, or an offer that opened by itself: Enter would press its
    # default button.
    if focused_window.is_own(now):
        return "own_window"
    same = focused_window.same_window(sent_to, now)
    if same is False:
        return "switched"
    return _when_the_window_is_unknown() if same is None else None


def _claim(pending, counting_down: bool = True):
    """virtual_keyboard.send's *claim* for the Enter of *pending*: the
    checks that take a while, made with the keyboard free and the send still
    pending, so a tap meanwhile still holds it. Returns *take*, which runs
    with the keyboard held, right before the Enter goes down. Either raises
    virtual_keyboard.GaveUp when the Enter mustn't go. A key said after the
    words (press_where_sent) isn't *counting_down*: no tap holds it."""
    start = virtual_keyboard.emitted()
    first = focused_window.look()
    now = focused_window.destination()
    # A click during the password check: its answer may be the other window's.
    why = "switched" if focused_window.changed(first, now) else _why_not(pending["to"], now)
    if why:
        _refuse(pending, why)

    def take():
        global _pending
        with _send_lock:
            if counting_down and _pending is not pending:
                raise virtual_keyboard.GaveUp("held")
            if virtual_keyboard.emitted() != start:
                _refuse(pending, "typed_meanwhile")
            if counting_down:
                _pending = None
    return take


def _refuse(pending, why: str) -> None:
    """Stop the Enter of *pending*; it's said why once the press is over
    (_send), unless a tap held it first."""
    pending["refused"] = why
    raise virtual_keyboard.GaveUp("moved")


def _say_not_sent(why: str, relabel: bool = False, key: str = "ENTER") -> None:
    text, notice = _NOT_SENT[why]
    what = "Not sent" if key == "ENTER" else f"{key.title()} not pressed"
    if key != "ENTER":
        notice = f"{what}: {text}."
    logger.info(f"{what}: {text}")
    if relabel:
        from . import screen_message
        screen_message.relabel(LABEL_TAG, notice)
    else:
        show_notice(notice)


def press_where_sent(key: str, why: str, sent_to, press) -> bool:
    """Press *key*, said after words delivered to *sent_to* (landed()'s
    answer): "... press enter" sends as an automatic Enter does, and "...
    press tab" leaves the box, so either goes only where the words went, by
    the same claim, with no wait to hold it. *press* is as for
    send_automatically. Not pressed, the notice says why."""
    pending = {"to": sent_to}
    if press(key, why, claim=lambda: _claim(pending, counting_down=False)):
        return True
    _say_not_sent(pending.get("refused") or "not_pressed", key=key)
    return False


# --- The countdown ------------------------------------------------------------

def send_automatically(why: str, sent_to, press, type_more, space: str = "",
                       injection_mode: str = "type") -> None:
    """Press Enter for the dictation just delivered to *sent_to* (landed()'s
    answer), after the wait (hold), through the one claim. *press*(key,
    why, claim=None) presses it, as app._press_key; *type_more*(text, mode)
    types onto the dictation.

    *space* is the auto-space left off so it wouldn't be sent; a held send
    types it, or what's said next sticks to the last word ("delay.All")."""
    global _pending
    pending = {"to": sent_to, "why": why, "press": press, "type": type_more,
               "space": space, "mode": injection_mode, "timer": None, "ticks": []}
    # Nothing to count down to when no Enter could go: the delivery saw the
    # focus move, or the window can't be told and the policy says no.
    if reason := _why_not(sent_to, sent_to):
        _say_not_sent(reason)
        return
    delay = _delay
    if delay > 0:
        pending["timer"] = threading.Timer(delay, _send, args=(pending,))
        # The label counts down once a second: "Sending in 4 s", 3, 2, 1.
        pending["ticks"] = [threading.Timer(delay - left, _count_down, args=(pending, left))
                            for left in range(int(delay) - 1, 0, -1)]
    with _send_lock:
        if _pending is not None:
            _cancel_timers(_pending)
        _pending = pending
    if delay <= 0:
        _send_right_away(pending)
        return
    for timer in [pending["timer"], *pending["ticks"]]:
        timer.daemon = True
        timer.start()
    logger.info(f"Sending in {delay:g}s ({why})")
    from . import screen_message
    screen_message.show_over_active_window(
        _label(delay), seconds=delay, warning=False, tag=LABEL_TAG, low=True)


def _send_right_away(pending: dict) -> None:
    """"Right away": the Enter on a thread of its own, as after a countdown.
    A dictation is typed on the hotkey loop's own thread (stop_recording),
    and run there the Enter's checks and its wait for a Right Ctrl or Right
    Alt dictation key kept the loop from reading the tap that holds it."""
    threading.Thread(target=_send, args=(pending,), name="send-right-away", daemon=True).start()


def _label(seconds_left: float) -> str:
    return f"Sending in {seconds_left:g} s. Tap {_hold_key_name} to hold it."


def _count_down(pending: dict, seconds_left: int) -> None:
    """One second less on the "Sending in" label, unless that send is over.
    A send that couldn't go any more (another window, a password field) ends
    here, so the label doesn't count down to a send that won't happen."""
    global _pending
    from . import screen_message
    with _send_lock:
        if _pending is not pending:
            return
    if why := _why_not(pending["to"], focused_window.destination()):
        with _send_lock:
            if _pending is not pending:
                return
            _pending = None
        _cancel_timers(pending)
        _say_not_sent(why, relabel=True)
        return
    screen_message.relabel(LABEL_TAG, _label(seconds_left))


def _cancel_timers(pending: dict) -> None:
    for timer in [pending["timer"], *pending["ticks"]]:
        if timer is not None:
            timer.cancel()


def _send(pending: dict) -> None:
    """The wait is over (or there was none) and nothing held it: press
    Enter through the claim (_claim).

    A Right Ctrl or Right Alt dictation key down now, or going down before
    the Enter does, is waited for first (modifier_hotkeys), with the send
    pending until the Enter goes down: the hotkey loop reads a tap of it
    meanwhile, and the tap holds the send as it would have."""
    global _pending
    with _send_lock:
        if _pending is not pending:
            return
    sent = pending["press"]("ENTER", pending["why"], claim=lambda: _claim(pending))
    with _send_lock:
        unclaimed = _pending is pending
        if unclaimed:
            _pending = None
    if unclaimed and not sent:
        # A wait that gave up says nothing of why: the window may have
        # changed while it waited for the key.
        why = pending.get("refused") or _why_not(pending["to"], focused_window.destination())
        _say_not_sent(why or "not_pressed")


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


def _focus_left(pending: dict) -> bool:
    """Whether another window than the one the text went to is known to
    have the focus, for the space a held send owes."""
    return focused_window.changed(pending["to"], focused_window.look())


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
