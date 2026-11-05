"""Open apps by voice (TuxTalk Labs): "open the text editor" starts it, said
again it brings the open window forward, a name that fits nothing opens
nothing, and a GTK app starts as from the menu. CLAUDE.md Step 8: "Open apps
by voice".

The apps are found by name in the VM's own menu, so the text editor is
whatever that desktop has as its default. A window counts as new when its
application wasn't in the AT-SPI window list before.
"""
import contextlib
import re
import shlex
import time

from harness import Outcome, check

SECTION = "Open apps by voice"
NO_MATCH = "Voice command: open, but no app matched"
# What an AppImage or a venv leaves in TuxTalk's environment, and an app
# started from the menu must not inherit (launch_env.py).
LEAKS = "APPDIR|APPIMAGE|ARGV0|OWD|PYTHONPATH|PYTHONHOME|GI_TYPELIB_PATH"
# Python or GTK apps to try, with the phrase that opens each.
GTK_APPS = (("meld", "open_meld"), ("gnome-calculator", "open_calculator"))
PHRASES = ("open_text_editor", "open_unknown_app", "open_meld", "open_calculator")


@contextlib.contextmanager
def _open_apps_on(run):
    """Open apps ticked for the block, as the VM had it afterwards."""
    with run.settings_put_back("open_apps"):
        run.set_config(open_apps=True)
        yield


def _windows(run):
    """[(application, title, has the focus)] of every window in the session."""
    return [(w["app"], w["title"], "active" in w["states"])
            for w in run.vm.ui("windows") or []]


def _apps(run):
    return {app for app, _title, _active in _windows(run)}


def _new_app(run, before, timeout=25):
    """The application that has a window now and didn't in *before*, or None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new = _apps(run) - before
        if new:
            return sorted(new)[0]
        time.sleep(1)
    return None


def _running(program):
    """pgrep/pkill -f pattern for *program* as a command or a path's last part.
    Not -x: the kernel keeps 15 characters of a process name, so "-x
    gnome-calculator" never matches."""
    return shlex.quote(f"(^|/){re.escape(program)}( |$)")


def _close(run, app):
    """Quit *app*, so the next check starts with it not running. One that
    stays is noticed there: its first "open" finds it already open."""
    run.vm.ssh(f"pkill -f {_running(app)}", check=False)
    time.sleep(1.5)


def _say_and_wait(run, phrase_id, *outcomes):
    """Say *phrase_id* with the test window in front and wait for the log to
    show *outcomes* (what open_app logs once it has acted). run.say's own
    wait ends at "Voice command:", which comes before the app is started or
    brought forward. Returns (the applications with windows before, the log mark)."""
    run.sink_ready(fresh=True)
    before = _apps(run)
    _typed, _heard, mark = run.say(phrase_id, wait=False)
    run.wait_log(mark, *outcomes, NO_MATCH, timeout=30)
    return before, mark


def _nothing_to_open(log, what):
    """Why *log* (of the first "open" in a check) shows nothing was started,
    or None when it was: no such app on this VM, or it was already running."""
    if NO_MATCH in log:
        return f"this VM has no {what} to open"
    if "already open" in log:
        return f"the {what} was already running; close it and run again"
    return None


@check(SECTION, "open_text_editor", "“open the text editor” starts the default editor")
def open_text_editor(run):
    with _open_apps_on(run):
        before, mark = _say_and_wait(run, "open_text_editor", "Opened ", "already open")
        log = run.log_since(mark)
        if why := _nothing_to_open(log, "default text editor"):
            return Outcome("skip", why)
        app = _new_app(run, before)
        time.sleep(1.5)
        log, typed = run.log_since(mark), run.sink_text().strip()
        try:
            problems = []
            if app is None:
                problems.append("no new window appeared")
            if "Opened " not in log:
                problems.append("no “Opened …” in the log")
            if typed:
                problems.append(f"the words were typed: {typed!r}")
            if problems:
                return Outcome("fail", "; ".join(problems), typed=typed)
            shot = run.shot("open-text-editor")
            return Outcome("pass", f"A window of “{app}” opened and nothing was typed",
                           screenshot=shot)
        finally:
            if app:
                _close(run, app)


@check(SECTION, "open_again_focuses",
       "Said again, “open the text editor” brings the open window forward")
