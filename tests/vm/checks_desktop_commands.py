"""Desktop voice commands (TuxTalk Labs, the "Apps, windows, keys and the
mouse by voice" tickbox): "snap left", "centre window", "show desktop", "go
to desktop two", "move window to desktop two", "show all windows", "volume down", "mute sound",
"pause music", "lock screen", "take a screenshot", "what can I say" and
"minimize the text editor". Only a real desktop can say whether a window
landed where it was told, a desktop was reached, the volume moved, a lock
screen came up or a screenshot was saved.

Each check judges an effect it can see (the window's size and place, KWin's
or the X server's own answer, the sound server's volume, the pretend
player's calls, logind's LockedHint or Cinnamon's screensaver, a new file)
and never the absence of one. A desktop that can't do a command says so
with a note in the log: COSMIC can't place windows or switch desktops, X11
desktops have no overview, a one-screen VM has no other screen. Those are
checked as the note, and GNOME without the TuxTalk extension (which can't
tell which window has the focus) is skipped until the extension is there
(checks_extension.AGAIN). What the VM can't judge is Ron's part: how the
commands feel on his own desktop and screens.
"""
import contextlib
import json
import re
import shlex
import time
from collections import namedtuple

from checks_open_apps import opened_by_voice
from checks_send import PLAYER_LOG
from checks_type_at_pause import PAUSE_S
from checks_voice_actions import (
    _action_blocker, _kwin_windows, _no_editor, _not_a_command, _open_apps_on, _sink,
    _sink_state, _until, _wayland)
from harness import Abort, Outcome, check, heard_in
from vmlib import REMOTE

SECTION = "Desktop commands by voice"
SINK = "TT Sink"
# The desktops whose lockers are known to tell logind they locked. There,
# and on Cinnamon, the check needs the lock up and TuxTalk to have confirmed
# it; elsewhere TuxTalk's "not confirmed" note is the right answer to a lock
# logind isn't told of. Cinnamon's locker never sets LockedHint
# (cinnamon-screensaver has no SetLockedHint call), so there TuxTalk and the
# check ask the screensaver itself whether the lock screen is up.
LOCK_ANSWERS = ("kde", "gnome")
CINNAMON_SCREENSAVER = ("gdbus", "call", "--session", "--dest", "org.cinnamon.ScreenSaver",
                        "--object-path", "/org/cinnamon/ScreenSaver", "--method",
                        "org.cinnamon.ScreenSaver.GetActive")
# SetActive(false) unlocks it (the manager's unlock()): a lock asked of the
# screensaver may not be one logind's unlock reaches.
CINNAMON_UNLOCK = (*CINNAMON_SCREENSAVER[:-1], "org.cinnamon.ScreenSaver.SetActive", "false")
# GNOME Shell's own way to put its lock screen down.
GNOME_UNLOCK = ("gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver",
                "--object-path", "/org/gnome/ScreenSaver", "--method",
                "org.gnome.ScreenSaver.SetActive", "false")
UNLOCK_TRIES = 3
# TuxTalk waits up to 30 s for the portal (90 s for its picker, which the
# window check closes itself) and 10 s for its call; then the files are
# looked for one last time.
SCREENSHOT_WAIT_S = 50
# TuxTalk's log line for either screenshot command and what came of it.
SCREENSHOT_LINE = r"Voice command: take a screenshot(?: of the window)? \("
SCREENSHOTS = "~/Pictures"

# A window's place: x and y are None where the desktop doesn't tell an app.
Box = namedtuple("Box", "x y width height")


def parse_numbers(text, pattern):
    """The first group of *pattern* in *text* as an int, None when it isn't there."""
    found = re.search(pattern, text or "")
    return int(found.group(1)) if found else None


def within(value, total, low, high):
    """Whether *value* is between *low* and *high* of *total*."""
    return low <= value / total <= high


def parse_x11_desktops(text):
    """{window name: desktop number from 1} of `xprop -id ... _NET_WM_NAME
    _NET_WM_DESKTOP` blocks run one after the other. A window on all
    desktops (0xFFFFFFFF) is left out."""
    found, name = {}, None
    for line in text.splitlines():
        if line.startswith("_NET_WM_NAME"):
            name = line.partition("=")[2].strip().strip('"')
        elif line.startswith("_NET_WM_DESKTOP") and name is not None:
            number = parse_numbers(line, r"=\s*(\d+)")
            if number is not None and number < 0xFFFF:
                found[name] = number + 1
            name = None
    return found


def parse_output_volume(volume, muted=None):
    """(percent, muted) from `wpctl get-volume` ("Volume: 0.50 [MUTED]"), or
    from `pactl get-sink-volume` and `pactl get-sink-mute` ("... / 50% / ...",
    "Mute: yes") when *muted* is given. None when it isn't readable."""
    if muted is None:
        found = re.search(r"Volume:\s*([\d.]+)", volume)
        return (round(float(found[1]) * 100), "[MUTED]" in volume) if found else None
    found = re.search(r"/\s*(\d+)%\s*/", volume)
    return (int(found[1]), "yes" in muted) if found else None


def extension_absent(answer):
    """Whether gdbus's *answer* says GNOME Shell has no TuxTalk extension
    serving DesktopCommand (not installed, or the old one before a logout),
    as opposed to one that failed or didn't answer."""
    return any(f"org.freedesktop.DBus.Error.{name}" in answer
               for name in ("UnknownMethod", "UnknownObject", "UnknownInterface"))


def placed_at(box, spec, place):
    """Whether *box*'s corner is where a snap *place* puts it: (low, high)
    of the screen's width for x and of its height for y. True where the
    desktop doesn't tell an app where its window is (x is None)."""
    if box.x is None:
        return True
    (x_low, x_high), (y_low, y_high) = place
    return within(box.x, spec.width, x_low, x_high) and within(box.y, spec.height, y_low, y_high)


def overview_open(answer, desktop):
    """Whether the overview is open, from gdbus's answer: KWin's
    activeEffects ("(<['blur', 'overview']>,)") on KDE, GNOME Shell's
    OverviewActive ("(<true>,)") on GNOME. None when it isn't readable."""
    if desktop == "kde":
        found = re.search(r"\[(.*)\]", answer or "")
        return None if found is None else "'overview'" in found[1]
    found = re.search(r"<(true|false)>", answer or "")
    return None if found is None else found[1] == "true"


def screenshot_problem(files, sizes, log):
    """What is wrong with what "take a screenshot" left: no picture, TuxTalk
    not saying it saved one, two pictures (a second shot after the first may
    have been taken), or an empty one. None when it is exactly one saved
    picture."""
    if not files or not re.search(SCREENSHOT_LINE + "saved", log):
        said = [line for line in log.splitlines() if "screenshot" in line.lower()]
        return f"no new picture was saved; log: {said}"
    if len(files) > 1:
        return f"{len(files)} pictures were saved: a second screenshot was taken"
    if not all(size and int(size) > 0 for size in sizes):
        return "the saved picture is empty"
    return None


# --- what the checks read and say ---------------------------------------------


def _said(run, phrase_id, *done, timeout=30):
    """Say *phrase_id*, wait for the log to show one of *done*, a refusal or
    the words typed instead, and give (the log since, what Parakeet heard).
    The window in front is the caller's business."""
    _typed, _heard, mark = run.say(phrase_id, wait=False)
    run.wait_log(mark, *done, "Voice command not done", "Text injected", timeout=timeout)
    time.sleep(1.5)
    log = run.log_since(mark)
    return log, heard_in(log)


