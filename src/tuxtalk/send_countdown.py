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
     Message (recording_state) keeps where its words went, and whether
     all of them did.
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
typing still refuses a password field. The typing is delivery.py's, which
imports this module: it hands over what presses a key and what types more
onto the dictation.
"""
import threading

from . import focused_window, virtual_keyboard
from .logger import setup_logger
from .recording_state import Message

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
# or the next dictation (give_back_space, type_owed_space), else None.
# Typed while the key is down, GNOME without the extension sees the key
# too and the space is lost.
_owed = None
# Recordings begun so far (recording_begins). A recording's Message carries
# its number (serial); a send for one with an older number waits for the newer.
_begun = 0
# The recording a send was held for when it was set up (send_automatically),
# until "Not sent" is said for it (say_held_for) or the next recording
# begins: its number, else 0.
_held_for = 0
# The dictation keys whose presses, TuxTalk's, are still down (F8 and F9
# can overlap), guarded by _send_lock. The space a held send owes waits for
# all of them to come up: typed while one is down, GNOME without the
# extension sees the key too and the space is lost. A press turned away as
# another app's shortcut (Alt+F8) never counts.
_keys_down = set()
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
    the window, and as a desktop notification (_notify_in_background)."""
    global _told_unknown_window
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
        _notify_in_background(f"{what}: {text}", body)
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
                       injection_mode: str = "type") -> bool:
    """Press Enter for *message*, just delivered, after the wait (hold),
    through the one claim. *press*(key, why, claim=None) presses it, as
    delivery.press_key; *type_more*(text, mode, into=...) types onto the
    dictation, only into the window *into* names.

    *space* is the auto-space left off so it wouldn't be sent; a held send
    types it, or what's said next sticks to the last word ("delay.All").

    True when a newer recording had begun (recording_begins), its key read
    while these words were still being typed: the send is held for it, as a
    press during the countdown holds it, and its space owed."""
    global _pending, _owed, _held_for
    pending = {"to": message.to, "why": why, "press": press, "type": type_more,
               "space": space, "mode": injection_mode, "timer": None, "ticks": []}
    # Nothing to count down to when no Enter could go: part of the message
    # is missing, the delivery saw the focus move, or the window can't be
    # told.
    if reason := _incomplete(message) or _why_not(message.to, message.to):
        _say_not_sent(reason)
        if reason == "unknown_window" and space:
            # No Enter could ever follow: the space goes in now, as with
            # sending off, and only where the words went, as far as the
            # desktop tells (on COSMIC, any window of that app; on GNOME
            # without the extension, wherever the focus is). Owed, it would
            # start the next message after the user's own Enter.
            type_more(space, injection_mode, into=message.to)
        else:
            _owe_space(pending)
        return False
    delay = _delay
    if delay > 0:
        pending["timer"] = threading.Timer(delay, _send, args=(pending,))
        # The label counts down once a second: "Sending in 4 s", 3, 2, 1.
        pending["ticks"] = [threading.Timer(delay - left, _count_down, args=(pending, left))
                            for left in range(int(delay) - 1, 0, -1)]
    with _send_lock:
        newer = message.serial != _begun
        if newer:
            _held_for = _begun
            if space and message.to is not None:
                _owed = pending
        else:
            if _pending is not None:
                _cancel_timers(_pending)
                _pending["stopped"] = True
            _pending = pending
    if newer:
        logger.info("Held the send: another recording began before it")
        return True
    if delay <= 0:
        _send_right_away(pending)
        return False
    for timer in [pending["timer"], *pending["ticks"]]:
        timer.daemon = True
        timer.start()
    logger.info(f"Sending in {delay:g}s ({why})")
    from . import screen_message
    screen_message.show_over_active_window(
        _label(delay), seconds=delay, warning=False, tag=LABEL_TAG, low=True)
    return False


