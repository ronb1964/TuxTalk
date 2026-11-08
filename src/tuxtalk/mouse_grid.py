"""Mouse by voice, step by step: "mouse grid" shows the numbered grid, a
number zooms into a cell, "left 3" and the like nudge the click dot, and
"click" or "move" puts the pointer there.

From the first number, the desktop's own zoom (desktop_zoom) shows the
chosen part about three times bigger, the pointer waiting where the view
shows the chosen part centred. Every way the grid closes puts that zoom
back, and a click or move puts it back first, so the click lands on the
normal view.

It runs on the dictation worker, like every voice command. The grid's
windows belong to GTK's thread, so each step on them goes through
gtk_loop.call, and the worker polls for where the pointer arrived while
GTK's thread stays free to receive the motion. Nothing is clicked until the
pointer has been seen to land on the spot: a click in the wrong place is
worse than none.
"""
import threading
import time
from typing import NamedTuple

from gi.repository import GLib

from . import (beeps, command_notices, delivery, desktop_zoom, gtk_loop, keyboard_output,
               virtual_pointer)
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

# How long after the desktop zooms in the pointer goes a pixel aside and back,
# for GNOME and Cinnamon, which aim the view on the pointer's next motion.
ZOOM_SETTLE_S = 0.15


class _Click(NamedTuple):
    """What a click request makes: the button, how many clicks, and its
    word for the log."""

    button: str
    count: int
    done: str


_CLICKS = {"click": _Click("left", 1, "clicked"),
           "double click": _Click("left", 2, "double-clicked"),
           "right click": _Click("right", 1, "right-clicked"),
           "middle click": _Click("middle", 1, "middle-clicked")}


class _PointerFailed(Exception):
    """The virtual pointer couldn't move or click (virtual_pointer logged why)."""


class _Session:
    """One grid on the screen, from "mouse grid" until it closes."""

    def __init__(self, windows, grid: Grid, beeps_on: bool, magnifier):
        self.windows = windows
        self.grid = grid
        self.beeps_on = beeps_on
        self.fit: Fit | None = None
        self.level = 1
        self.chosen_cell: int | None = None     # the number said last, the grid now inside it
        self.timer = None               # the GLib source of the timeout; GTK's thread only
        # The desktop's zoom (a desktop_zoom.Magnifier, or None where there's
        # none, or once put back), and whether it's zoomed in now or still
        # owes a put-back. Taken and changed under _lock: the grid closes
        # from any thread.
        self.magnifier = magnifier
        self.desktop_zoomed = False


_lock = threading.Lock()
_session: _Session | None = None


def showing() -> bool:
    """Whether a grid is on the screen, so numbers and "click" are its commands."""
    return _session is not None


def act(request, beeps_on: bool) -> bool:
    """Do the mouse_phrases.MouseRequest *request*: True when done, False when
    refused with a note, or with the cancel beep when the grid it was heard
    for closed first."""
    try:
        return _act(request, beeps_on)
    except _PointerFailed:
        close("the virtual pointer failed")
        command_notices.say("pointer_failed", beeps_on)
        return False
    except GaveUp as stop:
        close(f"not clicked: {stop.reason}")
        if stop.reason != "cancelled":      # Esc: esc_stopped logged it
            logger.warning(f"Mouse: not clicked: {stop.reason}")
            command_notices.say("click_key_held", beeps_on)
        return False


def close(reason: str) -> None:
    """Take the grid off the screen, if one shows. From any thread: after a
    click, on "cancel" or Esc, at the timeout, on a real mouse click, for
    another dictation, at quitting."""
    _end(None, reason)


def _act(request, beeps_on: bool) -> bool:
    """act() itself: "mouse grid" opens one; without a grid only the plain
    clicks act; with one, the step it asks for, the minute starting again."""
    if request.action == "grid":
        return _open(beeps_on)
    session = _session
    if session is None:
        if request.in_grid or request.action not in _CLICKS:
            return _grid_gone(request, beeps_on)
        return _click_here(request.action, beeps_on)
    session.beeps_on = beeps_on
    if request.action == "cancel":
        _end(session, "cancel")
        beeps.beep(beeps_on, *beeps.CANCEL_BEEP)
        return True
    if not gtk_loop.call(_restart_timer, session):
        return _grid_gone(request, beeps_on)
    if request.action == "zoom":
        return _zoom(session, request.number)
    if request.action == "nudge":
        return _nudge(session, request)
    return _point(session, request)


def _grid_gone(request, beeps_on: bool) -> bool:
    """A grid command whose grid closed after it was heard (the minute ran
    out, a real click): nothing is done, and above all no click wherever the
    pointer happens to be."""
    logger.info(f"Mouse grid: not done ({request.action}): the grid closed first")
    beeps.beep(beeps_on, *beeps.CANCEL_BEEP)
    return False