def _refused_with(run, phrase_id, note):
    """The Outcome of *phrase_id* said where the desktop can't do it: the
    log must hold the note *note* and nothing may be typed."""
    run.sink_ready(fresh=True)
    log, heard = _said(run, phrase_id, f"Voice command not done: {note}")
    if bad := _not_a_command(run, phrase_id, log, heard):
        return bad
    typed = run.sink_text().strip()
    shot = run.shot(f"{phrase_id}-note")
    if f"Voice command not done: {note}" not in log or typed:
        return Outcome("fail", f"expected the note “{note}” and nothing typed", typed=typed,
                       screenshot=shot)
    return Outcome("pass", f"This desktop can't do it, and TuxTalk said so (“{note}”); "
                   "nothing was typed", screenshot=shot)


def _session_tool(run, *command):
    """The standard output of *command* run inside the desktop session."""
    return run.vm.session(*command, check=False).stdout


# --- where windows are -------------------------------------------------------


def _sink_box(run):
    """The test window's Box: KWin's own on KDE (Wayland tells an app nothing
    of where its window is), else AT-SPI's, whose place is only known on X11.
    None when no size can be read."""
    if found := _kwin_windows(run, SINK):
        x, y, width, height = found[0]["geometry"]
        return Box(x, y, width, height)
    window = _sink(run)
    if not window or not window.get("size") or not all(window["size"]):
        return None
    x, y = window["position"] if "x11" in run.vm.spec.tags and window.get("position") \
        else (None, None)
    return Box(x, y, *window["size"])


