"""What the service remembers of the dictation in progress: its entry in
Recent Dictations (one per recording, added to phrase by phrase when Type
at pause is on), and its screen copy, the text it believes is in the box,
which voice edits and "edit that" work from. The copy is dropped as soon as
it can't be trusted."""
from . import keyboard_output
from . import recording_state as rs
from .normalize import LINE_BREAK


def remember_said(text: str, chunked: bool, takes_back: str = "") -> str:
    """Put *text* in Recent Dictations, one entry per recording: a phrase of
    a recording typed at pauses (*chunked*) adds to its recording's entry.
    *takes_back* is the end of that entry a correction across a pause
    replaced ("Meet at three. " for "Meet at four"). Returns the entry as it
    was before, for unsay."""
    from . import history
    text = text.replace(LINE_BREAK, "\n")
    same_recording = rs.state.said_serial == rs.state.recording_serial
    old = rs.state.said_text if chunked and same_recording else ""
    base = old[:len(old) - len(takes_back)] if takes_back and old.endswith(takes_back) else old
    rs.state.said_serial, rs.state.said_text = rs.state.recording_serial, base + text
    if old:
        history.add_entry(rs.state.said_text, replaces=old)
    else:
        history.add_entry(rs.state.said_text)
    return old


def unsay(before: str) -> None:
    """Undo the last remember_said, which returned *before*: the text went
    into Recent Dictations first and was then refused for a password field."""
    from . import history
    said, rs.state.said_text = rs.state.said_text, before
    if before:
        history.add_entry(before, replaces=said)
    else:
        history.forget(said)


def remember_shown(piece: str, chunked: bool, ok: bool) -> None:
    """*piece* was just typed (*ok*) or tried: add it to this recording's
    screen copy. The copy is only trusted while every piece landed, in one
    window, one after the other."""
    if not (chunked and rs.state.shown_serial == rs.state.recording_serial):
        rs.state.shown_serial, rs.state.shown_text = rs.state.recording_serial, ""
        rs.state.shown_window = rs.state.last_inserted_window
    if rs.state.shown_text is None:
        return
    if ok and not keyboard_output.not_where_typed(rs.state.shown_window):
        rs.state.shown_text += piece
    else:
        rs.state.shown_text = None


def trim_shown(tail: str, count: int) -> None:
    """*count* characters were backspaced off *tail*, the newest typing."""
    shown = rs.state.shown_text
    if shown is not None:
        rs.state.shown_text = shown[:len(shown) - count] if tail and shown.endswith(tail) else None


def forget_shown() -> None:
    """The cursor moved or the text changed: the screen copy can't be trusted."""
    rs.state.shown_text = None
