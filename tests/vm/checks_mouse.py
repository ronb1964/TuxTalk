"""Mouse by voice (TuxTalk Labs, the "Apps, windows, keys and the mouse by voice"
tickbox): "mouse grid", a number or two, then "click". Only a real desktop
can say whether TuxTalk's virtual pointer lands where the grid shows, and
whether the click reaches the app under it.

The clicks go to a window of nine buttons, A to I (guest/sink.py's
"buttons"), which logs each press with where it landed. A check passes on
the press it can see: the right button, and within the grid's own 3 px of
the spot TuxTalk's log says it aimed at. Running on X11 (XWayland), that
window sees the pixels the grid uses on every desktop. What the VMs can't
judge is Ron's: two screens, his own screen scale, and how it feels.
"""
import contextlib
import json
import re
import time
from collections import namedtuple

import voices
from checks_desktop_commands import _said
from harness import Outcome, check, squash
from vmlib import REMOTE

SECTION = "Mouse by voice"
BUTTONS = "TT Buttons"
BUTTONS_FILE = "buttons"
# TuxTalk's own tolerance for a landing (mouse_grid_model.close_enough).
TOLERANCE_PX = 3
# How far a logged aim may be from the cell centre worked out here, as a
# fraction of the screen: the grid's pixels are rounded in the log.
AIM_TOLERANCE = 0.005
SHOWN = "Mouse grid: shown on"
ZOOMED = "Mouse grid: zoomed into cell"
# The step of mouse_grid_model.NUDGE_PX, and the log lines of the desktop zoom.
NUDGE_PX = 5
NUDGED = "Mouse grid: moved the dot"
DESKTOP_ZOOMED = "Mouse grid: zoomed the desktop in"
DESKTOP_UNZOOMED = "Mouse grid: put the desktop zoom back"
NOT_ZOOMED = "Mouse grid: the desktop didn't zoom in"
# Not an error: the user's own magnifier is a lens or part of the screen.
ZOOM_NOT_USED = "covers part of the screen; not used"
# Desktops whose zoom TuxTalk drives, and their magnifier's gsettings schema
# (KDE's has none to read: its Zoom effect shows in the log only).
ZOOM_DESKTOPS = ("kde", "gnome", "cinnamon")
MAGNIFIER_SCHEMAS = {"gnome": "org.gnome.desktop.a11y", "cinnamon": "org.cinnamon.desktop.a11y"}
ZOOM_FACTOR = 3.0
# The step of each phrase that is not a number: its log line.
STEP_LINES = {"mouse_right_four": NUDGED}
_STEP_DIRECTIONS = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}
# Where a plain "left click" is tried: inside button C of the maximized
# window on every VM's screen, panels and docks allowed for.
PLAIN_CLICK_AT = (0.8, 0.25)
SCALE = 1.25

Press = namedtuple("Press", "button letter x y")

_PRESS = re.compile(r"(?:(right|middle) )?([A-I]) (-?\d+),(-?\d+)")
_AIMED = re.compile(r"Mouse grid: (?:clicked|right-clicked|double-clicked|middle-clicked) "
                    r".*? at (-?\d+),(-?\d+)")
_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                 "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def parse_presses(text):
    """The test window's presses, oldest first, from its log file."""
    presses = []
    for line in (text or "").splitlines():
        if found := _PRESS.fullmatch(line.strip()):
            button, letter, x, y = found.groups()
            presses.append(Press(button or "left", letter, int(x), int(y)))
    return presses


def aimed_at(log):
    """Where the grid's last click aimed, from TuxTalk's log: (x, y) or None."""
    found = _AIMED.findall(log or "")
    return (int(found[-1][0]), int(found[-1][1])) if found else None


def click_problem(presses, button, letter, aim):
    """What is wrong with *presses* for one *button* click on *letter* at the
    point *aim* (None: wherever on that button); None when it is exactly that."""
    if not presses:
        return "no press reached the test window"
    if len(presses) > 1:
        return f"{len(presses)} presses reached the test window: {presses}"
    press = presses[0]
    if (press.button, press.letter) != (button, letter):
        return f"a {press.button} press landed on {press.letter}, not a {button} one on {letter}"
    if aim and (abs(press.x - aim[0]) > TOLERANCE_PX or abs(press.y - aim[1]) > TOLERANCE_PX):
        return (f"it landed at {press.x},{press.y}, more than {TOLERANCE_PX} px from "
                f"{aim[0]:.0f},{aim[1]:.0f}")
    return None


