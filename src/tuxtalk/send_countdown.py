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
typing still refuses a password field. The typing is the pipeline's: the
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
# or the next dictation (type_owed_space), else None.
# Typed while the key is down, GNOME without the extension sees the key
# too and the space is lost.
_owed = None
# Recordings begun so far (recording_begins). A recording's Message carries
# its number (serial); a send for one with an older number waits for the newer.
_begun = 0
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
    ended the message before it (type_at_pause.start_afresh): one dictation, or Type
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
    # The word its last words were cut off on ("and"), or "": at the end of
    # a Type at pause recording, half a sentence isn't sent.
    ends_on: str = ""
    # Which recording it is (rs.state.recording_serial when that began):
    # Recent Dictations and the screen copy keep a recording's phrases together.
    serial: int = 0
    # What Type at pause's next phrase of this recording picks up: the last
    # phrase's final mark, held back for it to decide
    # (chunking.hold_back_boundary: {"mark", "audio", "raw", "typed",
    # "where"}); a "No wait." said alone, whose corrected value comes next; a
    # "?" heard as mid-sentence, so the question ends with the next phrase
    # (chunking.carry_question); and the text "edit that" said alone is to
    # change. Each recording's own, so a recording started before the last
    # one's phrases are typed can't take them over.
    held_mark: dict | None = None
    held_cue: str = ""
    question_open: bool = False
    edit_waiting: str | None = None
    # Its recording was cancelled (Esc): a phrase of it still waiting to be
    # typed is dropped.
    cancelled: bool = False

    def add(self, outcome: str, landed) -> None:
        """Words of it were delivered: delivery.inject_text's *outcome*, and
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
        self.to, self.has_words, self.complete, self.ends_on = None, False, True, ""


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
    pipeline.press_key; *type_more*(text, mode) types onto the dictation.

    *space* is the auto-space left off so it wouldn't be sent; a held send
    types it, or what's said next sticks to the last word ("delay.All").

    True when a newer recording had begun (recording_begins), its key read
    while these words were still being typed: the send is held for it, as a
    press during the countdown holds it, and its space owed."""
    global _pending, _owed
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
    global _begun
    # The count and the hold in one go: the Enter's claim (_claim) checks
    # under the same lock that its send is still pending.
    with _send_lock:
        _begun += 1
        number = _begun
        pending = _take_pending()
    return number, _stop_held(pending, announce=False)


def _send_right_away(pending: dict) -> None:
    """"Right away": the Enter on a thread of its own, as after a countdown,
    so the dictation worker, which typed the words, goes on to what's
    queued after them first; the Enter waits its turn there
    (pipeline.press_key), and a recording begun meanwhile holds it."""
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


def type_owed_space(before: str | None = None) -> None:
    """Type the space a held or refused send left off, in the window its
    text went to: once the hotkey that held it is up, or ahead of the next
    dictation, whose text is *before*. On its own and held to that window,
    never as part of the next dictation's text: a dictation goes wherever
    the focus is, and a space in it would go along. Dropped when that
    dictation is empty or brings its own space."""
    pending = _take_owed()
    if pending is None or _focus_left(pending):
        return
    if before is not None and (not before or before[0].isspace()):
        return
    # Held to that window up to the key: the focus can move after this look.
    pending["type"](pending["space"], pending["mode"], into=pending["to"])