def open_again_focuses(run):
    if "cosmic" in run.vm.spec.tags:
        return Outcome("skip", "COSMIC can't bring a window forward; the app is started again, "
                       "by design")
    if run.window_blind():
        return Outcome("skip", run.window_blind())
    with _open_apps_on(run):
        before, mark = _say_and_wait(run, "open_text_editor", "Opened ", "already open")
        log = run.log_since(mark)
        if why := _nothing_to_open(log, "default text editor"):
            return Outcome("skip", why)
        app = _new_app(run, before)
        try:
            if app is None:
                return Outcome("fail", "the text editor didn't open the first time")
            count = sum(1 for w in _windows(run) if w[0] == app)
            run.sink_ready(fresh=True)          # in front of the editor, which has to come back
            _typed, _heard, again_mark = run.say("open_text_editor", wait=False)
            run.wait_log(again_mark, "Brought ", "can't bring", "Couldn't bring", "Opened ",
                         timeout=30)
            time.sleep(1.5)                     # the window manager hands the focus over
            again = run.log_since(again_mark)
            windows = [w for w in _windows(run) if w[0] == app]
            problems = []
            if len(windows) != count:
                problems.append(f"{count} window(s) of {app} before, {len(windows)} after")
            if not any(active for _app, _title, active in windows):
                problems.append(f"{app} didn't get the focus")
            if "already open" not in again:
                problems.append("no “Brought … forward: it was already open” in the log")
            if problems:
                return Outcome("fail", "; ".join(problems))
            shot = run.shot("open-again")
            return Outcome("pass", f"The same {app} window came forward; log: “Brought … "
                           "forward: it was already open”", screenshot=shot)
        finally:
            if app:
                _close(run, app)


@check(SECTION, "open_unknown_app", "A name that fits no app opens nothing and says so")
def open_unknown_app(run):
    with _open_apps_on(run):
        run.sink_ready(fresh=True)
        before = _apps(run)
        _typed, _heard, mark = run.say("open_unknown_app", wait=False)
        run.wait_log(mark, NO_MATCH, "Voice command: open", "Text injected", timeout=25)
        shot = run.shot("open-no-match")        # the floating label is up for a few seconds
        time.sleep(3)
        typed, log = run.sink_text().strip(), run.log_since(mark)
        problems = []
        if NO_MATCH not in log:
            if typed:
                return Outcome("misheard", "the computer voice was heard as another sentence",
                               typed=typed, screenshot=shot)
            problems.append("no “no app matched” in the log")
        opened = sorted(_apps(run) - before)
        if opened:
            problems.append(f"{opened} opened")
        if typed:
            problems.append("something was typed")
        if problems:
            return Outcome("fail", "; ".join(problems), typed=typed, screenshot=shot)
        return Outcome("pass", "Nothing opened or typed; log: “Voice command: open, but no "
                       "app matched”. The floating label is in the screenshot.", screenshot=shot)


@check(SECTION, "open_gtk_app", "A Python or GTK app opened by voice starts clean")
def open_gtk_app(run):
    program, phrase = next(((p, ph) for p, ph in GTK_APPS
                            if not run.vm.ssh(f"command -v {p}", check=False).returncode),
                           (None, None))
    if program is None:
        return Outcome("skip", f"neither {' nor '.join(p for p, _ in GTK_APPS)} is installed here")
    with _open_apps_on(run):
        before, mark = _say_and_wait(run, phrase, "Opened ", "already open")
        log = run.log_since(mark)
        if "already open" in log:
            return Outcome("skip", f"{program} was already running; close it and run again")
        app = _new_app(run, before)
        try:
            if app is None:
                return Outcome("fail", f"{program} didn't open")
            problems = _inherited(run, program)
            if "its own scope" in log or "stays in TuxTalk's group" in log:
                problems.append("it wasn't given a scope of its own")
            if problems:
                return Outcome("fail", "; ".join(problems))
            shot = run.shot("open-gtk-app")
            return Outcome(
                "pass", f"{program} opened with none of {LEAKS.replace('|', ', ')} in its "
                "environment. Its dropdowns are not opened here: that is on Ron's list.",
                screenshot=shot)
        finally:
            _close(run, program)


def _inherited(run, program):
    """What's wrong with the environment of the running *program*: a list of
    problems, empty when it has none of TuxTalk's variables. No process, or
    one whose environment can't be read, is a problem, not a clean start."""
    answer = run.vm.ssh(
        f"pid=$(pgrep -nf {_running(program)}) && [ -r /proc/$pid/environ ] "
        f"|| {{ echo NOT_FOUND; exit 0; }}; "
        f"tr '\\0' '\\n' < /proc/$pid/environ | grep -E '^({LEAKS})='; echo READ",
        check=False).stdout
    if "NOT_FOUND" in answer or "READ" not in answer:
        return [f"couldn't find {program}'s process to read its environment"]
    names = sorted(line.split("=", 1)[0] for line in answer.splitlines()
                   if "=" in line)
    return [f"{program} started with TuxTalk's {', '.join(names)}"] if names else []