def recording_begins() -> tuple[int, bool]:
    """A recording begins, before its microphone opens: its number, and
    whether it held a send counting down (hold). Counted under the send's
    lock, so a send set up from now on for an earlier recording is held
    for this one instead (send_automatically); one set up before is held
    here."""
    global _begun, _held_for
    # The count and the hold in one go: the Enter's claim (_claim) checks
    # under the same lock that its send is still pending.
    with _send_lock:
        _begun += 1
        _held_for = 0
        number = _begun
        pending = _take_pending()
    return number, _stop_held(pending, announce=False)


def _send_right_away(pending: dict) -> None:
    """"Right away": the Enter on a thread of its own, as after a countdown,
    so the dictation worker, which typed the words, goes on to what's
    queued after them first; the Enter waits its turn there
    (delivery.press_key), and a recording begun meanwhile holds it."""
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
            pending["stopped"] = True       # as _take_pending: said once, here
        _cancel_timers(pending)
        _say_not_sent(why, relabel=True)
        _owe_space(pending)
        return
    # Checked again: a tap during the focus check held the send, and its
    # notice must not be turned back into a countdown.
    with _send_lock:
        if _pending is pending:
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
    notification, as every other end of a send without its Enter is
    (_notify_in_background; here the tap is read on the hotkey loop)."""
    show_notice(HELD_NOTICE)
    _notify_in_background("Not sent: you held it",
                          "The text is still in the box. Press Enter when you're ready.")


def say_held_for(serial: int) -> bool:
    """Say "Not sent" for the send held for recording *serial* when it was
    set up (send_automatically), once: that recording ended without adding
    to it. The recording's end and the send come in either order (a
    microphone that fails to open takes a moment to give up), and whichever
    is second says it. False when no send was held for it, or it was said."""
    global _held_for
    with _send_lock:
        if serial != _held_for:
            return False
        _held_for = 0
    say_held()
    return True


def held_for(serial: int) -> bool:
    """Whether a send set up while recording *serial* ran was held for it
    and its "Not sent" isn't said yet (say_held_for)."""
    with _send_lock:
        return serial == _held_for


def key_pressed(code: int) -> None:
    """A press of dictation key *code* that is TuxTalk's: until it comes up,
    an owed space waits (give_back_space)."""
    with _send_lock:
        _keys_down.add(code)


def key_released(code: int) -> bool:
    """Dictation key *code* came up: True when its press was TuxTalk's
    (key_pressed)."""
    with _send_lock:
        if code not in _keys_down:
            return False
        _keys_down.discard(code)
        return True


def say_held_back(word: str, message: Message) -> None:
    """An automatic Enter held back because *message* ended on *word*
    ("and", key_commands.ends_unfinished): said as any unsent message is
    (_say_not_sent), or it looks as if sending were off. Where the Enter
    couldn't have gone anyway (part of the message missing, another window
    in front), that reason is the one said."""
    why = _incomplete(message) or _why_not(message.to, focused_window.destination())
    if why:
        _say_not_sent(why)
        return
    ended = f"it ended on \u201c{word}\u201d"
    _notify_in_background(f"Not sent: {ended}",
                          "The text is still in the box. Press Enter when you're ready, or "
                          "go on dictating: the next dictation carries the sentence on.")
    show_notice(f"Not sent: {ended}. Press Enter when you're ready.")


def _notify_in_background(title: str, body: str) -> None:
    """A desktop notification whatever the notifications setting, from a
    thread of its own: a slow notification service would hold up what the
    calling thread does next (the dictation worker, a send's timer, the
    hotkey loop). A
    screen reader reads it, not the label over the window, and without
    XWayland the label can't be shown at all."""
    from .notify import notify
    threading.Thread(target=notify, name="send-not-sent-notice", daemon=True,
                     args=(title, body)).start()


def show_notice(text: str) -> None:
    """Say over the window, where the countdown was, what became of the send."""
    from . import screen_message
    screen_message.show_over_active_window(text, warning=False, tag=LABEL_TAG, low=True)