def _end(only: _Session | None, reason: str) -> bool:
    """Close the grid showing, or only *only* if that is still the one
    showing, and put the desktop's zoom back. True when this closed it."""
    global _session
    with _lock:
        session = _session
        if session is None or (only is not None and session is not only):
            return False
        _session = None
    logger.info(f"Mouse grid closed: {reason}")
    gtk_loop.call(_take_down, session)
    _unzoom(session)
    return True


def _unzoom(session: _Session) -> bool:
    """Put the desktop's zoom back, on the thread that asks (a D-Bus call
    or a settings write, never inside gtk_loop.call), and let go of the
    magnifier: another restore of it later could undo a newer zoom. Whether
    the desktop is back as it was; what couldn't be put back stays owed in
    desktop_zoom's restore file, for the next grid or the next start."""
    with _lock:
        magnifier, session.magnifier = session.magnifier, None
        zoomed, session.desktop_zoomed = session.desktop_zoomed, False
    if magnifier is None:
        return True
    if not magnifier.restore():
        logger.warning("Mouse grid: couldn't put the desktop zoom back")
        return False
    if zoomed:
        logger.info("Mouse grid: put the desktop zoom back")
    return True


def _take_down(session: _Session) -> None:
    """On GTK's thread: the timer off and the windows gone."""
    if session.timer is not None:
        GLib.source_remove(session.timer)
        session.timer = None
    session.windows.hide()


def _restart_timer(session: _Session) -> bool:
    """On GTK's thread: the minute starts again, unless the grid closed
    while the dictation worker was on its way here (False)."""
    if _session is not session:
        return False
    if session.timer is not None:
        GLib.source_remove(session.timer)
    session.timer = GLib.timeout_add_seconds(TIMEOUT_S, _timed_out, session)
    return True


def _timed_out(session: _Session) -> bool:
    """On GTK's thread, a minute after the last grid command: the grid goes."""
    session.timer = None            # this source ends with the False below
    if _end(session, f"no grid command for {TIMEOUT_S} s"):
        beeps.beep(session.beeps_on, *beeps.CANCEL_BEEP)
    return False


def _put_up(windows, beeps_on: bool, magnifier):
    """On GTK's thread: the grid over every monitor, and the one showing
    from here on, as (session, None); or (None, the command_notices kind
    saying why) when it can't be shown. Made the one showing in the same
    step, so a real click on it, which comes on this thread, always finds
    it to close."""
    global _session
    from . import mouse_grid_window
    grid = Grid(mouse_grid_window.monitors())
    refused = windows.show(grid)
    if refused:
        return None, refused
    session = _Session(windows, grid, beeps_on, magnifier)
    with _lock:
        _session = session
    return session, None


def _open(beeps_on: bool) -> bool:
    """Show a fresh grid, a top-level one even if one shows, and measure how
    the pointer lands on it."""
    from . import mouse_grid_window
    close("a new grid")
    magnifier = desktop_zoom.find()     # a D-Bus call on KDE: before the grid shows
    windows = mouse_grid_window.GridWindows(lambda: close("a real mouse click"))
    session, refused = gtk_loop.call(_put_up, windows, beeps_on, magnifier)
    if refused:
        command_notices.say(refused, beeps_on)
        return False
    session.fit = _calibrate(session)
    if session.fit is None:
        if _end(session, "the pointer can't be placed exactly"):
            command_notices.say("cant_place", beeps_on)
        return False
    if _session is not session:         # a real click closed it meanwhile
        return False
    gtk_loop.call(_restart_timer, session)
    logger.info(f"Mouse grid: shown on {len(session.grid.monitors)} screen(s)")
    return True


def _calibrate(session: _Session) -> Fit | None:
    """The fit from screen points to the tablet's fractions, from where the
    pointer lands at the grid's two calibration spots; None if it doesn't
    show up there or the landings can't be fitted. The plain mapping over the
    screens' box when both landings agree with it: it's exact, where the
    landings are rounded to whole pixels."""
    straight = Fit.straight(session.grid.bounds)
    spots = session.grid.calibration_spots()
    fractions = [straight.fraction(spot) for spot in spots]
    arrivals = []
    for spot, fraction in zip(spots, fractions, strict=True):
        arrived = _move_and_see(session, fraction)
        if arrived is None:
            if _session is not session:     # closed meanwhile (Esc, a real click)
                return None
            logger.warning(f"Mouse grid: the pointer sent to {_xy(spot)} never showed up "
                           f"on the grid; screens {_layout(session)}")
            return None
        arrivals.append(arrived)
    if all(close_enough(s, a) for s, a in zip(spots, arrivals, strict=True)):
        return straight
    logger.info(f"Mouse grid: the pointer lands off the plain mapping ({_xy(arrivals[0])}, "
                f"{_xy(arrivals[1])} for {_xy(spots[0])}, {_xy(spots[1])}); fitted to it")
    return Fit.measured((fractions[0], fractions[1]), (arrivals[0], arrivals[1]))


