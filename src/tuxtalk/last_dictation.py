"""What the service remembers of the dictation in progress: its entry in
Recent Dictations (one per recording, added to phrase by phrase when Type
at pause is on), and its screen copy, the text it believes is in the box,
which voice edits and "edit that" work from. The copy is dropped as soon as
it can't be trusted."""
import time

from . import focused_window
from . import recording_state as rs
from .normalize import LINE_BREAK


def remember_said(text: str, chunked: bool, message, takes_back: str = "") -> str:
    """Put *text* in Recent Dictations, one entry per recording: a phrase of
    a recording typed at pauses (*chunked*) adds to its recording's entry,
    kept on its *message* (send_countdown.Message.said).
    *takes_back* is the end of that entry a correction across a pause
    replaced ("Meet at three. " for "Meet at four"). Returns the entry as it
    was before, for unsay."""
    from . import history
    text = text.replace(LINE_BREAK, "\n")
    old = message.said if chunked else ""
    base = old[:len(old) - len(takes_back)] if takes_back and old.endswith(takes_back) else old
    message.said = base + text
    if old:
        history.add_entry(message.said, replaces=old)
    else:
        history.add_entry(message.said)
    return old


def unsay(message, before: str) -> None:
    """Undo the last remember_said of *message*, which returned *before*:
    the text went into Recent Dictations first and was then refused for a
    password field."""
    from . import history
    said, message.said = message.said, before
    if before:
        history.add_entry(before, replaces=said)
    else:
        history.forget(said)


def remember_shown(piece: str, chunked: bool, ok: bool, message) -> None:
    """*piece* was just typed (*ok*) or tried: add it to the screen copy of
    *message*'s recording. The copy is only trusted while every piece
    landed, in one window, one after the other."""
    if not chunked or message.shown_window is None:
        message.shown, message.shown_window = "", _where_typed()
    if message.shown is None:
        return
    if ok and focused_window.same_window(message.shown_window, _where_typed()) is not False:
        message.shown += piece
    else:
        message.shown = None


def _where_typed():
    """(class, which window) the last piece went to, as focused_window looks."""
    return rs.state.last_inserted_window, rs.state.last_inserted_where[1]


def forget_after_key(key: str, why: str) -> None:
    """TuxTalk pressed *key* ("ENTER" or "TAB") after a dictation, for *why*:
    what's behind it is out of reach of undo, "edit that" and voice moves,
    which would count on a dictation that's gone. After Enter, "edit that"
    says it was sent. Run on the dictation worker in the same step as the
    key (delivery.press_key), so nothing can be typed between them."""
    rs.state.last_inserted_text, rs.state.nav_context = "", None
    if key == "ENTER":
        rs.state.sent = {"at": time.time(), "automatic": why.startswith("Send each")}


def trim_shown(tail: str, count: int) -> None:
    """*count* characters were backspaced off *tail*, the newest typing."""
    last = rs.state.last_message
    if last.shown is not None:
        last.shown = (last.shown[:len(last.shown) - count]
                      if tail and last.shown.endswith(tail) else None)


def forget_shown() -> None:
    """The cursor moved or the text changed: the screen copy of the last
    dictation can't be trusted."""
    rs.state.last_message.shown = None
