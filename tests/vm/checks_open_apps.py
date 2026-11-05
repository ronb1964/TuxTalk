"""Open apps by voice (TuxTalk Labs): "open the text editor" starts it, said
again it brings the open window forward, a name that fits nothing opens
nothing, and a GTK app starts as from the menu. CLAUDE.md Step 8: "Open apps
by voice".

The apps are found by name in the VM's own menu, so the text editor is
whatever that desktop has as its default. A window counts as new when its
application wasn't in the AT-SPI window list before.
"""
import contextlib
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


def _close(run, app):
    """Quit *app*, so the next check starts with it not running."""
    for name in {app, app.lower()}:
        run.vm.ssh(f"pkill -x {shlex.quote(name)}", check=False)
    time.sleep(1)


def _started_by_voice(run, phrase_id):
    """Say *phrase_id* with the test window in front; (app that opened or
    None, what was typed, the log). The app is the caller's to close."""
    run.sink_ready(fresh=True)
    before = _apps(run)
    typed, _heard, log = run.say(phrase_id)
    return _new_app(run, before), typed, log


@check(SECTION, "open_text_editor", "“open the text editor” starts the default editor")
def open_text_editor(run):
    with _open_apps_on(run):
        app, typed, log = _started_by_voice(run, "open_text_editor")
        try:
            if NO_MATCH in log:
                return Outcome("skip", "this VM has no default text editor to open")
            problems = []
            if app is None:
                problems.append("no new window appeared")
            if "Opened " not in log:
                problems.append("no “Opened …” in the log")
            if typed.strip():
                problems.append(f"the words were typed: {typed.strip()!r}")
            if problems:
                return Outcome("fail", "; ".join(problems), typed=typed.strip())
            return Outcome("pass", f"A window of “{app}” opened and nothing was typed",
                           screenshot=run.shot("open-text-editor"))
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
        app, _typed, log = _started_by_voice(run, "open_text_editor")
        try:
            if NO_MATCH in log:
                return Outcome("skip", "this VM has no default text editor to open")
            if app is None:
                return Outcome("fail", "the text editor didn't open the first time")
            count = sum(1 for w in _windows(run) if w[0] == app)
            run.sink_ready(fresh=True)          # in front of the editor, which has to come back
            _typed, _heard, again = run.say("open_text_editor")
            time.sleep(2)
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
            return Outcome("pass", f"The same {app} window came forward; log: “Brought … "
                           "forward: it was already open”", screenshot=run.shot("open-again"))
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
        typed = run.sink_text()
        log = run.log_since(mark)
        problems = []
        if NO_MATCH not in log:
            if typed.strip():
                return Outcome("misheard", "the computer voice was heard as another sentence",
                               typed=typed.strip(), screenshot=shot)
            problems.append("no “no app matched” in the log")
        if _apps(run) - before:
            problems.append(f"{sorted(_apps(run) - before)} opened")
        if typed.strip():
            problems.append("something was typed")
        if problems:
            return Outcome("fail", "; ".join(problems), typed=typed.strip(), screenshot=shot)
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
        app, _typed, log = _started_by_voice(run, phrase)
        try:
            if app is None:
                return Outcome("fail", f"{program} didn't open")
            leaked = run.vm.ssh(
                f"tr '\\0' '\\n' < /proc/$(pgrep -nx {program})/environ | grep -E '^({LEAKS})='",
                check=False).stdout.strip()
            problems = []
            if leaked:
                names = sorted(line.split("=", 1)[0] for line in leaked.splitlines())
                problems.append(f"{program} started with TuxTalk's {', '.join(names)}")
            if "its own scope" in log or "stays in TuxTalk's group" in log:
                problems.append("it wasn't given a scope of its own")
            if problems:
                return Outcome("fail", "; ".join(problems))
            return Outcome(
                "pass", f"{program} opened with none of {LEAKS.replace('|', ', ')} in its "
                "environment. Its dropdowns are not opened here: that is on Ron's list.",
                screenshot=run.shot("open-gtk-app"))
        finally:
            _close(run, program)