def _move_and_see(session: _Session, fraction):
    """Move the pointer to *fraction* of the screens' box and return where the
    grid saw it land, or None if it never did, or the grid closed first: then
    the pointer is the user's again and stays where they put it. A move onto
    the spot the pointer is already on sends no motion, so with none in time
    it goes a pixel aside and back."""
    for route in ([fraction], [_aside(session, fraction), fraction]):
        since = time.monotonic()
        if not _move_while_showing(session, route):
            return None
        if (arrived := _arrival(session, since)) is not None:
            return arrived
    return None


def _aside(session: _Session, fraction):
    """*fraction* moved a pixel to the right."""
    return (fraction[0] + 1 / session.grid.bounds.width, fraction[1])


def _move_again(session: _Session, target) -> None:
    """The pointer a pixel aside and back onto *target* once the grid is gone,
    and again before each press of a click, which may have waited while the
    real mouse moved it. COSMIC gives the window under the pointer its press,
    or its hover, only after the pointer has moved since the grid's window
    over it went."""
    _move_through(_aside_and_back(session, target))


def _aside_and_back(session: _Session, target):
    """The tablet fractions a pixel right of the screen point *target*, then
    on it: a motion for an app, or for a magnifier that aims its view on
    the pointer's next motion, even when the pointer is already there."""
    fraction = session.fit.fraction(target)
    return [_aside(session, fraction), fraction]


def _move_through(fractions) -> None:
    for spot in fractions:
        if not virtual_pointer.move_to(*spot):
            raise _PointerFailed


def _move_while_showing(session: _Session, fractions) -> bool:
    """Move the pointer through *fractions* only while *session*'s grid
    shows, holding _lock so it can't close midway; False when it had
    closed: the pointer is the user's again, and isn't moved."""
    with _lock:
        if _session is not session:
            return False
        _move_through(fractions)
    return True


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
    """A number: the grid goes into that cell, and the desktop zooms in on
    it, or re-aims a zoom already on."""
    result = session.grid.zoom(number)
    if result != "ok":
        command_notices.say(result, session.beeps_on, number=number)
        return False
    session.level += 1
    session.chosen_cell = number
    gtk_loop.call(session.windows.redraw, session.grid)
    logger.info(f"Mouse grid: zoomed into cell {number}, level {session.level}")
    with _lock:         # read once: the grid closes from any thread
        if _session is not session:
            return True
        magnifier, zoomed = session.magnifier, session.desktop_zoomed
    if magnifier is None:
        return True
    spot = _park(session, magnifier.view)
    if spot is not None and not zoomed:
        _zoom_desktop(session, magnifier, spot)
    return True


def _park(session: _Session, view):
    """The pointer to where the zoomed desktop shows the part chosen centred
    on its monitor (desktop_zoom.parking_spot); that spot, or None when the
    grid closed first and the pointer stayed where the user has it."""
    grid = session.grid
    middle = grid.region.center
    monitor = next(m for m in grid.monitors if _holds(m, middle))
    spot = desktop_zoom.parking_spot(grid.region, monitor, grid.bounds, view)
    if not _move_while_showing(session, [session.fit.fraction(spot)]):
        return None
    return spot


def _holds(rect, point) -> bool:
    """Whether the rectangle *rect* holds the screen point *point*."""
    return (rect.x <= point[0] < rect.x + rect.width
            and rect.y <= point[1] < rect.y + rect.height)


def _zoom_desktop(session: _Session, magnifier, parked) -> None:
    """Zoom the desktop in, the pointer already parked, then move the pointer
    a pixel aside and back for the view to follow it. A zoom that fails
    leaves the grid as it was, unzoomed, unless part of it couldn't be undone:
    then it stays owed, so a click refuses and the close tries again. A grid
    that closed while it zoomed gets the zoom put back here, since its close
    found nothing to put back. A close during the settling wait leaves the
    pointer alone."""
    zoomed = magnifier.zoom_in()
    undone = zoomed or magnifier.restore()  # whatever a failed zoom couldn't undo itself
    with _lock:
        kept = session.magnifier is magnifier
        if kept:
            session.desktop_zoomed = zoomed or not undone
            if not session.desktop_zoomed:
                session.magnifier = None        # not tried again at every number
    if not zoomed:
        if undone:
            logger.warning("Mouse grid: the desktop didn't zoom in; the grid goes on without it")
        else:
            logger.warning("Mouse grid: the desktop didn't zoom in and a zoom is still owed; "
                           "no click until it's put back")
        return
    if not kept:
        if magnifier.restore():
            logger.info("Mouse grid: put the desktop zoom back")
        else:
            logger.warning("Mouse grid: couldn't put the desktop zoom back")
        return
    view = magnifier.view
    logger.info(f"Mouse grid: zoomed the desktop in {view.factor:.1f}x "
                f"({magnifier.name}, {view.tracking})")
    time.sleep(ZOOM_SETTLE_S)
    _move_while_showing(session, _aside_and_back(session, parked))