def aim_problem(aim, screen, fraction):
    """What is wrong with the grid aiming at *aim* on a *screen* (width,
    height) when it should aim at *fraction* of it; None when it does."""
    if aim is None:
        return "the log doesn't say where the grid clicked"
    off = [abs(aim[axis] / screen[axis] - fraction[axis]) for axis in (0, 1)]
    if max(off) > AIM_TOLERANCE:
        want = f"{fraction[0] * screen[0]:.0f},{fraction[1] * screen[1]:.0f}"
        return f"the grid aimed at {aim[0]},{aim[1]}, not at the cell's centre {want}"
    return None


def nudge_offset(direction, steps):
    """How many pixels (x, y) *steps* nudges to *direction* move the click dot."""
    dx, dy = _STEP_DIRECTIONS[direction]
    return dx * NUDGE_PX * steps, dy * NUDGE_PX * steps


def nudge_problem(aim, screen, fraction, offset):
    """What is wrong with the grid aiming at *aim* when it should be the
    *offset* pixels from *fraction* of a *screen*; None when it is."""
    if aim is None:
        return "the log doesn't say where the grid clicked"
    want = (fraction[0] * screen[0] + offset[0], fraction[1] * screen[1] + offset[1])
    if max(abs(aim[0] - want[0]), abs(aim[1] - want[1])) > TOLERANCE_PX:
        return (f"the grid aimed at {aim[0]},{aim[1]}, not at {want[0]:.0f},{want[1]:.0f}, "
                f"{offset[0]},{offset[1]} px from the part's centre")
    return None


def zoomed_in(log):
    """Whether the log says the grid zoomed the desktop in."""
    return DESKTOP_ZOOMED in (log or "")


def zoom_problem(log, expects_zoom):
    """What is wrong with the desktop zoom the *log* shows. A desktop where
    zoom is *expects_zoom* must have zoomed, unless the user's own magnifier
    is a lens; every zoom must be put back."""
    log = log or ""
    if NOT_ZOOMED in log:
        return "the desktop didn't zoom in, though the grid asked for it"
    zoomed = log.count(DESKTOP_ZOOMED)
    if not zoomed:
        if expects_zoom and ZOOM_NOT_USED not in log:
            return "the desktop didn't zoom in; the log says nothing about a zoom"
        return None
    if log.count(DESKTOP_UNZOOMED) != zoomed or log.rfind(DESKTOP_UNZOOMED) < log.rfind(
            DESKTOP_ZOOMED):
        return "the desktop zoom was not put back after the click"
    return None


def parse_magnifier(enabled, factor):
    """(on, factor) from gsettings' answers for screen-magnifier-enabled and
    mag-factor, or None when it gave no answer (no such schema)."""
    try:
        return enabled.strip() == "true", float(factor.strip().split()[-1])
    except (ValueError, IndexError):
        return None


def magnifier_problem(before, during, after):
    """What is wrong with the magnifier settings read (on, factor) before
    the grid, while it was zoomed and after the click; None when it came on
    at the zoom factor and is exactly as it was after."""
    if before is None:
        return None
    if during != (True, ZOOM_FACTOR):
        return f"the magnifier wasn't on at {ZOOM_FACTOR:g}x while the grid was zoomed: {during}"
    if after != before:
        return f"the magnifier was not put back: it was {before} and is {after}"
    return None


def same_words(heard, said):
    """Whether Parakeet heard the words played, a number as a word or digits."""
    def plain(text):
        words = re.sub(r"[^\w\s]", "", (text or "").lower()).split()
        return squash("".join(_NUMBER_WORDS.get(word, word) for word in words))
    return plain(heard) == plain(said)


# --- what the checks do --------------------------------------------------------


@contextlib.contextmanager
def _mouse_on(run):
    """Mouse by voice (the Open apps tickbox) on for the block, with what
    Parakeet heard in the log, as the VM had them afterwards."""
    with run.settings_put_back("open_apps", "log_transcripts"):
        run.set_config(open_apps=True, log_transcripts=True)
        yield


