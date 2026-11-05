"""Voice commands for windows, keys and addresses (TuxTalk Labs, the one
"Open apps" tickbox): "close the text editor", "switch to ...", "minimize
window", "new tab", "go back", "go to example dot com", "web search cheap
tires" and "scroll down". These need a real desktop: only it has a window
that asks to save, a title bar to minimize, a browser with an address bar.

What the VM can't judge is Ron's part (CLAUDE.md Step 8's "Voice commands"):
how the keys feel in his own browsers and whether his default browser comes
forward. Closing, switching and sizing windows need to know which window has
the focus, so COSMIC and GNOME without the TuxTalk extension skip them.

The text editor is whatever the VM's menu has by that name (as in
checks_open_apps.py). Firefox is driven on two local pages, so nothing here
needs the internet; the default browser checks look for its window and for
the address or the search words in its address bar or title.
"""
import contextlib
import json
import re
import time

from checks_open_apps import (
    _apps, _nothing_to_open, _open_apps_on, _processes, _running, _windows, apps_seen,
    opened_by_voice)
import voices
from harness import Outcome, check
from vmlib import REMOTE

SECTION = "Windows, keys and addresses by voice"
SAVE_QUESTION = "Save changes?"
# A browser's application or window title, whatever the VM's default is.
BROWSER = re.compile(r"firefox|chrom|brave|epiphany|web|browser|mozilla|example", re.I)
PROFILE = f"~/{REMOTE}/ttff-profile"
# A new profile every time, with no first-run, welcome or privacy pages, no
# offer to restore the last session (an earlier check ended Firefox by
# killing it) and no questions: only the test page is open, in one tab.
FIREFOX_PREFS = """\
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.preonboarding.enabled", false);
user_pref("trailhead.firstrun.didSeeAboutWelcome", true);
user_pref("browser.startup.firstrunSkipsHomepage", true);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.startup.homepage_welcome_url", "");
user_pref("browser.startup.homepage_welcome_url.additional", "");
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("startup.homepage_override_url", "");
user_pref("browser.startup.page", 0);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.sessionstore.max_resumed_crashes", 0);
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("datareporting.policy.firstRunURL", "");
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("browser.uitour.enabled", false);
user_pref("browser.tabs.warnOnClose", false);
user_pref("browser.navigation.requireUserInteraction", false);
"""
# Firefox's address bar: a "combo box" in newer versions, an "entry" before.
ADDRESS_BAR = "combo box|entry"
# Page one sends the tab on to page two once, so that "go back" has somewhere
# to go and does not bounce forward again. Firefox leaves a page nobody
# clicked or typed in out of the back button's history unless
# browser.navigation.requireUserInteraction is off (FIREFOX_PREFS), and
# nobody touches page one.
PAGE_ONE = """<title>TT Page One</title><p>one</p>
<script>if (!sessionStorage.seen) { sessionStorage.seen = 1;
setTimeout(() => location.href = 'page2.html', 1000); }</script>"""
PAGE_TWO = "<title>TT Page Two</title><p>two</p>"


@contextlib.contextmanager
def _open_apps_on_unlogged(run):
    """Open apps ticked and "Log transcribed text" off for the block, as the
    VM had them afterwards: the dictation checks turn the logging on, which
    would put the address and the search words in the log these checks make
    sure they stay out of. One restart of the service for both."""
    with run.settings_put_back("open_apps", "log_transcripts"), apps_seen(run):
        run.set_config(open_apps=True, log_transcripts=False)
        yield


def _action_blocker(run):
    """Why window actions can't be checked here, or None."""
    if "cosmic" in run.vm.spec.tags:
        return "COSMIC can't act on a window by name; TuxTalk says so, by design"
    return run.window_blind()


def _sink(run):
    """The test window of the VM's window list, or None."""
    return next((w for w in run.vm.ui("windows") or [] if w["title"] == "TT Sink"), None)


def _until(seconds, test):
    """*test*()'s first true answer within *seconds*, else None."""
    deadline = time.monotonic() + seconds
    while True:
        answer = test()
        if answer:
            return answer
        if time.monotonic() > deadline:
            return None
        time.sleep(1)