def _nudge(session: _Session, request) -> bool:
    """"left 3" and the like: the click dot moves; the pointer stays where
    it waits, so a zoomed view doesn't pan."""
    result = session.grid.nudge(request.direction, request.number)
    if result != "ok":
        command_notices.say(result, session.beeps_on)
        return False
    gtk_loop.call(session.windows.redraw, session.grid)
    logger.info(f"Mouse grid: moved the dot {request.direction} {request.number}, "
                f"to {_xy(session.grid.spot)}")
    return True


def _point(session: _Session, request) -> bool:
    """A click or a move in the grid: the pointer onto the cell's centre (with
    no number, the dot's spot) and seen there. The desktop's zoom goes back
    first, and with it still on nothing is done: the click would land where
    the user can't see it. A click then waits for a held dictation key with
    the grid still up, where a real click still cancels it. Then the grid
    goes, and the pointer is put back on the spot right before each press:
    the real mouse may have moved it."""
    target = session.grid.target(request.number)
    if isinstance(target, str):
        command_notices.say(target, session.beeps_on, number=request.number)
        return False
    if not _unzoom(session):    # first: the pointer then moves on the normal view
        if _end(session, "the desktop zoom couldn't be put back"):
            command_notices.say("zoom_not_back", session.beeps_on)
        return False
    if not _lands_on(session, target):
        if _end(session, "the pointer didn't land on the spot"):
            command_notices.say("cant_place", session.beeps_on)
        return False
    where = f"{_spot_name(session, request.number)} at {_xy(target)}"
    clicking = request.action != "move"
    if clicking:
        virtual_pointer.wait_for_dictation_keys()
    if not _end(session, request.action):
        return False                    # closed meanwhile: a real click, the timeout
    time.sleep(GONE_S)
    if clicking:
        _click(request.action, place=lambda: _move_again(session, target))
        logger.info(f"Mouse grid: {_CLICKS[request.action].done} {where}")
    else:
        if keyboard_output.esc_stopped("moved"):
            return False
        _move_again(session, target)
        logger.info(f"Mouse grid: moved to {where}")
    beeps.beep(session.beeps_on, *beeps.READY_BEEP)
    return True


def _lands_on(session: _Session, target) -> bool:
    """Whether the pointer, sent to the screen point *target*, was seen to
    land within close_enough's few pixels of it; logs where when not."""
    arrived = _move_and_see(session, session.fit.fraction(target))
    if arrived is not None and close_enough(target, arrived):
        return True
    if _session is not session:         # closed meanwhile: nothing to log
        return False
    landed = _xy(arrived) if arrived else "nowhere on the grid"
    logger.warning(f"Mouse grid: not clicked: the pointer sent to {_xy(target)} landed at "
                   f"{landed}; screens {_layout(session)}")
    return False


def _click_here(action: str, beeps_on: bool) -> bool:
    """"left click" and the others with no grid: a click where the pointer is."""
    _click(action)
    logger.info(f"Mouse: {_CLICKS[action].done} where the pointer is")
    beeps.beep(beeps_on, *beeps.READY_BEEP)
    return True


def _click(action: str, place=None) -> None:
    """Make the click *action* asks for, where the pointer is or where
    *place*() puts it before each press (virtual_pointer.click). Esc is
    looked at before each press (a double click has two)."""
    click = _CLICKS[action]
    # The click can put the cursor in other text, so "delete the last
    # sentence" must not erase from it; forgotten first, since even half a
    # double click can move the cursor.
    delivery.forget_typed()
    if not virtual_pointer.click(click.button, click.count, place=place,
                                 stopped=lambda: keyboard_output.esc_stopped("clicked")):
        raise _PointerFailed


def _spot_name(session: _Session, number: int | None) -> str:
    """The spot clicked or moved to, for the log: "cell 5, level 2"."""
    if number:
        return f"cell {number}, level {session.level},"
    if session.chosen_cell:
        grid = session.grid
        where = "the middle of" if grid.spot == grid.region.center else "the dot in"
        return f"{where} cell {session.chosen_cell}, level {session.level},"
    return "the middle of the screen,"


def _xy(point) -> str:
    return f"{point[0]:.0f},{point[1]:.0f}"


def _layout(session: _Session) -> str:
    return " ".join(f"{r.width:g}x{r.height:g}+{r.x:g}+{r.y:g}" for r in session.grid.monitors)
