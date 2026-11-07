"""Mouse by voice, step by step: "mouse grid" shows the numbered grid, a
number zooms into a cell, and "click" or "move" puts the pointer there.

It runs on the dictation worker, like every voice command. The grid's
windows belong to GTK's thread, so each step on them goes through
gtk_loop.call, and the worker polls for where the pointer arrived while
GTK's thread stays free to receive the motion. Nothing is clicked until the
pointer has been seen to land on the spot: a click in the wrong place is
worse than none.
"""
import threading
import time

from gi.repository import GLib

from . import beeps, command_notices, gtk_loop, keyboard_output, virtual_pointer
from .logger import setup_logger
from .mouse_grid_model import Fit, Grid, close_enough
from .virtual_keyboard import GaveUp

logger = setup_logger(__name__)

# The grid closes by itself after this long with no grid command.
TIMEOUT_S = 60

# How long a move waits for the grid to see the pointer arrive, and how often
# it looks. The spike saw motion within a frame or two.
ARRIVAL_S = 0.3
POLL_S = 0.02

# Long enough for a hidden grid to be gone from the screen (about a frame),
# so the click lands on the app under it.
GONE_S = 0.05

# Where the pointer goes when the grid opens, as fractions of the screens'
# box: from where it lands there, the fit for every cell is worked out.
CALIBRATION = ((0.25, 0.25), (0.75, 0.75))

# A click request's action -> the button, how many clicks, and the word for the log.
_CLICKS = {"click": ("left", 1, "clicked"), "double click": ("left", 2, "double-clicked"),
           "right click": ("right", 1, "right-clicked"),
           "middle click": ("middle", 1, "middle-clicked")}


class _PointerFailed(Exception):
    """The virtual pointer couldn't move or click (virtual_pointer logged why)."""


class _Session:
    """One grid on the screen, from "mouse grid" until it closes."""

    def __init__(self, windows, grid: Grid, screens: list, beeps_on: bool):
        self.windows = windows
        self.grid = grid
        self.screens = screens          # the monitors' rectangles, for the log
        self.beeps_on = beeps_on
        self.fit: Fit | None = None
        self.level = 1
        self.zoomed_into: int | None = None     # the cell the last zoom went into
        self.timer = None               # the GLib source of the timeout; GTK's thread only


_lock = threading.Lock()
_session: _Session | None = None


def showing() -> bool:
    """Whether a grid is on the screen, so numbers and "click" are its commands."""
    return _session is not None


def act(request, beeps_on: bool) -> bool | None:
    """Do the mouse_phrases.MouseRequest *request*: True when done, False when
    refused with a note (or the grid went first), None for a grid word with no
    grid any more (it closed a moment ago), to be typed as words."""
    try:
        return _act(request, beeps_on)
    except _PointerFailed:
        close("the virtual pointer failed")
        command_notices.say("pointer_failed", beeps_on)
        return False
    except GaveUp as stop:
        if stop.reason != "cancelled":      # Esc: esc_stopped logged it
            logger.warning(f"Mouse: not clicked: {stop.reason}")
            command_notices.say("click_key_held", beeps_on)
        return False


def close(reason: str) -> None:
    """Take the grid off the screen, if one shows. From any thread: after a
    click, on "cancel", at the timeout, on a real mouse click, for another
    dictation, at quitting."""
    _end(None, reason)


def _act(request, beeps_on: bool) -> bool | None:
    """act() itself: "mouse grid" opens one; without a grid only the plain
    clicks act; with one, the step it asks for, the minute starting again."""
    if request.action == "grid":
        return _open(beeps_on)
    session = _session
    if session is None:
        if request.action in _CLICKS and request.number is None:
            return _click_here(request.action, beeps_on)
        return None
    session.beeps_on = beeps_on
    if request.action == "cancel":
        _end(session, "cancel")
        beeps.beep(beeps_on, *beeps.CANCEL_BEEP)
        return True
    gtk_loop.call(_restart_timer, session)
    if request.action == "zoom":
        return _zoom(session, request.number)
    return _point(session, request)