def _command(run, phrase_id, *outcomes, timeout=30, mode=None, fresh=True):
    """Say *phrase_id* with the test window in front (a new one unless
    *fresh* is False, for a check that acts on the same window step by step)
    and wait for the log to show *outcomes*; returns the log mark. The words
    of a command that isn't heard right are typed, which the checks report
    as misheard."""
    run.sink_ready(fresh=fresh, mode=mode)
    _typed, _heard, mark = run.say(phrase_id, wait=False)
    run.wait_log(mark, *outcomes, "Voice command not done", "Text injected", timeout=timeout)
    time.sleep(1.5)
    return mark


def _unheard(run, mark, phrase_id):
    """A misheard Outcome naming *phrase_id* when the log shows the words
    were typed instead of done as a command, else None."""
    if "Voice command" not in run.log_since(mark) and run.sink_text().strip():
        said = voices.load_phrases()[phrase_id]["say"]
        return Outcome("misheard", f"the computer voice's “{said}” was heard as another "
                       "sentence", typed=run.sink_text().strip())
    return None


def _no_editor(opened):
    """Why a check that needs the text editor's window (an Opened) can't go
    on, or None."""
    if why := _nothing_to_open(opened.log, "default text editor"):
        return why
    if opened.app is None:
        return (f"no window of the text editor ({opened.program}) shows through AT-SPI; new "
                f"there: {', '.join(opened.new_apps) or 'nothing'}")
    return None