def hold(announce: bool) -> bool:
    """Stop a counting-down send; True when there was one. *announce* says
    "Not sent" at once (a tap of the toggle key, say_held); a press of the
    hold key waits to see whether it's a tap or more dictation."""
    with _send_lock:
        pending = _take_pending()
    return _stop_held(pending, announce)


def _take_pending():
    """The send counting down, no longer pending and marked stopped, its
    space owed; None if there was none. With _send_lock held: marked under
    the lock, its own thread can't find it neither claimed nor stopped and
    say "Not sent" for a press that only meant to dictate more."""
    global _pending, _owed
    pending, _pending = _pending, None
    if pending is not None:
        pending["stopped"] = True
        if pending.get("space"):
            _owed = pending
    return pending


def _stop_held(pending, announce: bool) -> bool:
    """Stop *pending*, just taken (_take_pending), and say so as hold does;
    False when there was none."""
    if pending is None:
        return False
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


def type_owed_space(before: str) -> None:
    """Type the space a held or refused send left off ahead of the next
    dictation, whose text is *before*, in the window the send's text went
    to. On its own and held to that window, never as part of the next
    dictation's text: a dictation goes wherever the focus is, and a space
    in it would go along. Dropped when that dictation is empty or brings
    its own space."""
    pending = _take_owed()
    if pending is None or _focus_left(pending) or not before or before[0].isspace():
        return
    # Held to that window up to the key: the focus can move after this look.
    pending["type"](pending["space"], pending["mode"], into=pending["to"])


def give_back_space() -> None:
    """Type the space a held or refused send left off, on its own, in the
    window its text went to, on the dictation worker: once the dictation
    key that held the send is up, or its recording ended with nothing to
    add. Looked at when it runs, not when it was queued, and again right
    before its key goes out (_refuse_unless_alone): while a dictation key
    of TuxTalk's is down (pressed again meanwhile) or Alt, Ctrl or Super is
    (as far as TuxTalk can tell: modifier_hotkeys.shortcut_keys_down), the
    space stays owed, for the key's release or the next dictation."""
    global _owed
    from . import keyboard_output, modifier_hotkeys
    if modifier_hotkeys.shortcut_keys_down():       # asks the keyboards: not under the lock
        return
    with _send_lock:
        if _keys_down:
            return
        pending, _owed = _owed, None
    if pending is None or _focus_left(pending):
        return
    keyboard_output.this_thread.last_check = _refuse_unless_alone
    try:
        outcome = pending["type"](pending["space"], pending["mode"], into=pending["to"])
    finally:
        keyboard_output.this_thread.last_check = None
    if outcome == "keys_down":      # Alt or a dictation key went down meanwhile
        with _send_lock:
            if _owed is None:
                _owed = pending


def _refuse_unless_alone() -> None:
    """The space's last check, right before its key, after the window's:
    GaveUp("keys_down") while Alt, Ctrl or Super, or a dictation key of
    TuxTalk's, is down, or may be (the hotkey helper not connected). Made
    with TuxTalk's keyboard held, so it waits on nothing: a pause here would
    let a window switch slip in after the window check. The keyboards are
    read outside _send_lock."""
    from . import modifier_hotkeys
    if modifier_hotkeys.shortcut_keys_maybe_down() or _press_down():
        logger.info("Space not typed yet: Alt, Ctrl, Super or a dictation key is down")
        raise virtual_keyboard.GaveUp("keys_down")


def forget_presses(still_down) -> bool:
    """Forget TuxTalk's dictation key presses not in *still_down* (the
    dictation keys the keyboards report down now): their keyboard went away,
    or they aren't dictation keys any more, and their release will never be
    read. True when none is left, so a space they held back may go now."""
    with _send_lock:
        _keys_down.intersection_update(still_down)
        return not _keys_down


def _press_down() -> bool:
    """Whether a dictation key press of TuxTalk's is down (key_pressed)."""
    with _send_lock:
        return bool(_keys_down)