def _end(only: _Session | None, reason: str) -> bool:
    """Close the grid showing, or only *only* if that is still the one
    showing. True when this closed it."""
    global _session
    with _lock:
        session = _session
        if session is None or (only is not None and session is not only):
            return False
        _session = None
    logger.info(f"Mouse grid closed: {reason}")
    gtk_loop.call(_take_down, session)
    return True


def _take_down(session: _Session) -> None:
    """On GTK's thread: the timer off and the windows gone."""
    if session.timer is not None:
        GLib.source_remove(session.timer)
        session.timer = None
    session.windows.hide()


def _restart_timer(session: _Session) -> None:
    """On GTK's thread: the minute starts again, unless the grid closed
    while the dictation worker was on its way here."""
    if _session is not session:
        return
    if session.timer is not None:
        GLib.source_remove(session.timer)
    session.timer = GLib.timeout_add_seconds(TIMEOUT_S, _timed_out, session)


def _timed_out(session: _Session) -> bool:
    """On GTK's thread, a minute after the last grid command: the grid goes."""
    session.timer = None            # this source ends with the False below
    if _end(session, f"no grid command for {TIMEOUT_S} s"):
        beeps.beep(session.beeps_on, *beeps.CANCEL_BEEP)
    return False


def _put_up(windows):
    """On GTK's thread: the grid over every monitor, as (grid, monitors), or
    None when there is no X display for its windows."""
    from . import mouse_grid_window
    screens = mouse_grid_window.monitors()
    grid = Grid(screens)
    return (grid, screens) if windows.show(grid) else None


def _open(beeps_on: bool) -> bool:
    """Show a fresh grid, a top-level one even if one shows, and measure how
    the pointer lands on it."""
    global _session
    from . import mouse_grid_window
    close("a new grid")
    windows = mouse_grid_window.GridWindows(lambda: close("a real mouse click"))
    shown = gtk_loop.call(_put_up, windows)
    if shown is None:
        command_notices.say("grid_needs_x", beeps_on)
        return False
    session = _Session(windows, *shown, beeps_on)
    with _lock:
        _session = session
    session.fit = _calibrate(session)
    if session.fit is None:
        if _end(session, "the pointer can't be placed exactly"):
            command_notices.say("cant_place", beeps_on)
        return False
    if _session is not session:         # a real click closed it meanwhile
        return False
    gtk_loop.call(_restart_timer, session)
    logger.info(f"Mouse grid: shown on {len(session.screens)} screen(s)")
    return True


def _calibrate(session: _Session) -> Fit | None:
    """The fit from screen points to the tablet's fractions, from where the
    pointer lands at the two CALIBRATION spots; None if it doesn't show up
    there or the landings can't be fitted. The plain mapping over the
    screens' box when both landings agree with it: it's exact, where the
    landings are rounded to whole pixels."""
    bounds = session.grid.bounds
    plain = [(bounds.x + fx * bounds.width, bounds.y + fy * bounds.height)
             for fx, fy in CALIBRATION]
    arrivals = []
    for fraction in CALIBRATION:
        arrived = _move_and_see(session, fraction)
        if arrived is None:
            logger.warning(f"Mouse grid: the pointer sent to {_xy(plain[len(arrivals)])} "
                           f"never showed up on the grid; screens {_layout(session)}")
            return None
        arrivals.append(arrived)
    if all(close_enough(p, a) for p, a in zip(plain, arrivals, strict=True)):
        return Fit.straight(bounds)
    logger.info(f"Mouse grid: the pointer lands off the plain mapping ({_xy(arrivals[0])}, "
                f"{_xy(arrivals[1])} for {_xy(plain[0])}, {_xy(plain[1])}); fitted to it")
    return Fit.measured(CALIBRATION, (arrivals[0], arrivals[1]))


def _move_and_see(session: _Session, fraction):
    """Move the pointer to *fraction* of the screens' box and return where the
    grid saw it land, or None if it never did. A move onto the spot the
    pointer is already on sends no motion, so with none in time it goes a
    pixel aside and back."""
    for route in ([fraction], [_aside(session, fraction), fraction]):
        since = time.monotonic()
        for spot in route:
            if not virtual_pointer.move_to(*spot):
                raise _PointerFailed
        if (arrived := _arrival(session, since)) is not None:
            return arrived
    return None