@contextlib.contextmanager
def _buttons(run):
    """The buttons window open, in front and with no presses for the block,
    then closed: the next check's click in the middle of the screen must not
    land on it."""
    run.sink_ready(name=BUTTONS_FILE, title=BUTTONS, fresh=True, mode="buttons")
    # sink_ready's click in the middle pressed E.
    run.vm.ssh(f"kill -USR1 $(cat ~/{REMOTE}/{BUTTONS_FILE}.txt.pid)")
    time.sleep(0.5)
    try:
        yield
    finally:
        run.close_sink(BUTTONS_FILE)


def _presses(run):
    return parse_presses(run.sink_text(BUTTONS_FILE))


def _screen(run):
    """The buttons window's root size (width, height), the pixels presses are in."""
    width, height = run.vm.read(f"~/{REMOTE}/{BUTTONS_FILE}.txt.screen").split()
    return int(width), int(height)


def _step(run, phrase_id, line):
    """Say *phrase_id* and wait for the log line *line*: (the log since,
    None) when it came, else (the log, the Outcome: "misheard" when Parakeet
    heard other words, a fail when it heard them and TuxTalk didn't act)."""
    log, heard = _said(run, phrase_id, line)
    if line in log:
        return log, None
    said = voices.load_phrases()[phrase_id]["say"]
    if heard is not None and not same_words(heard, said):
        return log, Outcome("misheard", f"the computer voice's “{said}” was heard as "
                            f"“{heard}”", heard=heard)
    seen = [entry.split(" - ")[-1] for entry in log.splitlines()
            if "Mouse" in entry or "Voice command" in entry or "Text injected" in entry]
    return log, Outcome("fail", f"“{said}”: no “{line}” in the log; it says {seen[-4:]}",
                        heard=heard)


def _grid_click(run, phrase_ids, line, press, fraction, shot_name, offset=None,
                while_zoomed=None):
    """Open the grid over the buttons, say *phrase_ids* (numbers that zoom
    and nudges, then the click, whose log line starts with *line*) and judge
    the *press* (button, letter) seen against where the grid aimed, which
    must be *fraction* of the screen, plus *offset* pixels after a nudge.
    *while_zoomed* is called once the steps before the click are done.
    (Outcome that stops the check or None, what it saw, the screenshot of
    the grid at its last level)."""
    with _buttons(run):
        screen = _screen(run)
        _log, bad = _step(run, "mouse_grid", SHOWN)
        if bad:
            return bad, None, None
        shot = run.shot(f"{shot_name}-grid")
        *zooms, click = phrase_ids
        for phrase_id in zooms:
            _log, bad = _step(run, phrase_id, STEP_LINES.get(phrase_id, ZOOMED))
            if bad:
                bad.screenshot = shot
                return bad, None, shot
        if while_zoomed:
            while_zoomed()
        if zooms:
            shot = run.shot(f"{shot_name}-zoomed")
        log, bad = _step(run, click, line)
        if bad:
            bad.screenshot = shot
            return bad, None, shot
        time.sleep(0.5)
        presses = _presses(run)
        aim = aimed_at(log)
        aim_wrong = (nudge_problem(aim, screen, fraction, offset) if offset
                     else aim_problem(aim, screen, fraction))
        if problem := aim_wrong or click_problem(presses, *press, aim):
            return Outcome("fail", problem, screenshot=run.shot(f"{shot_name}-after")), \
                None, shot
    saw = presses[0]
    return None, (f"{saw.button} press on {saw.letter} at {saw.x},{saw.y}; the grid aimed at "
                  f"{aim[0]},{aim[1]} on a {screen[0]} x {screen[1]} screen"), shot


def _grid_check(run, phrase_ids, line, press, fraction, shot_name):
    with _mouse_on(run):
        bad, saw, shot = _grid_click(run, phrase_ids, line, press, fraction, shot_name)
    return bad or Outcome("pass", saw, screenshot=shot)


@check(SECTION, "mouse_grid_click", "“mouse grid”, then “click five”, clicks the middle button")
def mouse_grid_click(run):
    return _grid_check(run, ["mouse_click_five"], "Mouse grid: clicked cell 5,",
                       ("left", "E"), (0.5, 0.5), "mouse-click-five")


