"""Open apps by voice (TuxTalk Labs): "open the text editor" starts it, said
again it brings the open window forward, a name that fits nothing is typed
as words, and a GTK app starts as from the menu. Ron's part is
RELEASE_CHECKLIST.md's "Open apps by voice (Labs, from the AppImage)": the
dropdowns of that GTK app, and an app that uses the NVIDIA card.

The apps are found by name in the VM's own menu, so the text editor is
whatever that desktop has as its default. A window counts as the app's when
its application wasn't in the AT-SPI window list before and is named after
the program its menu entry runs; what a check started is ended by its new
processes afterwards, whatever the check found.
"""
import contextlib
import re
import shlex
import time
from dataclasses import dataclass, field

from harness import Outcome, check, squash

SECTION = "Open apps by voice"
NO_MATCH = "Open command: no app matched; typed as words"
# What an AppImage or a venv leaves in TuxTalk's environment, and an app
# started from the menu must not inherit (launch_env.py). The VMs have no
# CUDA download, so TuxTalk hides the GPU there with CUDA_VISIBLE_DEVICES;
# the dictation service runs with GDK_BACKEND=x11.
LEAKS = ("APPDIR|APPIMAGE|ARGV0|OWD|PYTHONPATH|PYTHONHOME|GI_TYPELIB_PATH|CUDA_VISIBLE_DEVICES"
         "|GDK_BACKEND|HF_HUB_DISABLE_XET|TUXTALK_[A-Z_]*")
# A search path still holding the AppImage's mount.
MOUNT_ON_A_PATH = r"(LD_LIBRARY_PATH|PATH)=.*/\.mount_"
# Python or GTK apps to try, with the phrase that opens each.
GTK_APPS = (("meld", "open_meld"), ("gnome-calculator", "open_calculator"))
PHRASES = ("open_text_editor", "open_unknown_app", "open_meld", "open_calculator")
# Launchers whose app runs, and shows in AT-SPI, under another name:
# "libreoffice --writer" (Debian's default text editor) starts soffice.bin,
# whose application AT-SPI calls "soffice".
RUNS_AS = {"libreoffice": ("soffice", "soffice.bin")}


# The session's accessibility switch (org.a11y.Status's IsEnabled), which
# apps read when they start.
A11Y_SWITCH = ("gdbus", "call", "--session", "--dest", "org.a11y.Bus", "--object-path",
               "/org/a11y/bus", "--method")
# Where menu entries are, for the program an "Opened <id>" line started.
ENTRY_DIRS = ("~/.local/share/applications", "/usr/local/share/applications",
              "/usr/share/applications", "/var/lib/flatpak/exports/share/applications",
              "/var/lib/snapd/desktop/applications")


def _a11y_on(run):
    """Whether the session's accessibility switch is on."""
    answer = run.vm.session(*A11Y_SWITCH, "org.freedesktop.DBus.Properties.Get",
                            "org.a11y.Status", "IsEnabled", check=False).stdout
    return "true" in answer


def _set_a11y(run, on):
    run.vm.session(*A11Y_SWITCH, "org.freedesktop.DBus.Properties.Set", "org.a11y.Status",
                   "IsEnabled", "<true>" if on else "<false>", check=False)


@contextlib.contextmanager
def apps_seen(run):
    """Every app started in the block shows its windows to AT-SPI. GTK apps
    always do; Qt apps (KWrite), Firefox and COSMIC's only when the session's
    accessibility bus says IsEnabled, which nothing turns on without a screen
    reader, so their windows never showed. Apps already running that follow
    the switch come in at once, before a check lists the apps it has, so they
    don't look newly opened. Off again afterwards: with the desktop's shell in
    the tree, a button another check looks for by name could be the shell's."""
    was_on = _a11y_on(run)
    if not was_on:
        _set_a11y(run, True)
        time.sleep(2)
    try:
        yield
    finally:
        if not was_on:
            _set_a11y(run, False)


@contextlib.contextmanager
def _open_apps_on(run):
    """Open apps ticked for the block, as the VM had it afterwards, and the
    windows of the apps it opens seen (apps_seen)."""
    with run.settings_put_back("open_apps"), apps_seen(run):
        run.set_config(open_apps=True)
        yield