def _aside(session: _Session, fraction):
    """*fraction* moved a pixel to the right."""
    return (fraction[0] + 1 / session.grid.bounds.width, fraction[1])


def _move_again(session: _Session, target) -> None:
    """The pointer a pixel aside and back onto *target* once the grid is gone.
    COSMIC gives the window under the pointer its press, or its hover, only
    after the pointer has moved since the grid's window over it went."""
    fraction = session.fit.fraction(target)
    for spot in (_aside(session, fraction), fraction):
        if not virtual_pointer.move_to(*spot):
            raise _PointerFailed


def _arrival(session: _Session, since: float):
    """Where the grid last saw the pointer after monotonic time *since*, once
    it has stopped moving, waiting up to ARRIVAL_S; None if nothing came."""
    deadline = time.monotonic() + ARRIVAL_S
    seen = None
    while time.monotonic() < deadline:
        time.sleep(POLL_S)
        now = gtk_loop.call(session.windows.pointer_since, since)
        if now is not None and now == seen:
            break
        seen = now
    return seen


def _zoom(session: _Session, number: int) -> bool:
    result = session.grid.zoom(number)
    if result != "ok":
        command_notices.say(result, session.beeps_on, number=number)
        return False
    session.level += 1
    session.zoomed_into = number
    gtk_loop.call(session.windows.redraw, session.grid)
    logger.info(f"Mouse grid: zoomed into cell {number}, level {session.level}")
    return True


def _point(session: _Session, request) -> bool:
    """A click or a move in the grid: the pointer onto the cell's centre (the
    zoomed region's, with no number), seen there, then the grid gone and the
    click made."""
    target = session.grid.target(request.number)
    if isinstance(target, str):
        command_notices.say(target, session.beeps_on, number=request.number)
        return False
    if not _lands_on(session, target):
        if _end(session, "the pointer didn't land on the spot"):
            command_notices.say("cant_place", session.beeps_on)
        return False
    where = f"{_spot_name(session, request.number)} at {_xy(target)}"
    if not _end(session, request.action):
        return False                    # closed meanwhile: a real click, the timeout
    time.sleep(GONE_S)
    _move_again(session, target)
    if request.action == "move":
        logger.info(f"Mouse grid: moved to {where}")
    else:
        logger.info(f"Mouse grid: {_click(request.action)} {where}")
    beeps.beep(session.beeps_on, *beeps.READY_BEEP)
    return True


def _lands_on(session: _Session, target) -> bool:
    """Whether the pointer, sent to the screen point *target*, was seen to
    land within close_enough's few pixels of it; logs where when not."""
    arrived = _move_and_see(session, session.fit.fraction(target))
    if arrived is not None and close_enough(target, arrived):
        return True
    landed = _xy(arrived) if arrived else "nowhere on the grid"
    logger.warning(f"Mouse grid: not clicked: the pointer sent to {_xy(target)} landed at "
                   f"{landed}; screens {_layout(session)}")
    return False


def _click_here(action: str, beeps_on: bool) -> bool:
    """"left click" and the others with no grid: a click where the pointer is."""
    logger.info(f"Mouse: {_click(action)} where the pointer is")
    beeps.beep(beeps_on, *beeps.READY_BEEP)
    return True


def _click(action: str) -> str:
    """The click *action* asks for, where the pointer is, and its word for
    the log. Esc is looked at before each press (a double click has two)."""
    button, count, done = _CLICKS[action]
    if not virtual_pointer.click(button, count,
                                 stopped=lambda: keyboard_output.esc_stopped("clicked")):
        raise _PointerFailed
    return done


def _spot_name(session: _Session, number: int | None) -> str:
    """The spot clicked or moved to, for the log: "cell 5, level 2"."""
    if number:
        return f"cell {number}, level {session.level},"
    if session.zoomed_into:
        return f"the middle of cell {session.zoomed_into}, level {session.level},"
    return "the middle of the screen,"


def _xy(point) -> str:
    return f"{point[0]:.0f},{point[1]:.0f}"


def _layout(session: _Session) -> str:
    return " ".join(f"{r.width:g}x{r.height:g}+{r.x:g}+{r.y:g}" for r in session.screens)