@check(SECTION, "mouse_grid_zoom",
       "“mouse grid”, “seven”, “three”, “click” clicks inside the zoomed cell")
def mouse_grid_zoom(run):
    return _grid_check(run, ["mouse_seven", "mouse_three", "mouse_click"],
                       "Mouse grid: clicked the middle of cell 3, level 3,",
                       ("left", "G"), (5 / 18, 13 / 18), "mouse-zoom")


def _magnifier(run):
    """The desktop magnifier's (on, factor) in the VM, None where there is
    none to read (KDE, other desktops)."""
    schema = next((MAGNIFIER_SCHEMAS[t] for t in run.vm.spec.tags if t in MAGNIFIER_SCHEMAS),
                  None)
    if schema is None:
        return None

    def get(schema_id, key):
        return run.vm.session("gsettings", "get", schema_id, key, check=False).stdout
    return parse_magnifier(get(f"{schema}.applications", "screen-magnifier-enabled"),
                           get(f"{schema}.magnifier", "mag-factor"))


@check(SECTION, "mouse_grid_desktop_zoom",
       "The desktop zooms in for a small cell and is put back after the click")
def mouse_grid_desktop_zoom(run):
    expects = bool(set(ZOOM_DESKTOPS) & set(run.vm.spec.tags))
    before, during, mark = _magnifier(run), [], run.log_mark()

    def read_while_zoomed():
        run.wait_log(mark, DESKTOP_ZOOMED, NOT_ZOOMED, "Desktop zoom:", timeout=10)
        time.sleep(1)
        during.append(_magnifier(run))
    with _mouse_on(run):
        bad, saw, shot = _grid_click(
            run, ["mouse_seven", "mouse_three", "mouse_click"],
            "Mouse grid: clicked the middle of cell 3, level 3,", ("left", "G"),
            (5 / 18, 13 / 18), "mouse-desktop-zoom", while_zoomed=read_while_zoomed)
    if bad:
        return bad
    log = run.log_since(mark)
    if problem := zoom_problem(log, expects):
        return Outcome("fail", problem, screenshot=shot)
    if not zoomed_in(log):
        return Outcome("pass", f"no desktop zoom here; the grid still clicked: {saw}",
                       screenshot=shot)
    time.sleep(1)
    if problem := magnifier_problem(before, during[0], _magnifier(run)):
        return Outcome("fail", problem, screenshot=shot)
    return Outcome("pass", f"The desktop zoomed in {ZOOM_FACTOR:g}x and was put back; {saw}",
                   screenshot=shot)


@check(SECTION, "mouse_grid_nudge",
       "“right four” moves the click dot 20 px, and “click” lands there")
def mouse_grid_nudge(run):
    with _mouse_on(run):
        bad, saw, shot = _grid_click(
            run, ["mouse_seven", "mouse_right_four", "mouse_click"],
            "Mouse grid: clicked the dot in cell 7, level 2,", ("left", "G"), (1 / 6, 5 / 6),
            "mouse-nudge", offset=nudge_offset("right", 4))
    return bad or Outcome("pass", saw, screenshot=shot)


@check(SECTION, "mouse_right_click", "“right click five” on the grid opens a context menu")
def mouse_right_click(run):
    return _grid_check(run, ["mouse_right_click_five"], "Mouse grid: right-clicked cell 5,",
                       ("right", "E"), (0.5, 0.5), "mouse-right-click")


def _point(run, fraction):
    """The screen pixel at *fraction* of the VM's screen, as QEMU's tablet counts."""
    return round(fraction[0] * run.vm.spec.width), round(fraction[1] * run.vm.spec.height)