def _windows(run):
    """[(application, title, has the focus)] of every window in the session."""
    return [(w["app"], w["title"], "active" in w["states"])
            for w in run.vm.ui("windows") or []]


def _apps(run):
    return {app for app, _title, _active in _windows(run)}


def names_of(program):
    """*program* and the names its app runs under (RUNS_AS)."""
    return (program, *RUNS_AS.get(program, ()))


def _pattern(program):
    """Regular expression for a command line running *program* (or a name it
    runs under) as a command or a path's last part. Not -x: the kernel keeps
    15 characters of a process name, so "-x gnome-calculator" never matches."""
    return f"(^|/)({'|'.join(re.escape(name) for name in names_of(program))})( |$)"


def _running(program):
    """pgrep/pkill -f pattern for *program* (_pattern)."""
    return shlex.quote(_pattern(program))


def _close(run, program):
    """Quit every *program*, for when its new processes couldn't be told
    apart from the ones before (ps failed)."""
    run.vm.ssh(f"pkill -f {_running(program)}", check=False)
    time.sleep(1.5)


def _processes(run):
    """{pid: (command name, command line)} of the user's processes now, None
    when ps failed."""
    result = run.vm.ssh("ps -u $(id -u) -o pid=,comm:16=,args=", check=False)
    if result.returncode:
        return None
    return parse_ps(result.stdout)


def parse_ps(text):
    """{pid: (command name, command line)} of `ps -o pid=,comm:16=,args=`'s
    lines. A name can hold spaces ("Web Content"); it is 15 characters at
    most, so the 16-wide column always ends in one."""
    rows = (re.match(r"\s*(\d+) (.{16}) ?(.*)", line) for line in text.splitlines())
    return {row[1]: (row[2].strip(), row[3]) for row in rows if row}


def _new_pids(now, before, program):
    """The pids in *now* (from _processes) running *program* that weren't
    in *before*."""
    pattern = re.compile(_pattern(program))
    return sorted(pid for pid, (_comm, args) in now.items()
                  if pid not in before and pattern.search(args))


def program_of(exec_line):
    """The program a menu entry's Exec line runs, by its last path part:
    "kwrite" of "/usr/bin/kwrite %U", "foo" of "env A=1 foo"; None for none.
    A Flatpak's is its --command= (else its app id's last part), not
    "flatpak": no flatpak process stays running, and AT-SPI names the app."""
    words = shlex.split(exec_line)
    plain = [w for w in words if "=" not in w and w != "env"]
    if not plain:
        return None
    program = plain[0].rsplit("/", 1)[-1]
    if program != "flatpak":
        return program
    command = next((w.split("=", 1)[1] for w in words if w.startswith("--command=")), None)
    if command:
        return command.rsplit("/", 1)[-1]
    app_id = next((w for w in plain[2:] if not w.startswith("-") and "." in w), None)
    return app_id.rsplit(".", 1)[-1] if app_id else program


def _entry(run, app_id):
    """(program, name) of the menu entry *app_id* on the VM, each None when
    it can't be read."""
    script = (f"for d in {' '.join(ENTRY_DIRS)}; do f=\"$d\"/{shlex.quote(app_id)}; "
              "[ -f \"$f\" ] && { sed -n '/^\\[Desktop Entry\\]/,/^\\[/p' \"$f\"; break; }; done")
    found = dict(line.split("=", 1) for line in run.vm.ssh(script, check=False).stdout.splitlines()
                 if line.startswith(("Exec=", "Name=")))
    return program_of(found.get("Exec", "")), found.get("Name")


def window_of(app, names):
    """Whether the AT-SPI application *app* is the one *names* (the program,
    the menu entry's id and Name) describe, whatever the spaces, dashes and
    capitals: "gnome-text-editor", "kwrite", "COSMIC Text Editor"."""
    return squash(app) in {squash(name) for name in names if name}