def _focus_sink(run):
    """Give the test window the focus again after a command moved it, with a
    click inside the window itself: the middle of the screen can lie outside
    a snapped window. Where the window's place isn't known nothing is done."""
    window = _sink(run)
    if window is None or "active" in window["states"]:
        return
    box = _sink_box(run)
    if box and box.x is not None:
        run.vm.click(box.x + box.width // 2, box.y + box.height // 2)
        time.sleep(0.5)


def _grown_to(run, fits):
    """The test window's Box once *fits*(box) holds, within 8 seconds."""
    return _until(8, lambda: (box := _sink_box(run)) and fits(box) and box)


def _snap_step(run, phrase_id, line, wide, tall, place):
    """Say a snap and wait for the test window to reach the width and height
    ratios *wide* and *tall* (low, high) of the screen, at *place*
    (placed_at) where the desktop tells it. (Outcome that stops the check or
    None, what it saw)."""
    spec = run.vm.spec
    log, heard = _said(run, phrase_id, f"Voice command: {line}")
    if bad := _not_a_command(run, phrase_id, log, heard):
        return bad, None
    box = _grown_to(run, lambda b: within(b.width, spec.width, *wide)
                    and within(b.height, spec.height, *tall) and placed_at(b, spec, place))
    typed = run.sink_text().strip()
    now = _sink_box(run)
    shot = run.shot(phrase_id.replace("_", "-"))
    done = f"Voice command: {line}" in log
    if not done or typed or not box:
        size = f"{now.width} x {now.height} at {now.x}, {now.y}" if now else "unknown"
        return Outcome("fail", f"“{line}”: the window is {size} px on a {spec.width} x "
                       f"{spec.height} screen; typed {typed[:40]!r}; log: "
                       f"{'done' if done else 'not done'}", typed=typed,
                       screenshot=shot), None
    where = f" at {box.x}, {box.y}" if box.x is not None else " (its place can't be read)"
    return None, f"{line}: {box.width} x {box.height}{where}"


def _centred(box, spec):
    """Whether *box* sits in the middle of the screen, panels allowed for."""
    middle_x, middle_y = box.x + box.width / 2, box.y + box.height / 2
    return abs(middle_x - spec.width / 2) <= 0.08 * spec.width \
        and abs(middle_y - spec.height / 2) <= 0.12 * spec.height


def _centre_step(run, kept):
    """Say "centre window" and judge the result: the size is the *kept* Box's
    and, where the desktop tells the window's place, it is in the middle.
    (Outcome that stops the check or None, what it saw)."""
    spec = run.vm.spec
    log, heard = _said(run, "centre_window", "Voice command: center")
    if bad := _not_a_command(run, "centre_window", log, heard):
        return bad, None
    placed = kept.x is not None

    def fits(box):
        same = abs(box.width - kept.width) <= 0.05 * kept.width \
            and abs(box.height - kept.height) <= 0.05 * kept.height
        return same and (not placed or _centred(box, spec))
    box = _grown_to(run, fits)
    now = _sink_box(run)
    shot = run.shot("centre-window")
    if "Voice command: center" not in log or not box:
        return Outcome("fail", f"“centre window”: the window was {kept} and is now {now}",
                       screenshot=shot), None
    return None, ("center: size kept, in the middle" if placed else
                  "center: size kept (its place can't be read on this desktop)")


@check(SECTION, "snap_and_centre",
       "“snap left”, “snap top right” and “centre window” place the window")
def snap_and_centre(run):
    if why := run.window_blind():
        return Outcome("skip", why)
    with _open_apps_on(run):
        if "cosmic" in run.vm.spec.tags:
            return _refused_with(run, "snap_left", "cant_place_here")
        run.sink_ready(fresh=True)
        if _sink_box(run) is None:
            return Outcome("skip", "this desktop reports no window sizes through AT-SPI")
        seen = []
        top = (-0.02, 0.12)         # a panel at the top is allowed for
        for phrase_id, line, wide, tall, place in (
                ("snap_left", "snap left", (0.4, 0.6), (0.8, 1.02), ((-0.02, 0.1), top)),
                ("snap_top_right", "snap top right", (0.4, 0.6), (0.35, 0.6),
                 ((0.4, 0.6), top))):
            _focus_sink(run)
            bad, saw = _snap_step(run, phrase_id, line, wide, tall, place)
            if bad:
                return bad
            seen.append(saw)
        _focus_sink(run)
        bad, saw = _centre_step(run, _sink_box(run))
        if bad:
            return bad
        return Outcome("pass", "; ".join([*seen, saw]) + "; nothing typed")


# --- the desktop itself: show it, switch desktops --------------------------------

GDBUS_KWIN = ("gdbus", "call", "--session", "--dest", "org.kde.KWin", "--object-path")
KWIN_MANAGER = "org.kde.KWin.VirtualDesktopManager"
# KWin hands out ids as the property gives them, braces included on some versions.
UUID = r"\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}?"


def _kwin_call(run, path, method, *args):
    """KWin's answer to *method* of *path*, as gdbus prints it."""
    return _session_tool(run, *GDBUS_KWIN, path, "--method", method, *args)


def _x11_root(run, atom):
    """The number xprop reads from the X root window's *atom*, None when not."""
    return parse_numbers(_session_tool(run, "xprop", "-root", atom), r"=\s*(\d+)")


def _windows_hidden(run):
    """Whether the desktop is shown instead of the windows: KDE's
    showingDesktop, X11's _NET_SHOWING_DESKTOP, else the test window not
    showing with the focus (the most GNOME's Wayland tells an app)."""
    tags = run.vm.spec.tags
    if "kde" in tags:
        answer = _kwin_call(run, "/KWin", "org.freedesktop.DBus.Properties.Get",
                            "org.kde.KWin", "showingDesktop")
        return "true" in answer
    if "x11" in tags:
        return bool(_x11_root(run, "_NET_SHOWING_DESKTOP"))
    return bool(_sink_state(_sink(run)))


def _show_desktop_again(run):
    """Put the windows back: KDE directly, elsewhere with the same voice
    command."""
    if "kde" in run.vm.spec.tags:
        _kwin_call(run, "/KWin", "org.kde.KWin.showDesktop", "false")
    else:
        _said(run, "show_desktop", "Voice command: show desktop")


@check(SECTION, "show_desktop_toggles",
       "“show desktop” hides the windows, said again brings them back")
def show_desktop_toggles(run):
    if why := run.window_blind():
        return Outcome("skip", why)
    with _open_apps_on(run):
        if "cosmic" in run.vm.spec.tags:
            return _refused_with(run, "show_desktop", "cant_desktops_here")
        run.sink_ready(fresh=True)
        if _windows_hidden(run):
            return Outcome("skip", "the desktop was already shown before the check began")
        try:
            log, heard = _said(run, "show_desktop", "Voice command: show desktop")
            if bad := _not_a_command(run, "show_desktop", log, heard):
                return bad
            hidden = _until(8, lambda: _windows_hidden(run))
            shot = run.shot("show-desktop")
            log_again, _heard = _said(run, "show_desktop", "Voice command: show desktop")
            back = _until(8, lambda: not _windows_hidden(run))
        finally:
            if _windows_hidden(run):
                _show_desktop_again(run)
        problems = []
        if "Voice command: show desktop" not in log:
            problems.append("no “Voice command: show desktop” in the log")
        if not hidden:
            problems.append("the windows were still showing after “show desktop”")
        if "Voice command: show desktop" not in log_again:
            problems.append("said again, no “Voice command: show desktop” in the log")
        elif not back:
            problems.append("the windows didn't come back when it was said again")
        if problems:
            return Outcome("fail", "; ".join(problems), screenshot=shot)
        return Outcome("pass", "The windows were hidden, then back after saying it again",
                       screenshot=shot)


def _current_desktop(run):
    """The number from 1 of the desktop in use on KDE and X11; None on GNOME,
    where an app can't ask."""
    tags = run.vm.spec.tags
    if "kde" in tags:
        return parse_numbers(_kwin_call(run, "/KWin", "org.kde.KWin.currentDesktop"),
                             r"\((\d+),")
    if "x11" in tags:
        index = _x11_root(run, "_NET_CURRENT_DESKTOP")
        return None if index is None else index + 1
    return None


def _desktop_count(run):
    """How many desktops there are (KDE, X11); None on GNOME, which always
    keeps an empty one beyond the last in use."""
    if "kde" in run.vm.spec.tags:
        return parse_numbers(_kwin_call(run, "/VirtualDesktopManager",
                                        "org.freedesktop.DBus.Properties.Get", KWIN_MANAGER,
                                        "count"), r"uint32 (\d+)")
    if "x11" in run.vm.spec.tags:
        return _x11_root(run, "_NET_NUMBER_OF_DESKTOPS")
    return None


def _kwin_desktop_ids(run):
    """The ids of KDE's virtual desktops."""
    return set(re.findall(UUID, _kwin_call(run, "/VirtualDesktopManager",
                                           "org.freedesktop.DBus.Properties.Get",
                                           KWIN_MANAGER, "desktops")))


@contextlib.contextmanager
def _two_desktops(run):
    """Yields None once there are two desktops and the first is in use, else
    the reason why not (a str). KDE gets a second desktop made for the
    block, which is removed afterwards (the count must then be the one it
    began with); X11 desktops can't be added from here. Afterwards the first
    desktop is in use again."""
    _back_to_first_desktop(run)
    made, original = None, None
    try:
        original = count = _desktop_count(run)
        if count == 1 and "kde" in run.vm.spec.tags:
            before = _kwin_desktop_ids(run)
            _kwin_call(run, "/VirtualDesktopManager", f"{KWIN_MANAGER}.createDesktop", "1",
                       "'TT Two'")
            time.sleep(1)
            made = next(iter(_kwin_desktop_ids(run) - before), None)
            count = _desktop_count(run)
        if count is None and "gnome" not in run.vm.spec.tags:
            yield "the number of desktops couldn't be read"
        elif count is not None and count < 2:
            yield "this desktop has one desktop and none can be added from here"
        else:
            yield None
    finally:
        _back_to_first_desktop(run)
        if made:
            _kwin_call(run, "/VirtualDesktopManager", f"{KWIN_MANAGER}.removeDesktop",
                       f"'{made}'")
            time.sleep(1)
            if _desktop_count(run) != original:
                raise Abort(f"the desktop made for the check could not be removed: "
                            f"{_desktop_count(run)} desktops, there were {original}")


def _back_to_first_desktop(run):
    """The first desktop in use again: KDE directly; GNOME through the
    TuxTalk extension, as TuxTalk asks it; X11 by the voice command, and by
    wmctrl or xdotool when that was misheard."""
    if "gnome" in run.vm.spec.tags:
        _gnome_first_workspace(run)
        return
    if _current_desktop(run) in (None, 1):
        return
    if "kde" in run.vm.spec.tags:
        _kwin_call(run, "/KWin", "org.kde.KWin.setCurrentDesktop", "1")
        return
    _said(run, "go_to_desktop_one", "Voice command: desktop 1")
    if _current_desktop(run) not in (None, 1):
        _session_tool(run, "sh", "-c", "wmctrl -s 0 || xdotool set_desktop 0")
        time.sleep(1)
        if _current_desktop(run) not in (None, 1):
            raise Abort("the first desktop could not be brought back (no wmctrl or xdotool "
                        "on this VM, and the voice command wasn't heard)")


def _gnome_desktop_command(run, action, number):
    """The TuxTalk extension's answer to DesktopCommand(*action*, *number*),
    asked directly, as TuxTalk asks it."""
    return _session_tool(run, "gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                         "--object-path", "/io/github/ronb1964/TuxTalk/Extension",
                         "--method", "io.github.ronb1964.TuxTalk.Extension.DesktopCommand",
                         action, str(number))


def _gnome_first_workspace(run):
    """GNOME: the first workspace in use, asked of the TuxTalk extension
    directly (GNOME tells an app no workspace number), with the test window
    on it showing with the focus. A check that stopped after moving the
    window to the second one left it there: it is fetched back. Abort when
    it doesn't show, as every later check would run on the wrong workspace."""
    answer = _gnome_desktop_command(run, "desktop", 1)
    if extension_absent(answer):
        return      # no extension, so no command can have switched the workspace
    if _sink(run) is None:
        if "done" in answer:
            return  # switched, with no test window to look at
        raise Abort(f"the first GNOME workspace couldn't be asked for: {answer.strip()[:120]}")
    run.vm.click_centre()
    if _until(8, lambda: _sink_state(_sink(run)) is None):
        return
    _gnome_desktop_command(run, "desktop", 2)
    run.vm.click_centre()
    if _until(5, lambda: _sink_state(_sink(run)) is None):
        _gnome_desktop_command(run, "to desktop", 1)
        _gnome_desktop_command(run, "desktop", 1)
        run.vm.click_centre()
    if not _until(8, lambda: _sink_state(_sink(run)) is None):
        raise Abort("the first GNOME workspace could not be brought back: the test window "
                    "doesn't show with the focus")


def _sink_desktop(run):
    """The number from 1 of the desktop the test window is on: KWin's answer
    on KDE, the window manager's on X11; None on GNOME."""
    tags = run.vm.spec.tags
    if "kde" in tags:
        found = _kwin_windows(run, SINK)
        return found[0]["desktop"] if found else None
    if "x11" in tags:
        script = ('for id in $(xprop -root _NET_CLIENT_LIST | grep -o "0x[0-9a-fA-F]*"); do '
                  'xprop -id "$id" _NET_WM_NAME _NET_WM_DESKTOP; done')
        return parse_x11_desktops(_session_tool(run, "sh", "-c", script)).get(SINK)
    return None


def _arranged(run, current, on):
    """Whether the desktop in use is *current* and the test window is on
    desktop *on*. On GNOME, which tells an app neither, the window being the
    active one stands for "it is on the desktop in use"."""
    now = _current_desktop(run)
    if now is None:
        active = "active" in ((_sink(run) or {}).get("states") or [])
        return active == (current == on)
    return now == current and _sink_desktop(run) == on


def _desktop_step(run, phrase_id, line, current, on):
    """Say *phrase_id* and wait for desktop *current* in use with the test
    window on *on*. (Outcome that stops the check or None, what it saw)."""
    log, heard = _said(run, phrase_id, f"Voice command: {line}")
    if bad := _not_a_command(run, phrase_id, log, heard):
        return bad, None
    reached = _until(8, lambda: _arranged(run, current, on))
    typed = run.sink_text().strip()
    shot = run.shot(phrase_id.replace("_", "-"))
    done = f"Voice command: {line}" in log
    if not done or typed or not reached:
        return Outcome("fail", f"“{line}”: desktop {_current_desktop(run)} in use, the window "
                       f"on {_sink_desktop(run)}, expected {current} and {on}; typed "
                       f"{typed[:40]!r}; log: {'done' if done else 'not done'}",
                       typed=typed, screenshot=shot), None
    if _current_desktop(run) is None:
        return None, (f"{line}: the test window {'has' if current == on else 'lost'} the "
                      "focus (GNOME tells an app no desktop number, so which one is in use "
                      "isn't verified)")
    return None, f"{line}: desktop {current} in use, window on {on}"


@check(SECTION, "switch_desktops", "“go to desktop two” and “one” change the desktop")
def switch_desktops(run):
    if why := run.window_blind():
        return Outcome("skip", why)
    with _open_apps_on(run):
        if "cosmic" in run.vm.spec.tags:
            return _refused_with(run, "go_to_desktop_two", "cant_desktops_here")
        with _two_desktops(run) as why:
            if why:
                return Outcome("skip", why)
            run.sink_ready(fresh=True)
            seen = []
            for phrase_id, line, current in (("go_to_desktop_two", "desktop 2", 2),
                                             ("go_to_desktop_one", "desktop 1", 1)):
                bad, saw = _desktop_step(run, phrase_id, line, current, 1)
                if bad:
                    return bad
                seen.append(saw)
            return Outcome("pass", "; ".join(seen) + "; nothing typed")


@check(SECTION, "move_window_to_desktop",
       "“move window to desktop two” sends the window there and back")
def move_window_to_desktop(run):
    if why := run.window_blind():
        return Outcome("skip", why)
    with _open_apps_on(run):
        if "cosmic" in run.vm.spec.tags:
            return _refused_with(run, "move_to_desktop_two", "cant_place_here")
        with _two_desktops(run) as why:
            if why:
                return Outcome("skip", why)
            run.sink_ready(fresh=True)
            seen = []
            # GNOME removes a workspace left empty and shows the one before:
            # moving the test window, alone on the second, back to the first
            # brings the person there with it.
            back_to = 1 if "gnome" in run.vm.spec.tags else 2
            for phrase_id, line, current, on in (
                    ("move_to_desktop_two", "to desktop 2", 1, 2),
                    ("go_to_desktop_two", "desktop 2", 2, 2),
                    ("move_to_desktop_one", "to desktop 1", back_to, 1),
                    ("go_to_desktop_one", "desktop 1", 1, 1)):
                if phrase_id == "move_to_desktop_one":
                    run.vm.click_centre()       # the window must have the focus to move
                    time.sleep(0.5)
                bad, saw = _desktop_step(run, phrase_id, line, current, on)
                if bad:
                    return bad
                seen.append(saw)
            return Outcome("pass", "; ".join(seen) + "; nothing typed")


@check(SECTION, "one_screen_note", "“move window to the other screen” on one screen says so")
def one_screen_note(run):
    if why := run.window_blind():
        return Outcome("skip", why)
    with _open_apps_on(run):
        if "cosmic" in run.vm.spec.tags:
            return _refused_with(run, "other_screen", "cant_place_here")
        run.sink_ready(fresh=True)
        log, heard = _said(run, "other_screen", "Voice command: to next screen",
                           "Voice command not done: one_screen")
        if bad := _not_a_command(run, "other_screen", log, heard):
            return bad
        typed = run.sink_text().strip()
        shot = run.shot("one-screen")
        if "Voice command: to next screen" in log:
            return Outcome("skip", "this VM has more than one screen; the window was moved",
                           screenshot=shot)
        if "Voice command not done: one_screen" not in log or typed:
            return Outcome("fail", "expected the note “one_screen” and nothing typed",
                           typed=typed, screenshot=shot)
        return Outcome("pass", "TuxTalk said there is only one screen; nothing was typed",
                       screenshot=shot)


@check(SECTION, "overview", "“show all windows” opens the overview (not on X11 or COSMIC)")
def overview(run):
    if why := run.window_blind():
        return Outcome("skip", why)
    with _open_apps_on(run):
        if "cosmic" in run.vm.spec.tags or "x11" in run.vm.spec.tags:
            return _refused_with(run, "show_all_windows", "no_overview_here")
        run.sink_ready(fresh=True)
        log, heard = _said(run, "show_all_windows", "Voice command: overview")
        if bad := _not_a_command(run, "show_all_windows", log, heard):
            return bad
        opened = _until(5, lambda: _overview_state(run) is not False)
        opened = _overview_state(run) if opened else False
        shot = run.shot("overview")
        run.vm.tap("esc")
        time.sleep(1)
        typed = run.sink_text().strip()
        done = "Voice command: overview" in log
        if not done or typed or opened is False:
            return Outcome("fail", f"expected the overview open and nothing typed; open: "
                           f"{opened}; log: {'done' if done else 'not done'}", typed=typed,
                           screenshot=shot)
        seen = ("the desktop says the overview was open" if opened else
                "whether it opened can't be read here (the screenshot shows it)")
        return Outcome("pass", f"TuxTalk asked for the overview; {seen}; Esc closed it",
                       screenshot=shot)


def _overview_state(run):
    """Whether the desktop says its overview is open: KWin's active effects
    on KDE, GNOME Shell's OverviewActive on GNOME; None when it can't be read."""
    desktop = "kde" if "kde" in run.vm.spec.tags else "gnome"
    if desktop == "kde":
        answer = _kwin_call(run, "/Effects", "org.freedesktop.DBus.Properties.Get",
                            "org.kde.kwin.Effects", "activeEffects")
    else:
        answer = _session_tool(run, "gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                               "--object-path", "/org/gnome/Shell", "--method",
                               "org.freedesktop.DBus.Properties.Get", "org.gnome.Shell",
                               "OverviewActive")
    return overview_open(answer, desktop)


# --- sound and music ---------------------------------------------------------


def _has(run, program):
    """Whether *program* is installed on the VM."""
    return not run.vm.ssh(f"command -v {program}", check=False).returncode


def _output(run):
    """(percent, muted) of the default output, read as TuxTalk reads it (wpctl,
    else pactl), None when neither can say."""
    if _has(run, "wpctl"):
        return parse_output_volume(
            run.vm.ssh("wpctl get-volume @DEFAULT_AUDIO_SINK@", check=False).stdout)
    return parse_output_volume(
        run.vm.ssh("pactl get-sink-volume @DEFAULT_SINK@", check=False).stdout,
        run.vm.ssh("pactl get-sink-mute @DEFAULT_SINK@", check=False).stdout)


def _set_output(run, percent, muted):
    """Set the default output's volume and mute."""
    if _has(run, "wpctl"):
        run.vm.ssh(f"wpctl set-volume @DEFAULT_AUDIO_SINK@ {percent / 100:.2f} && "
                   f"wpctl set-mute @DEFAULT_AUDIO_SINK@ {int(muted)}", check=False)
    else:
        run.vm.ssh(f"pactl set-sink-volume @DEFAULT_SINK@ {percent}% && "
                   f"pactl set-sink-mute @DEFAULT_SINK@ {int(muted)}", check=False)


@contextlib.contextmanager
def _output_restored(run):
    """The default output at 50 percent and not muted for the block, as it was
    afterwards. Yields False when its volume can't be read."""
    start = _output(run)
    if start is None:
        yield False
        return
    _set_output(run, 50, False)
    try:
        yield True
    finally:
        _set_output(run, *start)


def _close_to(run, percent, muted):
    """Whether the default output is at *percent* (one off is rounding)
    and muted or not as *muted* says."""
    now = _output(run)
    return now is not None and abs(now[0] - percent) <= 1 and now[1] == muted


@check(SECTION, "volume_commands",
       "“volume down”, “volume up three”, “mute sound” and “unmute sound” change the output")
def volume_commands(run):
    with _open_apps_on(run), _output_restored(run) as readable:
        if not readable:
            return Outcome("skip", "neither wpctl nor pactl can read the output's volume here")
        run.sink_ready(fresh=True)
        seen = []
        for phrase_id, line, percent, muted in (
                ("volume_down", "volume down", 45, False),
                ("volume_up_three", "volume up", 60, False),
                ("mute_sound", "mute", 60, True),
                ("unmute_sound", "unmute", 60, False)):
            # A volume change's log line names the new level, "Voice
            # command: volume down (45%)"; mute's and unmute's don't.
            marker = f"Voice command: {line}" + (" (" if line.startswith("volume") else "")
            log, heard = _said(run, phrase_id, marker)
            if bad := _not_a_command(run, phrase_id, log, heard):
                return bad
            reached = _until(5, lambda p=percent, m=muted: _close_to(run, p, m))
            typed = run.sink_text().strip()
            if marker not in log or typed or not reached:
                return Outcome("fail", f"“{line}”: the output is {_output(run)}, expected "
                               f"{(percent, muted)}; typed {typed[:40]!r}", typed=typed,
                               screenshot=run.shot(phrase_id.replace("_", "-")))
            seen.append(f"{line} -> {percent}%{' muted' if muted else ''}")
        return Outcome("pass", "; ".join(seen) + "; the volume was put back afterwards")


def _sink_inputs(run):
    """The playback streams pactl lists, as its JSON gives them; [] when it
    can't (pactl before 16 has no JSON output)."""
    out = run.vm.ssh("pactl -f json list sink-inputs", check=False).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def _stream_percent(stream):
    """A playback stream's volume in percent, None when it reports none."""
    channels = list((stream.get("volume") or {}).values())
    return parse_numbers(channels[0]["value_percent"], r"(\d+)") if channels else None


def _tone_stream(run):
    """(index, percent) of the tone's playback stream, None when not found."""
    for stream in _sink_inputs(run):
        if any("pw-play" in str(value) for value in stream.get("properties", {}).values()):
            return stream["index"], _stream_percent(stream)
    return None


def _stream_now(run, index):
    """The volume in percent of the playback stream *index* now, None when
    it has ended."""
    stream = next((s for s in _sink_inputs(run) if s["index"] == index), None)
    return None if stream is None else _stream_percent(stream)


@check(SECTION, "volume_kept_after_sounds_come_back",
       "“volume down” sticks when the other sounds come back up")
def volume_kept_after_sounds_come_back(run):
    with _open_apps_on(run), run.settings_put_back("duck_audio"), _output_restored(run) as ok:
        if not ok:
            return Outcome("skip", "neither wpctl nor pactl can read the output's volume here")
        run.set_config(duck_audio=True)
        run.sink_ready(fresh=True)
        run.vm.ssh(f"setsid -f timeout 25 pw-play ~/{REMOTE}/tone.wav "
                   "> /dev/null 2>&1 < /dev/null")
        time.sleep(1.5)
        try:
            stream = _tone_stream(run)
            if stream is None:
                return Outcome("skip", "the playing tone's stream couldn't be read (pactl's "
                               "JSON output)")
            index, start = stream
            _typed, _heard, mark = run.say("volume_down", wait=False)
            run.wait_log(mark, "sound(s) back", timeout=30)
            time.sleep(2)
            log = run.log_since(mark)
            output, back = _output(run), _stream_now(run, index)
        finally:
            run.vm.ssh("pkill -x pw-play", check=False)
    if bad := _not_a_command(run, "volume_down", log, heard_in(log)):
        return bad
    problems = []
    if not (parse_numbers(log, r"Turned (\d+) other sound") or 0) >= 1:
        problems.append("the playing sound was never turned down")
    if "Voice command: volume down" not in log:
        problems.append("no “Voice command: volume down” in the log")
    if output is None or abs(output[0] - 45) > 1:
        problems.append(f"the output is {output}, expected 45% (it was put back up)")
    if back is None or abs(back - start) > 2:
        problems.append(f"the stream's own volume is {back}%, it started at {start}%")
    if problems:
        return Outcome("fail", "; ".join(problems))
    return Outcome("pass", f"The output stayed at {output[0]}% and the stream is back at "
                   f"{back}% (it started at {start}%)")


PLAYER_BUS = ("gdbus", "call", "--session", "--dest", "org.mpris.MediaPlayer2.ttplayer",
              "--object-path", "/org/mpris/MediaPlayer2", "--method")


@contextlib.contextmanager
def _fake_player(run, status):
    """The pretend player (guest/fake_player.py) on the session bus in *status*
    for the block, its calls in PLAYER_LOG; ended afterwards."""
    run.vm.ssh(f"rm -f {PLAYER_LOG}")
    run.vm.session_background("player", "python3", f"{run.vm.home}/{REMOTE}/fake_player.py",
                              f"{run.vm.home}/{REMOTE}/player.txt", status)
    time.sleep(2)
    try:
        yield
    finally:
        run.vm.ssh("kill $(pgrep -f '[f]ake_player.py')", check=False)


def _player_status(run):
    """"Playing" or "Paused", as the pretend player says."""
    out = _session_tool(run, *PLAYER_BUS, "org.freedesktop.DBus.Properties.Get",
                        "org.mpris.MediaPlayer2.Player", "PlaybackStatus")
    found = re.search(r"'(\w+)'", out)
    return found[1] if found else None


def _player_calls(run):
    """Every call the pretend player has had so far."""
    return run.vm.read(PLAYER_LOG).split()


def _music_step(run, phrase_id, line, judge):
    """Say a music command and judge it by the calls the player had since
    (*judge*(calls, status) -> a problem str or None), after the 3 seconds
    TuxTalk would take to undo it. (Outcome that stops the check or None,
    what it saw)."""
    before = len(_player_calls(run))
    run.sink_ready(fresh=True)
    log, heard = _said(run, phrase_id, f"Voice command: {line}")
    if bad := _not_a_command(run, phrase_id, log, heard):
        return bad, None
    time.sleep(3)
    calls, status = _player_calls(run)[before:], _player_status(run)
    problem = judge(calls, status) or (
        None if f"Voice command: {line}" in log else "no line for it in the log")
    if problem:
        return Outcome("fail", f"“{line}”: {problem} (calls {calls}, status {status})",
                       screenshot=run.shot(phrase_id.replace("_", "-"))), None
    return None, f"{line}: {calls}, {status}"


def _pause_problem(calls, status):
    """What is wrong with the player's calls after "pause music": it must
    end Paused with at most the hold key's Pause, Play, Pause."""
    if calls not in (["Pause"], ["Pause", "Play", "Pause"]) or status != "Paused":
        return "it should end Paused after one Pause (or Pause, Play, Pause)"
    return None


def _pause_music_at_a_pause(run):
    """Hold F8 and say a sentence, then "pause music", each followed by a
    pause, and let go only once "pause music" was acted on: (typed, log).
    A phrase said right before the key comes up is the recording's last,
    read after the music came back with the key, and then paused again (the
    hold key's accepted blip); one cut at a pause is acted on during the
    recording, where TuxTalk must forget it was going to resume the player."""
    run.sink_ready(fresh=True)
    mark = run.log_mark()
    run.vm.key_down("f8")
    try:
        time.sleep(0.4)
        run.play("basic")
        time.sleep(PAUSE_S)
        run.play("pause_music")
        run.wait_log(mark, "Voice command: pause music", "Voice command not done", timeout=15)
    finally:
        run.vm.key_up("f8")
    typed = run.wait_dictation(mark)
    return typed or "", run.log_since(mark)


@check(SECTION, "music_commands", "“pause music”, “play music” and “next track” reach the player")
def music_commands(run):
    names = _session_tool(run, "gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                          "--object-path", "/org/freedesktop/DBus", "--method",
                          "org.freedesktop.DBus.ListNames")
    if others := sorted(set(re.findall(r"org\.mpris\.MediaPlayer2\.[\w.-]+", names))):
        return Outcome("skip", f"another media player is on the bus ({', '.join(others)}); "
                       "TuxTalk would pause it too")
    with _open_apps_on(run):
        with _fake_player(run, "Playing"):
            seen = []
            for phrase_id, line, judge in (
                    ("pause_music", "pause music", _pause_problem),
                    ("play_music", "play music",
                     lambda calls, status: None if status == "Playing" and "Play" in calls
                     else "it should be Playing after a Play"),
                    ("next_track", "next music",
                     lambda calls, _status: None if "Next" in calls else "no Next call")):
                bad, saw = _music_step(run, phrase_id, line, judge)
                if bad:
                    return bad
                seen.append(saw)
        with _fake_player(run, "Playing"), run.settings_put_back("chunked_dictation"):
            run.set_config(chunked_dictation=True)
            typed, log = _pause_music_at_a_pause(run)
            time.sleep(3)
            status = _player_status(run)
        if "Voice command: pause music" not in log:
            return Outcome("fail", "as a phrase of a Type at pause recording “pause music” "
                           "was not taken as a command", typed=typed)
        if status != "Paused" or "Resumed" in log:
            return Outcome("fail", f"after the Type at pause recording the player is {status}"
                           f"{', and TuxTalk resumed it' if 'Resumed' in log else ''}")
        seen.append("pause music in a Type at pause recording: Paused, never resumed")
        return Outcome("pass", "; ".join(seen))


# --- the system: lock, screenshot, the list of commands ---------------------


def _session_id(run):
    """This login session's id: the one the desktop's own programs run in.
    GNOME 46's shell runs as a systemd service without XDG_SESSION_ID, so
    logind's graphical session of the user is asked for. Never the first
    row of list-sessions: it sorts the ids as text, so the ssh session the
    command runs in ("17") can come before the desktop's ("4"), and its
    LockedHint is never "yes"."""
    sid = _session_tool(run, "printenv", "XDG_SESSION_ID").strip()
    return sid or run.vm.ssh("loginctl show-user $(id -un) -p Display --value",
                             check=False).stdout.strip()


def _locked(run, session):
    """What logind says of the session's LockedHint: "yes", "no" or ""."""
    return run.vm.ssh(f"loginctl show-session {session} -p LockedHint --value",
                      check=False).stdout.strip()


def _screen_locked(run, session):
    """Whether the lock screen is up: logind's LockedHint, or on Cinnamon,
    whose locker never sets it, the screensaver's own answer."""
    if "cinnamon" in run.vm.spec.tags:
        return "(true,)" in _session_tool(run, *CINNAMON_SCREENSAVER)
    return _locked(run, session) == "yes"


def _new_screenshots(run, marker):
    """The screenshot files made in the home folder since *marker* was touched
    (Spectacle and GNOME both name theirs Screenshot...)."""
    out = run.vm.ssh(f"find ~ -maxdepth 4 -type f -newer {marker} -iname 'screenshot*' "
                     "\\( -iname '*.png' -o -iname '*.jpg' \\) -not -path '*/.*' "
                     "2>/dev/null", check=False)
    return [line for line in out.stdout.splitlines() if line]


def screenshot_settled(log):
    """Whether "take a screenshot" can't save anything more: TuxTalk said it
    saved one, or gave a note, or typed the words. Not "not confirmed": the
    portal's request is still open then, and a picture can come later."""
    if "screenshot_unconfirmed" in log or re.search(SCREENSHOT_LINE + "unconfirmed", log):
        return False
    return bool(re.search(SCREENSHOT_LINE, log)) or any(
        line in log for line in ("Voice command not done", "Text injected"))


# The portal's permission for unsandboxed apps whose app id it can't tell,
# TuxTalk included ("Allow Apps to Take Screenshots?"): table, id, app.
SCREENSHOT_PERMISSION = ("screenshot", "screenshot", "")
PERMISSION_STORE = ("gdbus", "call", "--session", "--dest", "org.freedesktop.impl.portal."
                    "PermissionStore", "--object-path", "/org/freedesktop/impl/portal/"
                    "PermissionStore", "--method")


def _allow_screenshots(run):
    """Answer the screenshot portal's one-time question ahead, as Allow
    would: GNOME's and COSMIC's dialog held the keyboard for every check
    after it (the dictation service didn't start again under GNOME's; COSMIC's
    took "Pause." and kept the test window from the front), and nothing
    in the VM can click it. Whether the question shows is Ron's check."""
    table, permission_id, app = SCREENSHOT_PERMISSION
    _session_tool(run, *PERMISSION_STORE, "org.freedesktop.impl.portal.PermissionStore."
                  "SetPermission", table, "true", permission_id, repr(app), "['yes']")


def _png_size(run, path):
    """(width, height) of the PNG at *path* in the VM, None when unreadable."""
    reader = ("import struct, sys; head = open(sys.argv[1], 'rb').read(24); "
              "print(*struct.unpack('>II', head[16:24]))")
    out = run.vm.ssh(f"python3 -c {shlex.quote(reader)} {shlex.quote(path)}",
                     check=False).stdout.split()
    return (int(out[0]), int(out[1])) if len(out) == 2 else None


def _pictures_from(run, phrase_id, shot_name):
    """Say *phrase_id* and wait until TuxTalk can save nothing more (Escape
    at the deadline, for a question still showing). (the new pictures'
    files, their sizes in bytes, their (width, height), the log since, the
    report's screenshot); the pictures are removed again."""
    marker = f"~/{REMOTE}/shot.marker"
    folder_existed = not run.vm.ssh(f"test -d {SCREENSHOTS}/Screenshots", check=False).returncode
    files = []
    try:
        run.vm.ssh(f"touch {marker}")
        _typed, _heard, mark = run.say(phrase_id, wait=False)
        # One deadline, never moved: TuxTalk's own waits end within it.
        deadline = time.monotonic() + SCREENSHOT_WAIT_S
        while time.monotonic() < deadline and not screenshot_settled(run.log_since(mark)):
            time.sleep(1)
        if not screenshot_settled(run.log_since(mark)):
            # A question the portal still shows would hold the keyboard
            # for the checks after this one; Escape closes it.
            run.vm.tap("esc")
        time.sleep(2)       # a file still being written
        files = _new_screenshots(run, marker)
        log, shot = run.log_since(mark), run.shot(shot_name)
        sizes = [run.vm.ssh(f"stat -c %s {shlex.quote(f)}", check=False).stdout.strip()
                 for f in files]
        dimensions = [_png_size(run, f) for f in files]
    finally:
        # One last look, so a picture saved late is removed too.
        for file in {*files, *_new_screenshots(run, marker)}:
            run.vm.ssh(f"rm -f {shlex.quote(file)}", check=False)
        if not folder_existed:
            run.vm.ssh(f"rmdir {SCREENSHOTS}/Screenshots", check=False)
        run.vm.ssh(f"rm -f {marker}", check=False)
    return files, sizes, dimensions, log, shot


@check(SECTION, "screenshot_saved", "“take a screenshot” saves exactly one picture")
def screenshot_saved(run):
    with _open_apps_on(run):
        _allow_screenshots(run)
        run.sink_ready(fresh=True)
        files, sizes, _dimensions, log, shot = _pictures_from(run, "take_screenshot", "screenshot")
    if bad := _not_a_command(run, "take_screenshot", log, heard_in(log)):
        return bad
    if problem := screenshot_problem(files, sizes, log):
        return Outcome("fail", problem, screenshot=shot)
    return Outcome("pass", "One picture saved and removed again", screenshot=shot)


# A window's picture may carry its frame and shadow beyond the size KWin or
# AT-SPI gives for it: KDE's Breeze shadow adds about 65 px a side.
WINDOW_FRAME_PX = 160


@check(SECTION, "window_screenshot",
       "“take a screenshot of this window” saves the window, or opens the desktop's picker")
def window_screenshot(run):
    if {"gnome", "cosmic"} & set(run.vm.spec.tags):
        return _window_picker(run)
    with _open_apps_on(run):
        _allow_screenshots(run)
        run.sink_ready(fresh=True)
        box = _sink_box(run)
        if box is None:
            return Outcome("skip", "the test window's size can't be read here, so a picture "
                           "of it can't be told from another")
        files, sizes, dimensions, log, shot = _pictures_from(run, "take_window_screenshot",
                                                             "window-screenshot")
    if bad := _not_a_command(run, "take_window_screenshot", log, heard_in(log)):
        return bad
    if problem := screenshot_problem(files, sizes, log):
        return Outcome("fail", problem, screenshot=shot)
    size, screen = dimensions[0], (run.vm.spec.width, run.vm.spec.height)
    if size is None or size == screen:
        return Outcome("fail", f"the picture is {size}, the whole screen is {screen}",
                       screenshot=shot)
    if abs(size[0] - box.width) > WINDOW_FRAME_PX or abs(size[1] - box.height) > WINDOW_FRAME_PX:
        return Outcome("fail", f"the picture is {size}, the test window "
                       f"{(box.width, box.height)}", screenshot=shot)
    return Outcome("pass", f"One picture of the window ({size[0]}x{size[1]}, the screen is "
                   f"{screen[0]}x{screen[1]}), removed again", screenshot=shot)


def _window_picker(run):
    """GNOME's and COSMIC's portals can't take one window by themselves:
    TuxTalk opens the desktop's screenshot picker. Escape there must end in
    TuxTalk's "cancelled" note with nothing saved. Whether the picker showed
    is seen in the report's screenshot; choosing a window in it is Ron's
    check."""
    with _open_apps_on(run):
        _allow_screenshots(run)
        run.sink_ready(fresh=True)
        marker = f"~/{REMOTE}/shot.marker"
        run.vm.ssh(f"touch {marker}")
        cancelled, shot = None, None
        try:
            _typed, _heard, mark = run.say("take_window_screenshot", wait=False)
            run.wait_log(mark, "asked the desktop's picker", "Voice command not done",
                         "Text injected", timeout=30)
            time.sleep(4)       # the picker opening
            shot = run.shot("window-picker")
            run.vm.tap("esc")
            cancelled = _until(15, lambda: "of the window (cancelled" in run.log_since(mark))
            files, log = _new_screenshots(run, marker), run.log_since(mark)
        finally:
            if not cancelled:
                run.vm.tap("esc")       # a picker still open would hold the keyboard
            for file in _new_screenshots(run, marker):
                run.vm.ssh(f"rm -f {shlex.quote(file)}", check=False)
            run.vm.ssh(f"rm -f {marker}", check=False)
    if bad := _not_a_command(run, "take_window_screenshot", log, heard_in(log)):
        return bad
    noted = "Voice command not done: screenshot_cancelled" in log
    # The Escape is for the picker: the dictation was over by then.
    dictation_cancelled = "Recording cancelled" in log
    if not cancelled or not noted or files or dictation_cancelled:
        said = [line for line in log.splitlines()
                if "screenshot" in line.lower() or "Recording cancelled" in line]
        return Outcome("fail", f"Escape in the picker: cancelled {bool(cancelled)}, the note "
                       f"{noted}, {len(files)} picture(s) saved, the dictation cancelled "
                       f"{dictation_cancelled}; log: {said}", screenshot=shot)
    return Outcome("pass", "TuxTalk asked the desktop's picker; Escape ended it as cancelled, "
                   "with the note and nothing saved", screenshot=shot)


@check(SECTION, "what_can_i_say", "“what can I say” opens the Voice Commands window")
def what_can_i_say(run):
    def window():
        return next((w for w in run.vm.ui("windows") or [] if "Voice Commands" in w["title"]),
                    None)
    with _open_apps_on(run):
        run.sink_ready(fresh=True)
        log, heard = _said(run, "what_can_i_say", "Voice command: what can I say")
        if bad := _not_a_command(run, "what_can_i_say", log, heard):
            return bad
        shown = _until(15, window)
        shot = run.shot("what-can-i-say")
        if shown:
            run.vm.ui("click", "button", "Close", "~Voice Commands", timeout=5)
        closed = shown and _until(8, lambda: not window())
        if shown and not closed:
            run.vm.tap("esc")
            closed = _until(5, lambda: not window())
    if "Voice command: what can I say" not in log or not shown:
        return Outcome("fail", "the “Voice Commands” window never appeared", screenshot=shot)
    if not closed:
        return Outcome("fail", "the “Voice Commands” window could not be closed",
                       screenshot=shot)
    return Outcome("pass", "The Voice Commands window opened and was closed again",
                   screenshot=shot)


# --- windows by name ----------------------------------------------------------


def _editor_state(run, app, program):
    """How the editor's window looks from outside: "minimized (KWin)" or
    "shown (KWin)" (with the focus) or "unfocused (KWin)" where KWin says,
    else the window's own AT-SPI state as checks_voice_actions._sink_state
    reads it ("iconified", "gone", "unfocused", or None while it shows with
    the focus)."""
    found = _kwin_windows(run, f"~{program}")
    if found:
        if all(w["minimized"] for w in found):
            return "minimized (KWin)"
        return "shown (KWin)" if any(w["active"] for w in found) else "unfocused (KWin)"
    window = next((w for w in run.vm.ui("windows") or [] if w["app"] == app), None)
    return _sink_state(window)


# _editor_state answers that mean the window is not minimized.
SHOWING = (None, "shown (KWin)", "unfocused (KWin)")


@check(SECTION, "restore_by_name",
       "“minimize the text editor” and “restore the text editor” by name")
def restore_by_name(run):
    if why := _action_blocker(run):
        return Outcome("skip", why)
    with _open_apps_on(run), opened_by_voice(run, "open_text_editor") as opened:
        if why := _no_editor(opened):
            return Outcome("skip", why)
        app, program = opened.app, opened.program
        log, heard = _said(run, "minimize_text_editor", "Voice command: minimize")
        if bad := _not_a_command(run, "minimize_text_editor", log, heard):
            return bad
        gone = _until(8, lambda: _editor_state(run, app, program) not in SHOWING)
        state = _editor_state(run, app, program)
        shot = run.shot("minimize-editor")
        if "Voice command: minimize" not in log or not gone:
            return Outcome("fail", f"“minimize the text editor”: the editor is {state or 'shown'}",
                           screenshot=shot)
        if state == "unfocused" and not _wayland(run):
            return Outcome("fail", "the editor lost the focus but doesn't say it is minimized, "
                           "which only a Wayland session can't tell it", screenshot=shot)
        log, heard = _said(run, "restore_text_editor", "Voice command: restore")
        if bad := _not_a_command(run, "restore_text_editor", log, heard):
            return bad
        back = _until(10, lambda: _editor_state(run, app, program) in (None, "shown (KWin)"))
        shot = run.shot("restore-editor")
        if "Voice command: restore" not in log or not back:
            return Outcome("fail", f"“restore the text editor”: the editor is "
                           f"{_editor_state(run, app, program)}", screenshot=shot)
        return Outcome("pass", f"Minimized ({state}), then shown with the focus again",
                       screenshot=shot)


# --- words that only look like commands --------------------------------------

SENTENCES = (("bare_pause", "Pause."), ("bare_next", "Next."), ("bare_mute", "Mute."),
             ("bare_copy", "Copy."), ("sentence_lock_it", "Lock it."),
             ("sentence_show_me_desktop", "Show me the desktop."),
             ("sentence_volume_loud", "Volume is too loud."))


@check(SECTION, "bare_words_and_sentences_typed",
       "One word or a sentence that only sounds like a command is typed")
def bare_words_and_sentences_typed(run):
    with _open_apps_on(run):
        start = _output(run)
        for phrase_id, expected in SENTENCES:
            run.sink_ready(fresh=True)
            typed, heard, log = run.say(phrase_id)
            outcome = run.expect_typed(phrase_id, expected, typed, heard)
            if outcome.status != "pass":
                outcome.detail = outcome.detail or f"“{expected}” was not typed as said"
                outcome.screenshot = run.shot(f"words-{phrase_id}")
                return outcome
            if "Voice command" in log:
                return Outcome("fail", f"“{expected}” was taken as a command", typed=typed)
        if start is not None and _output(run) != start:
            return Outcome("fail", f"the volume changed from {start} to {_output(run)}")
        return Outcome("pass", f"All {len(SENTENCES)} were typed as said; no command ran and "
                       "the volume stayed as it was")


# Last in the section: a lock that goes wrong must not hold up another check.
def _unlock(run, session):
    """Unlock the session whatever logind says (COSMIC's locker never tells
    it), through loginctl and through sudo, and on Cinnamon and GNOME
    through their screensaver, and look again: True only when logind says
    "no", the lock screen is down (_screen_locked) and the test window shows
    with the focus, which a lock screen in front of it would take. Unknown
    is not unlocked. Asked UNLOCK_TRIES times before giving up: GNOME once
    left its lock screen up after logind's Unlock and took the next one, and
    the Abort that follows a failure skips every check after it."""
    for _attempt in range(UNLOCK_TRIES):
        for command in ("loginctl unlock-session", "sudo -n loginctl unlock-session"):
            run.vm.ssh(f"{command} {session}", check=False)
        if "cinnamon" in run.vm.spec.tags:
            _session_tool(run, *CINNAMON_UNLOCK)
        elif "gnome" in run.vm.spec.tags:
            _session_tool(run, *GNOME_UNLOCK)
        time.sleep(5)      # a lock that landed late is undone by this
        run.vm.click_centre()
        if _until(10, lambda: _locked(run, session) == "no"
                  and not _screen_locked(run, session)
                  and _sink_state(_sink(run)) is None):
            return True
    return False


@check(SECTION, "lock_screen", "“lock screen” locks the session")
def lock_screen(run):
    session = _session_id(run)
    if not session:
        return Outcome("skip", "the desktop session's id couldn't be found")
    if _screen_locked(run, session):
        return Outcome("skip", "the VM's session was already locked")
    # Where the lock screen can be seen (logind is told, or Cinnamon's
    # screensaver is asked), it must come up; elsewhere TuxTalk's "not
    # confirmed" note is the answer.
    seen = bool({*LOCK_ANSWERS, "cinnamon"} & set(run.vm.spec.tags))
    with _open_apps_on(run):
        run.sink_ready(fresh=True)
        try:
            log, heard = _said(run, "lock_screen", "Voice command: lock screen", timeout=40)
            locked = bool(_until(5, lambda: _screen_locked(run, session)))
            if locked and "cinnamon" in run.vm.spec.tags:
                # Its GetActive says only that it is on, and so does
                # TuxTalk's check: a lock stays on at a key press (the
                # password box comes up), a screen only blanked goes away.
                run.vm.tap("shift")
                time.sleep(2)
                locked = _screen_locked(run, session)
            shot = run.shot("lock-screen")
        finally:
            unlocked = _unlock(run, session)
        if not unlocked:
            raise Abort("after “lock screen” the VM's session couldn't be shown to be unlocked "
                        "(logind not saying \"no\", or the test window not in front)")
    if bad := _not_a_command(run, "lock_screen", log, heard):
        return bad
    unconfirmed = "Voice command not done: lock_unconfirmed" in log
    said = f"locked: {'yes' if locked else 'no'}; the note lock_unconfirmed: " \
           f"{'yes' if unconfirmed else 'no'}"
    if seen and locked and unconfirmed:
        return Outcome("fail", f"the screen locked, but TuxTalk said it couldn't confirm it "
                       f"({said})", screenshot=shot)
    if locked or (not seen and unconfirmed):
        unverified = "" if locked else ("; whether it really locked can't be verified on this "
                                        "desktop (the lock isn't reported to logind)")
        return Outcome("pass", f"{said}{unverified}; the VM's session was unlocked again",
                       screenshot=shot)
    return Outcome("fail", f"the session didn't lock within 5 seconds ({said})", screenshot=shot)