def _in_front(run, app):
    """Whether a window of *app* has the focus, after a click in the middle
    of its window when it hadn't. Not the middle of the screen: an editor can
    open beside it (Cinnamon), where that click lands on the test window."""
    def focused():
        return any("active" in w["states"] for w in run.vm.ui("windows", app=app) or [])
    if _until(3, focused):
        return True
    window = next((w for w in run.vm.ui("windows", app=app) or []
                   if _area(w) and w.get("position")), None)
    if window is None:
        return False
    (x, y), (width, height) = window["position"], window["size"]
    run.vm.click(x + width // 2, y + height // 2)
    return bool(_until(5, focused))


# --- windows ---------------------------------------------------------------


def _document_name(title):
    """The document's name in an editor window's title: "Untitled 1" of
    "*Untitled 1 - Mousepad"."""
    return re.split(r"\s+[-\u2013\u2014]\s+", title or "")[0].strip("*\u2022 ").strip()


def _new_window(run, app, before):
    """Whether the editor has a window that wasn't open before the close
    (*before*, its titles then): the question it asks."""
    return any(name == app and title not in before for name, title, _focused in _windows(run))


def _save_question(run, app, before, document):
    """The title of the save question for *document*: a window of the editor
    that wasn't open before the close and whose title or text names the
    document. None otherwise (a question naming no document, or one for
    another document already open): Discard is pressed nowhere then."""
    for name, title, _focused in _windows(run):
        if name != app or title in before:
            continue
        texts = run.vm.ui("texts", title, timeout=3, app=app) or []
        if document and document in " ".join([title or "", *texts]):
            return title
    return None


def _discard(run, app, question):
    """Answer the save question titled *question* with its discard button, as
    a person would, looked for in that dialog only: never a button of another
    window, the editor's other documents included. The editor itself is
    ended with the processes it started (opened_by_voice)."""
    names = ("~iscard", "~on't save", "~on\u2019t save", "~without sav")
    if question is not None and any(
            run.vm.ui("click", "button", name, question, timeout=2, app=app) for name in names):
        time.sleep(1.5)


def _unsaved_line(run, app):
    """Type a line into *app*'s window, which must have the focus; a
    problem (str) when it can't be given the focus or the keys went to the
    test window instead, else None."""
    if not _in_front(run, app):
        return f"{app}'s window couldn't be given the focus to type a line into it"
    for key in ("h", "i"):
        run.vm.tap(key)
    time.sleep(0.5)
    if run.sink_text().strip():
        return f"the line meant for {app} was typed into the test window"
    return None


@check(SECTION, "close_asks_to_save", "“close the text editor” asks to save, closing nothing")
def close_asks_to_save(run):
    if why := _action_blocker(run):
        return Outcome("skip", why)
    with _open_apps_on(run), opened_by_voice(run, "open_text_editor") as opened:
        if why := _no_editor(opened):
            return Outcome("skip", why)
        app, question = opened.app, None
        try:
            if problem := _unsaved_line(run, app):
                return Outcome("fail", problem, screenshot=run.shot("close-asks"))
            mine = [(title, focused) for name, title, focused in _windows(run) if name == app]
            titles = {title for title, _focused in mine}
            document = _document_name(next((t for t, focused in mine if focused), ""))
            _typed, _heard, mark = run.say("close_text_editor", wait=False)
            run.wait_log(mark, "Voice command: close", "Voice command not done", timeout=30)
            asked = _until(10, lambda: _new_window(run, app, titles))
            question = _save_question(run, app, titles, document) if asked else None
            log = run.log_since(mark)
            running = not run.vm.ssh(f"pgrep -f {_running(opened.program)}",
                                     check=False).returncode
            shot = run.shot("close-asks")
            problems = []
            if "Voice command: close" not in log:
                problems.append("no “Voice command: close” in the log")
            if not asked:
                problems.append("no question to save appeared")
            if not running:
                problems.append("the editor was closed without asking")
            if problems:
                return Outcome("fail", "; ".join(problems), screenshot=shot)
            return Outcome("pass", f"{app} asked what to do with the unsaved line and is still "
                           "running", screenshot=shot)
        finally:
            _discard(run, app, question)


@check(SECTION, "close_this_window", "“close this window” closes the focused window")
def close_this_window(run):
    if why := _action_blocker(run):
        return Outcome("skip", why)
    with _open_apps_on(run):
        mark = _command(run, "close_this_window", "Voice command: close", mode="ask")
        sink = _sink(run) or {}
        if misheard := _unheard(run, mark, "close_this_window"):
            return misheard
        # By its text, not its title: GTK 3 can give a message dialog a name of
        # its own for screen readers ("Question"), and on Mint the question
        # was on screen while no window in AT-SPI bore its title.
        asked = run.vm.ui("find", "label", SAVE_QUESTION, timeout=10, app=sink.get("app"))
        shot = run.shot("close-this-window")
        log = run.log_since(mark)
        if "Voice command: close the focused window" not in log:
            return Outcome("fail", "no “Voice command: close the focused window” in the log",
                           screenshot=shot)
        if not asked:
            return Outcome("fail", f"the window never asked “{SAVE_QUESTION}”", screenshot=shot)
        return Outcome("pass", f"The test window answered with its “{SAVE_QUESTION}” question",
                       screenshot=shot)


@check(SECTION, "switch_to", "“switch to the text editor” brings the open editor forward")
def switch_to(run):
    if why := _action_blocker(run):
        return Outcome("skip", why)
    with _open_apps_on(run), opened_by_voice(run, "open_text_editor") as opened:
        if why := _no_editor(opened):
            return Outcome("skip", why)
        app = opened.app
        count = sum(1 for w in _windows(run) if w[0] == app)
        mark = _command(run, "switch_text_editor", "Voice command: switch")
        if misheard := _unheard(run, mark, "switch_text_editor"):
            return misheard
        windows = [w for w in _windows(run) if w[0] == app]
        front = _until(10, lambda: any(w[2] for w in
                                       [w for w in _windows(run) if w[0] == app]))
        typed = run.sink_text().strip()
        shot = run.shot("switch-to")
        problems = []
        if "Voice command: switch" not in run.log_since(mark):
            problems.append("no “Voice command: switch” in the log")
        if not front:
            problems.append(f"{app} didn't get the focus")
        if len(windows) != count:
            problems.append(f"{count} window(s) of {app} before, {len(windows)} after")
        if typed:
            problems.append(f"the words were typed: {typed[:60]!r}")
        if problems:
            return Outcome("fail", "; ".join(problems), typed=typed, screenshot=shot)
        return Outcome("pass", f"The open {app} window came forward; no new window, "
                       "nothing typed", screenshot=shot)


def _area(window):
    """Pixels of an AT-SPI window's size, 0 when it reports none."""
    size = (window or {}).get("size") or [0, 0]
    return size[0] * size[1]


@check(SECTION, "minimize_maximize", "“restore”, “maximize” and “minimize window” size the window")
def minimize_maximize(run):
    if why := _action_blocker(run):
        return Outcome("skip", why)
    with _open_apps_on(run):
        run.sink_ready(fresh=True)      # the test window opens maximised
        if not _area(_sink(run)):
            return Outcome("skip", "this desktop reports no window sizes through AT-SPI")
        steps = []
        for phrase, wanted in (("restore_window", "smaller"), ("maximize_window", "bigger")):
            before = _area(_sink(run))
            mark = _command(run, phrase, "Voice command: ", fresh=False)
            if misheard := _unheard(run, mark, phrase):
                return misheard
            after = _until(8, lambda before=before: (
                _area(_sink(run)) != before and _area(_sink(run))))
            if not after or (after > before) != (wanted == "bigger"):
                return Outcome("fail", f"“{phrase}”: the window went from {before} to "
                               f"{after or before} px, expected it {wanted}",
                               screenshot=run.shot(f"size-{phrase}"))
            steps.append(f"{phrase.split('_')[0]} {before} -> {after}")
        if "active" not in ((_sink(run) or {}).get("states") or []):
            return Outcome("skip", "the test window doesn't have the focus to be minimized from")
        mark = _command(run, "minimize_window", "Voice command: ", fresh=False)
        if misheard := _unheard(run, mark, "minimize_window"):
            return misheard
        return _minimized(run, mark, steps)


def _sink_state(window):
    """"gone", "iconified" or "unfocused" for the test window once it's no
    longer showing with the focus, else None."""
    if window is None:
        return "gone"
    if "iconified" in window["states"]:
        return "iconified"
    return None if "active" in window["states"] else "unfocused"


def _wayland(run):
    """Whether the VM's desktop session says it is a Wayland one."""
    out = run.vm.session("printenv", "XDG_SESSION_TYPE", check=False).stdout
    return out.strip() == "wayland"


def _kwin_state(run):
    """"minimized (KWin)" once KWin says the test window is minimized, else
    None; None too off KDE. On KDE Wayland the window itself keeps saying it
    is shown and focused after a minimize, so only KWin can tell."""
    if "kde" not in run.vm.spec.tags:
        return None
    out = run.vm.session("python3", f"{run.vm.home}/{REMOTE}/kwin_window.py", "TT Sink",
                         check=False).stdout.strip().splitlines()
    try:
        found = json.loads(out[-1])
    except (IndexError, json.JSONDecodeError):
        return None
    if isinstance(found, list) and found and all(w["minimized"] for w in found):
        return "minimized (KWin)"
    return None


def _minimized(run, mark, steps):
    """The Outcome of "minimize window": the window says it's minimized
    where its toolkit learns of it (X11), or KWin says so (KDE). Only on
    other Wayland desktops, where an app is never told, is the window losing
    the focus the sign, once the log shows the command was done: any "not
    done" (a note, or the focus moving before the desktop was asked) fails
    it."""
    log = run.log_since(mark)
    refused = "not done" in log.casefold()     # "Not done (minimize): Esc ..." too
    if refused or "minimize the focused window" not in log:
        return Outcome("fail", "“minimize window” wasn't done: "
                       + ("the log says why" if refused else "no line in the log"),
                       screenshot=run.shot("minimize"))
    state = _until(8, lambda: _sink_state(_sink(run)) or _kwin_state(run))
    if state == "unfocused" and not _wayland(run):
        return Outcome("fail", "the window lost the focus but doesn't say it's minimized, "
                       "which only a Wayland session can't tell it",
                       screenshot=run.shot("minimize"))
    if not state:
        return Outcome("fail", "the window was still showing and focused after “minimize "
                       "window”", screenshot=run.shot("minimize"))
    how = {"iconified": "minimized (the window says so)",
           "gone": "minimized (gone from the window list)",
           "minimized (KWin)": "minimized (KWin says so; the window itself is never told "
                               "on Wayland)",
           "unfocused": "minimized as far as can be seen: the window lost the focus; it can't "
                        "say it's minimized here (Wayland tells an app nothing)"}[state]
    return Outcome("pass", "; ".join(steps) + "; " + how)


# --- keys and browser ------------------------------------------------------


class _Firefox:
    """Firefox on the two local pages in a profile of its own, made new each
    time; afterwards the browser processes that started with it are ended
    and the profile is removed."""

    def __init__(self, run):
        self.run = run
        self.before = None

    def __enter__(self):
        vm = self.run.vm
        vm.ssh(f"rm -rf {PROFILE} && mkdir -p {PROFILE} && cat > {PROFILE}/user.js",
               input=FIREFOX_PREFS)
        vm.ssh(f"cat > ~/{REMOTE}/page1.html", input=PAGE_ONE)
        vm.ssh(f"cat > ~/{REMOTE}/page2.html", input=PAGE_TWO)
        self.before = _processes(self.run)
        # GNOME_ACCESSIBILITY: this Firefox shows its windows and tabs to
        # AT-SPI whatever the session's switch (apps_seen) says.
        vm.session_background("firefox", "env", "GNOME_ACCESSIBILITY=1", "firefox",
                              "--no-remote", "--profile", f"{vm.home}/{REMOTE}/ttff-profile",
                              f"file://{vm.home}/{REMOTE}/page1.html")
        return self

    def title(self, part, timeout=30):
        """The Firefox window whose title holds *part*, once there is one."""
        return _until(timeout, lambda: next(
            (w for w in self.run.vm.ui("windows") or [] if part in w["title"]), None))

    def tabs(self):
        """How many page tabs Firefox shows, through AT-SPI."""
        found = self.run.vm.ui("dump", "~Firefox") or []
        return sum(1 for item in found if item["role"] == "page tab")

    def can_go_back(self):
        """Whether Firefox's Back button is enabled: there is a page to go
        back to."""
        back = self.run.vm.ui("find", "button", "Back", "~Firefox", timeout=3)
        return bool(back) and "sensitive" in back["states"]

    def __exit__(self, *_):
        _end_new_browsers(self.run, self.before)
        self.run.vm.ssh("pkill -f ttff-profile", check=False)    # when ps failed first
        _until(10, lambda: self.run.vm.ssh("pgrep -f ttff-profile", check=False).returncode)
        self.run.vm.ssh(f"rm -rf {PROFILE}", check=False)


def _no_firefox(run):
    """A skip Outcome when this VM has no Firefox, else None."""
    if run.vm.ssh("command -v firefox", check=False).returncode:
        return Outcome("skip", "Firefox isn't installed on this VM")
    return None


@check(SECTION, "firefox_go_back", "“go back” goes to the page before")
def firefox_go_back(run):
    if skip := _no_firefox(run):
        return skip
    with _open_apps_on(run), _Firefox(run) as firefox:
        if firefox.title("TT Page Two", 45) is None:
            return Outcome("skip", "Firefox never got to the second test page")
        if not _until(5, firefox.can_go_back):
            return Outcome("fail", "the test made no history: Firefox's Back button is "
                           "disabled on the second page, so “go back” can't be judged",
                           screenshot=run.shot("go-back"))
        run.vm.click_centre()
        _typed, _heard, mark = run.say("go_back", wait=False)
        run.wait_log(mark, "Voice command: go back", "Voice command not done",
                     "Text injected", timeout=30)
        back = firefox.title("TT Page One", 10)
        shot = run.shot("go-back")
        if back is None:
            return Outcome("fail", "the window never showed “TT Page One” after “go back”",
                           screenshot=shot)
        return Outcome("pass", "“go back” showed the first page again", screenshot=shot)


@check(SECTION, "firefox_new_tab", "“new tab” opens a tab")
def firefox_new_tab(run):
    if skip := _no_firefox(run):
        return skip
    with _open_apps_on(run), _Firefox(run) as firefox:
        if firefox.title("TT Page", 45) is None:
            return Outcome("skip", "Firefox never opened the test page")
        tabs = firefox.tabs()
        if not tabs:
            return Outcome("skip", "Firefox shows no tabs through AT-SPI here")
        run.vm.click_centre()
        _typed, _heard, mark = run.say("new_tab", wait=False)
        run.wait_log(mark, "Voice command: new tab", "Voice command not done",
                     "Text injected", timeout=30)
        more = _until(10, lambda: firefox.tabs() == tabs + 1)
        shot = run.shot("new-tab")
        if not more:
            return Outcome("fail", f"{tabs} tab(s) before “new tab”, {firefox.tabs()} after",
                           screenshot=shot)
        return Outcome("pass", f"“new tab” took Firefox from {tabs} to {tabs + 1} tabs",
                       screenshot=shot)


@check(SECTION, "go_to_in_browser", "“go to example dot com” fills the browser's address bar")
def go_to_in_browser(run):
    if skip := _no_firefox(run):
        return skip
    if why := _action_blocker(run):
        return Outcome("skip", why)
    with _open_apps_on_unlogged(run), _Firefox(run) as firefox:
        if firefox.title("TT Page", 45) is None:
            return Outcome("skip", "Firefox never opened the test page")
        time.sleep(2)
        run.vm.click_centre()
        _typed, _heard, mark = run.say("go_to_example", wait=False)
        run.wait_log(mark, "Voice command: go to an address", "Voice command not done",
                     "Text injected", timeout=30)

        seen = []

        def address():
            bar = run.vm.ui("find", ADDRESS_BAR, "~address", "~Firefox", timeout=3)
            if bar:
                seen.append(bar)
                return bar["text"] if "example.com" in bar.get("text", "") else None
            return None
        text = _until(15, address)
        log = run.log_since(mark)
        shot = run.shot("go-to-in-browser")
        if not seen:
            return Outcome("skip", "Firefox shows no address bar through AT-SPI here",
                           screenshot=shot)
        if not text:
            return Outcome("fail", "the address bar never held example.com; log: "
                           f"{'Voice command: go to an address' in log}", screenshot=shot)
        if "Text injected" in log or "example" in log:
            return Outcome("fail", "the words reached the log or were typed", screenshot=shot)
        return Outcome("pass", f"The address bar holds “{text}”; nothing typed elsewhere",
                       screenshot=shot)


def _forward_browser(run, before, timeout=40):
    """The ui.py window (app, title, index...) of a browser that is new since
    *before* (the applications then) or has the focus now, once there is one."""
    def found():
        return next((w for w in run.vm.ui("windows") or []
                     if (BROWSER.search(w["app"] or "") or BROWSER.search(w["title"] or ""))
                     and (w["app"] not in before or "active" in w["states"])), None)
    return _until(timeout, found)


def _end_new_browsers(run, before):
    """End the browser processes that aren't in *before* (_processes then):
    one already running is the VM user's and is left alone, whatever its
    name. Nothing is ended when *before* is None: every browser would look
    new."""
    now = _processes(run) if before is not None else None
    new = [pid for pid, (name, _args) in (now or {}).items()
           if pid not in before and BROWSER.search(name)]
    if new:
        run.vm.ssh(f"kill {' '.join(new)}", check=False)
        time.sleep(1.5)


def _closing_new_browsers(run, judge):
    """*judge*()'s Outcome, with the browser processes that started meanwhile
    ended afterwards (_end_new_browsers). When the processes couldn't be
    listed first, none is ended and the Outcome says so."""
    before = _processes(run)
    try:
        outcome = judge()
    finally:
        _end_new_browsers(run, before)
    if before is None:
        outcome.detail = "; ".join(filter(None, (
            outcome.detail, "browsers left running: ps couldn't list the processes first")))
    return outcome


def _browser_shows(run, window, part, stale):
    """Whether the browser window that came forward (*window*, ui.py's) has
    *part* in its own address bar or its title, when that title wasn't one
    there before the command (*stale*, every title then): where it went, not
    only that a browser came forward. Another window's bar or title never
    counts, the same browser's other windows included."""
    mine = {"app": window["app"], "index": window["index"]}
    bar = run.vm.ui("find", ADDRESS_BAR, "~address", timeout=3, **mine) or {}
    now = next((w for w in run.vm.ui("windows", **mine) or []), {})
    title = now.get("title") or ""
    return part in (bar.get("text", "") + " " + ("" if title in stale else title)).lower()


def _no_default_browser(run):
    """A skip Outcome when the VM names no default browser, else None."""
    out = run.vm.session("xdg-settings", "get", "default-web-browser", check=False).stdout
    return None if out.strip() else Outcome("skip", "this VM has no default web browser")


def _said_to_browser(run, phrase_id, done, word, timeout):
    """Say *phrase_id* in the test window and judge it: the log has *done*,
    a browser came forward with *word* (of the address or the search) in its
    own address bar or a new title, and it stayed out of the test window and
    the log."""
    run.sink_ready(fresh=True)
    before = _apps(run)
    stale = {title for _name, title, _focused in _windows(run)}
    _typed, _heard, mark = run.say(phrase_id, wait=False)
    run.wait_log(mark, done, "Voice command not done", "Text injected", timeout=timeout)
    window = _forward_browser(run, before)
    shown = window and _until(15, lambda: _browser_shows(run, window, word, stale))
    log, typed = run.log_since(mark), run.sink_text().strip()
    shot = run.shot(phrase_id.replace("_", "-"))
    if typed and "Voice command" not in log:
        return Outcome("misheard", "the computer voice was heard as another sentence",
                       typed=typed, screenshot=shot)
    problems = [f"no “{done}” in the log"] if done not in log else []
    if window is None:
        problems.append("no browser window appeared or came forward")
    if not shown:
        problems.append(f"neither a browser's address bar nor its title holds “{word}”")
    if typed:
        problems.append(f"the sink got text: {typed[:60]!r}")
    if word in log.lower():
        problems.append("the words are in the log")
    if problems:
        return Outcome("fail", "; ".join(problems), typed=typed, screenshot=shot)
    return Outcome("pass", f"A browser (“{window['app']}”) came forward showing “{word}”; the "
                   "words stayed out of the sink and the log", screenshot=shot)


# In the two checks below, "nothing leaked" rests on the test window being empty and
# the words being absent from the log, with "Log transcribed text" off for them;
# "Text injected" only guards the wait.
@check(SECTION, "go_to_outside_browser", "“go to example dot com” in another app opens it in the browser")
def go_to_outside_browser(run):
    if skip := _no_default_browser(run):
        return skip
    with _open_apps_on_unlogged(run):
        return _closing_new_browsers(run, lambda: _said_to_browser(
            run, "go_to_example", "Voice command: go to an address", "example", 30))


@check(SECTION, "web_search", "“web search cheap tires” searches in the default browser")
def web_search(run):
    if why := _action_blocker(run):
        return Outcome("skip", why)
    if skip := _no_default_browser(run):
        return skip
    with _open_apps_on_unlogged(run):
        return _closing_new_browsers(run, lambda: _said_to_browser(
            run, "web_search_tires", "Voice command: web search", "tires", 45))


@check(SECTION, "scroll_down", "“scroll down” scrolls the window under the pointer's focus")
def scroll_down(run):
    with _open_apps_on(run):
        mark = _command(run, "scroll_down", "Voice command: scroll down", mode="long")
        if misheard := _unheard(run, mark, "scroll_down"):
            return misheard
        position = float(run.vm.read(f"~/{REMOTE}/sink.txt.scroll").strip() or 0)
        typed = run.sink_text().strip()
        shot = run.shot("scroll-down")
        problems = []
        if "Voice command: scroll down" not in run.log_since(mark):
            problems.append("no “Voice command: scroll down” in the log")
        if position <= 0:
            problems.append("the text did not scroll")
        if typed:
            problems.append(f"text was typed: {typed[:60]!r}")
        if problems:
            return Outcome("fail", "; ".join(problems), typed=typed, screenshot=shot)
        return Outcome("pass", f"The tall text scrolled down to {position:.0f} px; nothing typed",
                       screenshot=shot)


# --- sentences that only start like a command --------------------------------

SENTENCES = (("sentence_new_tab", "I opened a new tab yesterday.", True),
             ("sentence_go_to", "Go to the store.", True),
             ("sentence_search", "Search for cheap tires.", True),
             ("sentence_copy", "Copy that.", True),
             ("sentence_close_door", "Close the door.", True),
             ("sentence_switch_decaf", "Switch to decaf.", True),
             # Only the words are pinned: how the marks are spaced is spoken_quotes' own test.
             ("sentence_close_quote", "hello", False))
# Logged for "Close the door.", which names no app: it is typed, as it should be.
TYPED_AS_WORDS = "Window command: no app matched; typed as words"


@check(SECTION, "sentences_are_typed", "Sentences that start like a command are typed as words")
def sentences_are_typed(run):
    with _open_apps_on(run):
        run.sink_ready(fresh=True)
        before = _apps(run)
        for phrase, expected, exact in SENTENCES:
            run.sink_ready(fresh=True)
            typed, heard, log = run.say(phrase)
            outcome = run.expect_typed(phrase, expected, typed, heard, exact=exact)
            if outcome.status != "pass":
                outcome.detail = outcome.detail or f"“{expected}” was not typed as it was said"
                outcome.screenshot = run.shot(f"sentence-{phrase}")
                return outcome
            log = log.replace(TYPED_AS_WORDS, "")
            if "Voice command" in log or "Window command" in log:
                return Outcome("fail", f"“{expected}” was taken as a command", typed=typed)
        opened = sorted(_apps(run) - before)
        if opened:
            return Outcome("fail", f"{opened} opened")
        return Outcome("pass", f"All {len(SENTENCES)} were typed as said; no key pressed, "
                       "nothing opened or closed")