@check(SECTION, "mouse_cancel", "“cancel” takes the grid away and clicks nothing")
def mouse_cancel(run):
    with _mouse_on(run), _buttons(run):
        _log, bad = _step(run, "mouse_grid", SHOWN)
        if bad:
            return bad
        _log, bad = _step(run, "mouse_cancel", "Mouse grid closed: cancel")
        if bad:
            return bad
        time.sleep(0.5)
        clicked = _presses(run)
        shot = run.shot("mouse-cancelled")
        # With the grid still up, a real click would close it and not reach C.
        mark = run.log_mark()
        run.vm.click(*_point(run, PLAIN_CLICK_AT))
        time.sleep(1)
        after = _presses(run)
        if clicked:
            return Outcome("fail", f"“cancel” clicked: {clicked}", screenshot=shot)
        if "Mouse grid closed" in run.log_since(mark) or click_problem(after, "left", "C", None):
            return Outcome("fail", f"the grid was still up after “cancel”: a real click on C "
                           f"gave {after}", screenshot=shot)
        return Outcome("pass", "The grid closed, nothing was clicked, and a real click on C "
                       "reached the window", screenshot=shot)


@check(SECTION, "mouse_number_typed", "“five” with no grid on the screen is typed")
def mouse_number_typed(run):
    with _mouse_on(run):
        run.sink_ready(fresh=True)
        typed, heard, log = run.say("mouse_five")
        typed = (typed or "").strip()
        if "Mouse grid" in log:
            return Outcome("fail", "“five” with no grid acted on a grid", typed=typed)
        if same_words(typed, "five"):
            return Outcome("pass", expected="Five.", typed=typed, heard=heard)
        if heard is not None and not same_words(heard, "five"):
            return Outcome("misheard", "Parakeet heard different words from the ones played",
                           typed=typed, heard=heard)
        return Outcome("fail", "“five” wasn't typed", expected="Five.", typed=typed,
                       heard=heard)


@check(SECTION, "mouse_plain_click", "“left click” clicks where the pointer is")
def mouse_plain_click(run):
    with _mouse_on(run), _buttons(run):
        screen = _screen(run)
        x, y = _point(run, PLAIN_CLICK_AT)
        run.vm.move(x, y)
        time.sleep(0.5)
        _log, bad = _step(run, "mouse_left_click", "Mouse: clicked where the pointer is")
        if bad:
            return bad
        time.sleep(0.5)
        presses = _presses(run)
        at = (x * screen[0] / run.vm.spec.width, y * screen[1] / run.vm.spec.height)
        if problem := click_problem(presses, "left", "C", at):
            return Outcome("fail", f"QEMU put the pointer at {at[0]:.0f},{at[1]:.0f}; {problem}",
                           screenshot=run.shot("mouse-plain-click"))
        return Outcome("pass", f"Clicked C at {presses[0].x},{presses[0].y}, where QEMU had put "
                       "the pointer", screenshot=run.shot("mouse-plain-click"))


# --- KDE at 125% ---------------------------------------------------------------


def _kde_output(run):
    """The name of the KDE VM's one screen, as kscreen-doctor knows it."""
    answer = run.vm.session("kscreen-doctor", "-j", check=False).stdout
    outputs = json.loads(answer or "{}").get("outputs") or []
    return next((o["name"] for o in outputs if o.get("enabled")), None)


@contextlib.contextmanager
def _kde_scaled(run, output, scale):
    """The screen at *scale* for the block, then at 100% again."""
    run.vm.session("kscreen-doctor", f"output.{output}.scale.{scale}")
    time.sleep(4)
    try:
        yield
    finally:
        run.vm.session("kscreen-doctor", f"output.{output}.scale.1", check=False)
        time.sleep(4)


@check(SECTION, "mouse_grid_scaled", "At 125% screen scale the grid still clicks the spot",
       desktops=("kde",))
def mouse_grid_scaled(run):
    output = _kde_output(run)
    if output is None:
        return Outcome("error", "kscreen-doctor named no screen")
    seen, shot = [], None
    with _kde_scaled(run, output, SCALE), _mouse_on(run):
        for phrase_ids, line, press, fraction, name in (
                (["mouse_click_five"], "Mouse grid: clicked cell 5,", ("left", "E"),
                 (0.5, 0.5), "mouse-scaled-five"),
                (["mouse_seven", "mouse_three", "mouse_click"],
                 "Mouse grid: clicked the middle of cell 3, level 3,", ("left", "G"),
                 (5 / 18, 13 / 18), "mouse-scaled-zoom")):
            bad, saw, shot = _grid_click(run, phrase_ids, line, press, fraction, name)
            if bad:
                return bad
            seen.append(saw)
    return Outcome("pass", f"At {SCALE:.0%}: " + "; ".join(seen), screenshot=shot)