@dataclass
class Opened:
    """What an "open ..." command said by a check started."""
    log: str                    # the TuxTalk log since the command
    program: str = None         # what the menu entry runs, when one was started
    app: str = None             # its AT-SPI application, once a window of it shows
    pids: list = field(default_factory=list)   # its processes that are new
    new_apps: list = field(default_factory=list)   # every application new in AT-SPI


@contextlib.contextmanager
def opened_by_voice(run, phrase_id, timeout=25):
    """Say *phrase_id*, an "open ..." command, with the test window in front,
    and yield an Opened once the window shows or *timeout* seconds have
    passed. Afterwards every process of the program that started meanwhile
    is ended, also when the check failed or no window of it was seen: the
    next check finds the app not running, not "already open"."""
    run.sink_ready(fresh=True)
    before_apps, before = _apps(run), _processes(run)
    _typed, _heard, mark = run.say(phrase_id, wait=False)
    run.wait_log(mark, "Opened ", "already open", NO_MATCH, timeout=30)
    opened = Opened(run.log_since(mark))
    found = re.search(r"Opened (\S+\.desktop)", opened.log)
    if found:
        opened.program, name = _entry(run, found.group(1))
        names = (*names_of(opened.program),
                 found.group(1).removesuffix(".desktop").split(".")[-1], name)
        deadline = time.monotonic() + timeout
        while opened.app is None and time.monotonic() < deadline:
            opened.new_apps = sorted(_apps(run) - before_apps)
            opened.app = next((a for a in opened.new_apps if window_of(a, names)), None)
            if opened.app is None:
                time.sleep(1)
        opened.log = run.log_since(mark)
    try:
        if opened.program and before is not None:
            opened.pids = _new_pids(_processes(run) or {}, before, opened.program)
        yield opened
    finally:
        _end(run, opened.program, before)


def _end(run, program, before):
    """End the processes of *program* that weren't in *before*: every one
    when *before* couldn't be listed."""
    if not program:
        return
    if before is None:
        _close(run, program)
        return
    pids = _new_pids(_processes(run) or {}, before, program)
    if pids:
        run.vm.ssh(f"kill {' '.join(pids)}", check=False)
        time.sleep(1.5)


def _nothing_to_open(log, what):
    """Why *log* (of the first "open" in a check) shows nothing was started,
    or None when it was: no such app on this VM, or it was already running."""
    if NO_MATCH in log:
        return f"this VM has no {what} to open"
    if "already open" in log:
        return f"the {what} was already running; close it and run again"
    return None


def _not_seen(opened):
    """Why no window of what *opened* started shows, for a failure's detail."""
    return (f"no window of {opened.program or 'it'} showed (new in AT-SPI: "
            f"{', '.join(opened.new_apps) or 'nothing'})")


@check(SECTION, "open_text_editor", "“open the text editor” starts the default editor")
def open_text_editor(run):
    with _open_apps_on(run), opened_by_voice(run, "open_text_editor") as opened:
        if why := _nothing_to_open(opened.log, "default text editor"):
            return Outcome("skip", why)
        typed = run.sink_text().strip()
        problems = []
        if "Opened " not in opened.log:
            problems.append("no “Opened …” in the log")
        elif opened.app is None and not opened.pids:
            problems.append(f"{_not_seen(opened)} and no new {opened.program} is running")
        if typed:
            problems.append(f"the words were typed: {typed!r}")
        shot = run.shot("open-text-editor")
        if problems:
            return Outcome("fail", "; ".join(problems), typed=typed, screenshot=shot)
        if opened.app is None:
            return Outcome("pass", f"{opened.program} started (process {', '.join(opened.pids)}) "
                           "and nothing was typed; this desktop shows no window of it through "
                           "AT-SPI, so the window is seen only in the screenshot",
                           screenshot=shot)
        return Outcome("pass", f"A window of “{opened.app}” opened and nothing was typed",
                       screenshot=shot)


@check(SECTION, "open_again_focuses",
       "Said again, “open the text editor” brings the open window forward")
