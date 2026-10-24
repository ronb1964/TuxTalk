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
     they may be in two places, and no send is set up. The recording's
     Message keeps where its words went, and whether all of them did.
  2. Right before the Enter goes down, after every wait (the countdown, a
     Right Ctrl or Right Alt dictation key held, the keyboard taken back
     from other typing, the Flatpak's portal getting ready), one claim
     (_claim) makes every check again: the send wasn't held, the same
     window has the focus before the password check and after it, the
     cursor isn't in a password field, and no other key went out on
     TuxTalk's keyboard while the checks were made. In the Flatpak, keys go
     through the desktop's portal (output_backends.LibeiOutputBackend),
     which this last check can't count.

Anything else, nothing is sent and the notice says why (_why_not). Where
the desktop can't say which window has the focus, the two kinds of key part
ways (_why_not's *spoken*).

The send waiting is _pending, and the space a held or refused one owes,
_owed, both guarded by _send_lock. The owed space follows a gentler rule than the
Enter: it goes in unless another window is known to have the focus, since
typing still refuses a password field. The typing is app.py's: the
dictation hands over what presses a key and what types more onto it.
"""
import threading
from dataclasses import dataclass

from . import focused_window, virtual_keyboard
from .logger import setup_logger

logger = setup_logger(__name__)

_send_lock = threading.Lock()
LABEL_TAG = "send-countdown"
HELD_NOTICE = "Not sent. Press Enter when you're ready."
SWITCHED_WINDOW_NOTICE = "Not sent: you switched windows."
# Not "you switched": on KDE, a click on the desktop before dictating moves
# the focus back to the last window by itself while a paste goes in.
MOVED_NOTICE = "Not sent: the focus moved while the words went in."
PASSWORD_FIELD_NOT_SENT = "Not sent: the cursor is in a password field."
# Not "this desktop doesn't say": a tray too busy to answer in time can't
# tell either (Preferences opening, a model loading).
UNKNOWN_WINDOW_NOTICE = ("Not sent: TuxTalk can't tell which window has the focus. "
                         "Press Enter when you're ready.")
OWN_WINDOW_NOTICE = "Not sent: a TuxTalk window has the focus."
INCOMPLETE_NOTICE = "Not sent: part of the message didn't go in."

# Why an Enter wasn't sent: (what the log says after "Not sent: ", the notice).
_NOT_SENT = {
    "switched": ("another window has the focus now", SWITCHED_WINDOW_NOTICE),
    "moved": ("the focus moved while the words were delivered", MOVED_NOTICE),
    "password": ("a password field has the focus now", PASSWORD_FIELD_NOT_SENT),
    "own_window": ("a TuxTalk window has the focus", OWN_WINDOW_NOTICE),
    "incomplete": ("part of the message didn't go in", INCOMPLETE_NOTICE),
    "typed_meanwhile": ("other keys went out while it was checked", HELD_NOTICE),
    "unknown_window": ("which window has the focus isn't known", UNKNOWN_WINDOW_NOTICE),
    "not_pressed": ("a dictation key stayed down, or the key couldn't be pressed", HELD_NOTICE),
}

_delay = 2.0                # seconds before the Enter (Preferences)
_hold_key_name = "F8"       # named in the "Sending in 2 s" label

# The send counting down to its Enter, until it presses it or a tap of a
# hotkey holds it, else None: {"to", "why", "press", "type", "space",
# "mode", "timer", "ticks", "refused", "stopped"}.
_pending = None
# The send a tap held, or one refused once its countdown began, while the
# space it left off waits for the hotkey that held it to come up
# (type_owed_space) or the next dictation (with_owed_space), else None.
# Typed while the key is down, GNOME without the extension sees the key
# too and the space is lost.
_owed = None
# Whether this session's notifications have said why an automatic send
# can't go where the focus can't be told: on such a desktop it's every
# dictation, so after once only the floating notice says so.
_told_unknown_window = False


def configure(cfg) -> None:
    """Take the wait and the hold key's name from the settings *cfg*."""
    global _delay, _hold_key_name
    _delay = float(getattr(cfg, "press_enter_delay", 2.0))
    _hold_key_name = getattr(cfg, "hotkey", "") or "the hotkey"


# --- Where the Enter may go ---------------------------------------------------

@dataclass
class Message:
    """What a recording has typed since it began, or since Enter or Tab
    ended the message before it (app._start_afresh): one dictation, or Type
    at pause's phrases, rewritten by "edit that" or not; each recording has
    its own. An automatic Enter and a key said after its words go only where
    they went, and only while all of them went in: a phrase that stopped
    partway leaves a hole a send would let out, and the phrases after it
    don't fill it."""
    # Where its words went (landed()), None once the focus moved while any
    # of them went in: they may be in two places.
    to: focused_window.Destination | None = None
    # Words of it were delivered, or tried: a key said now isn't said alone.
    has_words: bool = False
    # Every delivery of it went in whole.
    complete: bool = True

    def add(self, outcome: str, landed) -> None:
        """Words of it were delivered: app._inject_text's *outcome*, and
        where they went (*landed*, None when not all of them did). Words
        known to be in another window move it there (the user switched
        windows between two phrases); a look that can't tell (a held-back
        mark typed where only the app is known) keeps where it was."""
        if not self.has_words or (self.to is not None and (
                landed is None or focused_window.changed(self.to, landed))):
            self.to = landed
        self.has_words = True
        self.complete = self.complete and outcome == "ok"

    def start_over(self) -> None:
        """Enter or Tab ended it: what's typed next is a new message."""
        self.to, self.has_words, self.complete = None, False, True


def _incomplete(message: Message) -> str | None:
    """"incomplete" when part of *message* didn't go in, else None."""
    return None if message.complete else "incomplete"


def landed(before, after):
    """Where an automatic Enter may go after words were delivered: *before*
    (a focused_window.destination() taken before them), or None when
    *after* (a look once they're in) is known to be another window."""
    return None if focused_window.changed(before, after) else before


def _why_not(sent_to, now, spoken: bool = False) -> str | None:
    """Why an automatic Enter mustn't go into *now* (a destination) for
    words delivered to *sent_to* (landed()'s answer), or None when it may.
    A key of _NOT_SENT.

    Where the desktop can't say which window has the focus (COSMIC tells
    only the app, and so does GNOME's extension from 0.18 until the user
    logs out and in; GNOME without the extension and Plasma 5.27 on Wayland
    tell nothing, nor does a tray too busy to answer), an automatic Enter
    doesn't go: a message sent into the wrong chat can't be taken back, and
    the words stay for the user to send. A key *spoken* after the words
    ("... press enter") still goes: the user asked for it, looking at the
    window. Either is stopped where another window, a password field or
    TuxTalk's own window is known to have the focus; a spoken key goes into
    TuxTalk's own window only when its words were dictated there too."""
    if sent_to is None:
        return "moved"
    if now.password:
        return "password"
    # Preferences, or an offer that opened by itself: Enter would press its
    # default button. A key said after words dictated into one of TuxTalk's
    # own boxes (Add Word: "ClaudeClaw, press tab") goes there, as anywhere.
    if focused_window.is_own(now) and not (spoken and focused_window.is_own(sent_to)):
        return "own_window"
    same = focused_window.same_window(sent_to, now)
    if same is False:
        return "switched"
    return "unknown_window" if same is None and not spoken else None


def _claim(pending, spoken: bool = False):
    """virtual_keyboard.send's *claim* for the Enter of *pending*: the
    checks that take a while, made with the keyboard free and the send still
    pending, so a tap meanwhile still holds it. Returns *take*, which runs
    with the keyboard held, right before the Enter goes down. Either raises
    virtual_keyboard.GaveUp when the Enter mustn't go. A key *spoken* after
    the words (press_where_sent) is no automatic send: no tap holds it, and
    it goes where only the app is known (_why_not)."""
    start = virtual_keyboard.emitted()
    first = focused_window.look()
    now = focused_window.destination()
    # A click during the password check: its answer may be the other window's.
    why = ("switched" if focused_window.changed(first, now)
           else _why_not(pending["to"], now, spoken=spoken))
    if why:
        _refuse(pending, why)

    def take():
        global _pending
        with _send_lock:
            if not spoken and _pending is not pending:
                raise virtual_keyboard.GaveUp("held")
            if virtual_keyboard.emitted() != start:
                _refuse(pending, "typed_meanwhile")
            if not spoken:
                _pending = None
    return take


def _refuse(pending, why: str) -> None:
    """Stop the Enter of *pending*; it's said why once the press is over
    (_send), unless a tap held it first."""
    pending["refused"] = why        # the keyboard's callers drop GaveUp's reason
    raise virtual_keyboard.GaveUp(why)


def _say_not_sent(why: str, relabel: bool = False, key: str = "ENTER") -> None:
    """Say why the Enter (or *key*) of a send didn't go: in the log, over
    the window, and as a desktop notification whatever the notifications
    setting. A screen reader reads the notification, not the label, and
    without XWayland the label can't be shown at all. The notification goes
    from a thread of its own, as say_held's does: this runs on the typing
    thread, which reads the hotkeys."""
    global _told_unknown_window
    from .notify import notify
    text, notice = _NOT_SENT[why]
    what = "Not sent" if key == "ENTER" else f"{key.title()} not pressed"
    if key != "ENTER":
        notice = f"{what}: {text}."
    logger.info(f"{what}: {text}")
    body = f"The text is still in the box. Press {key.title()} yourself when you're ready."
    if why == "unknown_window":
        body = ("TuxTalk can't tell which window has the focus here, so it doesn't press "
                "Enter by itself. Press Enter yourself, or end with \u201cpress enter\u201d.")
    once = why == "unknown_window"
    if not (once and _told_unknown_window):
        _told_unknown_window = _told_unknown_window or once
        threading.Thread(target=notify, name="send-not-sent-notice", daemon=True,
                         args=(f"{what}: {text}", body)).start()
    if relabel:
        from . import screen_message
        screen_message.relabel(LABEL_TAG, notice)
    else:
        show_notice(notice)


def press_where_sent(key: str, why: str, message: Message, press) -> bool:
    """Press *key*, said after *message*'s words: "... press enter" sends as
    an automatic Enter does, and "... press tab" leaves the box, so either
    goes only where the words went, by the same claim, with no wait to hold
    it. *press* is as for send_automatically. Not pressed, the notice says
    why."""
    pending = {"to": message.to}
    if not (refused := _incomplete(message)) and press(
            key, why, claim=lambda: _claim(pending, spoken=True)):
        return True
    _say_not_sent(refused or pending.get("refused") or "not_pressed", key=key)
    return False


def type_where_sent(text: str, key: str, message: Message, type_more) -> str | None:
    """Type *text* in place of *key* said after *message*'s words (Claude
    Desktop's four spaces for Tab: its Tab key jumps to the Send button), by
    the key's rule: only where the words went. *type_more*(text, mode,
    into=None) types onto the dictation, into the window *into* only. The
    typing's outcome, or None when it wasn't tried; either way not typed,
    the notice says why."""
    why = _incomplete(message) or _why_not(message.to, focused_window.destination(), spoken=True)
    outcome = None if why else type_more(text, "type", into=message.to)
    if why or outcome == "moved":
        _say_not_sent(why or "switched", key=key)
    return outcome


# --- The countdown ------------------------------------------------------------

def send_automatically(why: str, message: Message, press, type_more, space: str = "",
                       injection_mode: str = "type") -> None:
    """Press Enter for *message*, just delivered, after the wait (hold),
    through the one claim. *press*(key, why, claim=None) presses it, as
    app._press_key; *type_more*(text, mode) types onto the dictation.

    *space* is the auto-space left off so it wouldn't be sent; a held send
    types it, or what's said next sticks to the last word ("delay.All")."""
    global _pending
    pending = {"to": message.to, "why": why, "press": press, "type": type_more,
               "space": space, "mode": injection_mode, "timer": None, "ticks": []}
    # Nothing to count down to when no Enter could go: part of the message
    # is missing, the delivery saw the focus move, or the window can't be
    # told.
    if reason := _incomplete(message) or _why_not(message.to, message.to):
        _say_not_sent(reason)
        if reason == "unknown_window" and space:
            # No Enter could ever follow: the space goes in now, as with
            # sending off, and only where the words went. Owed, it would
            # start the next message after the user's own Enter.
            type_more(space, injection_mode, into=message.to)
        else:
            _owe_space(pending)
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
            _pending["stopped"] = True
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
    why = _why_not(pending["to"], focused_window.destination())
    # A tick that can't tell (the tray busy for a moment) leaves it to the
    # claim at the Enter, which refuses if it still can't.
    if why and why != "unknown_window":
        with _send_lock:
            if _pending is not pending:
                return
            _pending = None
        _cancel_timers(pending)
        _say_not_sent(why, relabel=True)
        _owe_space(pending)
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
    if sent or pending.get("stopped"):
        return
    # A wait that gave up says nothing of why: the window may have changed
    # while it waited for the key. Claimed, the keyboard failed on the Enter.
    why = pending.get("refused") or (
        unclaimed and _why_not(pending["to"], focused_window.destination()))
    _say_not_sent(why or "not_pressed")
    _owe_space(pending)


def say_held() -> None:
    """A tap held the send: said over the window and as a desktop
    notification whatever the notifications setting, as every other end
    of a send without its Enter is (_say_not_sent). The notification goes
    from a thread of its own: the tap is read on the hotkey loop, and a
    slow notification service would hold up the keys after it."""
    from .notify import notify
    show_notice(HELD_NOTICE)
    threading.Thread(target=notify, name="send-held-notice", daemon=True, args=(
        "Not sent: you held it",
        "The text is still in the box. Press Enter when you're ready.")).start()


def show_notice(text: str) -> None:
    """Say over the window, where the countdown was, what became of the send."""
    from . import screen_message
    screen_message.show_over_active_window(text, warning=False, tag=LABEL_TAG, low=True)


def hold(announce: bool) -> bool:
    """Stop a counting-down send; True when there was one. *announce* says
    "Not sent" at once (a tap of the toggle key, say_held); a press of the
    hold key waits to see whether it's a tap or more dictation."""
    global _pending, _owed
    with _send_lock:
        pending, _pending = _pending, None
        if pending is not None and pending.get("space"):
            _owed = pending
    if pending is None:
        return False
    pending["stopped"] = True       # its press, if under way, says nothing more
    _cancel_timers(pending)
    logger.info("Held the send: the dictation stays unsent")
    if announce:
        say_held()
    else:
        from . import screen_message
        screen_message.hide(LABEL_TAG)
    return True


def _owe_space(pending: dict) -> None:
    """A send that didn't go owes the space it left off, as a held one does:
    what's said next into its window starts after it, and doesn't stick to
    the last word ("noon.How")."""
    global _owed
    # Without the window its words went to (the focus moved while they went
    # in), there is nowhere it could go.
    if pending.get("space") and pending["to"] is not None:
        with _send_lock:
            _owed = pending


def owes_space() -> bool:
    """Whether a held or refused send's space is still owed."""
    return _owed is not None


def _focus_left(pending: dict) -> bool:
    """Whether another window than the one the text went to is known to
    have the focus, for the space a held or refused send owes."""
    return focused_window.changed(pending["to"], focused_window.look())


def _take_owed():
    """The send whose space is owed, now no longer owed, or None."""
    global _owed
    with _send_lock:
        pending, _owed = _owed, None
    return pending


def type_owed_space() -> None:
    """The hotkey that held a send is up: type the space it left off, in the
    window its text went to."""
    pending = _take_owed()
    if pending is not None and not _focus_left(pending):
        pending["type"](pending["space"], pending["mode"])


def with_owed_space(text: str) -> str:
    """Put an owed space in front of *text*, the next dictation, when it
    goes to the same window."""
    pending = _take_owed()
    if pending is None or not text or text[0].isspace() or _focus_left(pending):
        return text
    return pending["space"] + text