def open_again_focuses(run):
    if "cosmic" in run.vm.spec.tags:
        return Outcome("skip", "COSMIC can't bring a window forward; the app is started again, "
                       "by design")
    if run.window_blind():
        return Outcome("skip", run.window_blind())
    with _open_apps_on(run), opened_by_voice(run, "open_text_editor") as opened:
        if why := _nothing_to_open(opened.log, "default text editor"):
            return Outcome("skip", why)
        app = opened.app
        if app is None:
            return Outcome("fail", f"the text editor didn't open the first time: "
                           f"{_not_seen(opened)}")
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


@check(SECTION, "open_unknown_app", "A name that fits no app is typed as words, opening nothing")
def open_unknown_app(run):
    with _open_apps_on(run):
        run.sink_ready(fresh=True)
        before = _apps(run)
        _typed, _heard, mark = run.say("open_unknown_app", wait=False)
        run.wait_log(mark, "Text injected", "Voice command: open", timeout=25)
        time.sleep(3)
        typed, log = run.sink_text().strip(), run.log_since(mark)
        shot = run.shot("open-no-match")
        problems = []
        if NO_MATCH not in log:
            if typed and "Voice command: open" not in log:
                return Outcome("misheard", "the computer voice was heard as another sentence",
                               typed=typed, screenshot=shot)
            problems.append(f"no “{NO_MATCH}” in the log")
        opened = sorted(_apps(run) - before)
        if opened:
            problems.append(f"{opened} opened")
        if not typed:
            problems.append("nothing was typed")
        if problems:
            return Outcome("fail", "; ".join(problems), typed=typed, screenshot=shot)
        return Outcome("pass", f"Typed as words and nothing opened; log: “{NO_MATCH}”",
                       typed=typed, screenshot=shot)


@check(SECTION, "open_gtk_app", "A Python or GTK app opened by voice starts clean")
def open_gtk_app(run):
    program, phrase = next(((p, ph) for p, ph in GTK_APPS
                            if not run.vm.ssh(f"command -v {p}", check=False).returncode),
                           (None, None))
    if program is None:
        return Outcome("skip", f"neither {' nor '.join(p for p, _ in GTK_APPS)} is installed here")
    with _open_apps_on(run), opened_by_voice(run, phrase) as opened:
        if NO_MATCH in opened.log:
            return Outcome("misheard", f"the computer voice wasn't heard as {program}'s name",
                           typed=run.sink_text().strip())
        if "already open" in opened.log:
            return Outcome("skip", f"{program} was already running; close it and run again")
        if opened.app is None:
            return Outcome("fail", f"{program} didn't open: {_not_seen(opened)}")
        problems = _inherited(run, program)
        if "its own scope" in opened.log or "stays in TuxTalk's group" in opened.log:
            problems.append("it wasn't given a scope of its own")
        if problems:
            return Outcome("fail", "; ".join(problems))
        shot = run.shot("open-gtk-app")
        return Outcome(
            "pass", f"{program} opened with none of {LEAKS.replace('|', ', ')} in its "
            "environment and no AppImage folder on LD_LIBRARY_PATH or PATH. Its dropdowns "
            "are Ron's: Step 8's “Open apps by voice (Labs, from the AppImage)”.",
            screenshot=shot)


def _inherited(run, program):
    """What's wrong with the environment of the running *program*: a list of
    problems, empty when it has none of TuxTalk's variables. No process, or
    one whose environment can't be read, is a problem, not a clean start."""
    answer = run.vm.ssh(
        f"pid=$(pgrep -nf {_running(program)}) && [ -r /proc/$pid/environ ] "
        f"|| {{ echo NOT_FOUND; exit 0; }}; "
        f"tr '\\0' '\\n' < /proc/$pid/environ | grep -E '^({LEAKS})=|^{MOUNT_ON_A_PATH}'; "
        "echo READ",
        check=False).stdout
    if "NOT_FOUND" in answer or "READ" not in answer:
        return [f"couldn't find {program}'s process to read its environment"]
    names = sorted(line.split("=", 1)[0] for line in answer.splitlines()
                   if "=" in line)
    return [f"{program} started with TuxTalk's {', '.join(names)}"] if names else []
