"""First run, from a computer that has never seen TuxTalk to the first
dictation: CLAUDE.md Step 8, "First run". These checks run in order and each
leaves the screens where the next one expects them.
"""
import re
import time

from harness import Abort, Outcome, check
from vmlib import REMOTE

WELCOME = "Welcome to TuxTalk!"
FLAGS = re.compile("[\U0001F1E6-\U0001F1FF]")   # regional-indicator letters make flag emoji


def texts(run, window):
    return run.vm.ui("texts", window) or []


@check("First run", "welcome", "Welcome screen appears and recommends a setup")
def welcome(run):
    run.vm.tap("esc")   # GNOME starts in the overview; leave it before a window opens
    time.sleep(1)
    run.first_run_mark = run.log_mark()
    run.vm.session_background("tuxtalk", f"{run.vm.home}/AppImages/{run.appimage.name}")
    if run.vm.ui("find", "dialog", WELCOME, timeout=120) is None:
        raise Abort("the welcome screen never appeared")
    time.sleep(2)
    shot = run.shot("welcome")
    lines = texts(run, WELCOME)
    card = next((t for t in lines if t.startswith("Parakeet on")), None)
    english = any("For English" in t for t in lines)
    problems = []
    if not (card and english):
        problems.append(f"card line: {card!r}, mentions English: {english}")
    if not ("1. Hold F8 (or a key you pick next)" in lines and "2. Talk" in lines
            and any(t.startswith("3. Let go") for t in lines)):
        problems.append("How It Works doesn't read 1. Hold F8 (or a key you pick next), "
                        "2. Talk, 3. Let go")
    if problems:
        return Outcome("fail", "; ".join(problems), screenshot=shot)
    return Outcome("pass", f"Card: “{card}”, For English; 1. Hold F8 (or a key you pick next), "
                   "2. Talk, 3. Let go",
                   screenshot=shot)


@check("First run", "language_list", "Language list uses native names, no flags")
def language_list(run):
    # Behind the "Change language, model or graphics card" button, closed.
    items = [i["name"] for i in
             run.vm.ui("items", "combo box", "English", WELCOME, hidden=True) or []]
    problems = []
    if run.vm.ui("find", "button", "Change language, model or graphics card", WELCOME,
                 timeout=5) is None:
        problems.append("no “Change language, model or graphics card” button")
    if len(items) != 35:
        problems.append(f"{len(items)} languages, expected 35")
    for wanted in ("Spanish (Español)", "Japanese (日本語)"):
        if wanted not in items:
            problems.append(f"no “{wanted}”")
    flagged = [i for i in items if FLAGS.search(i)]
    if flagged:
        problems.append(f"flags in {flagged[:3]}")
    detail = "; ".join(problems) or f"{len(items)} languages, e.g. {', '.join(items[1:4])}"
    return Outcome("fail" if problems else "pass", detail)


@check("First run", "no_tray_extension", "No system tray: extension ticked, with the amber line",
       desktops=("no-tray",))
def no_tray_extension(run):
    box = run.vm.ui("find", "check box", "~Install GNOME Extension", WELCOME)
    note = any("this desktop has no system tray" in t for t in texts(run, WELCOME))
    ticked = box is not None and "checked" in box["states"]
    # Untick it, as the checklist says: the menu window must then appear at the end.
    if ticked:
        run.vm.ui("click", "check box", "~Install GNOME Extension", WELCOME)
    if ticked and note:
        return Outcome("pass", "Ticked, with “this desktop has no system tray”; unticked for "
                       "the run")
    return Outcome("fail", f"ticked: {ticked}, no-tray line shown: {note}")


@check("First run", "password_prompt", "Fix Typing asks for the password once")
def password_prompt(run):
    run.vm.ui("click", "button", "~Fix Typing", WELCOME, check=True)
    deadline = time.monotonic() + 20
    agents = ""
    while time.monotonic() < deadline:
        # The brackets keep pgrep -f from counting this ssh command's own shell.
        agents = run.vm.ssh("pgrep -c -f '[p]olkit-agent-helper-1'; pgrep -c -x pkexec",
                            check=False).stdout.split()
        if agents and agents[0] != "0":
            break
        time.sleep(0.5)
    time.sleep(1.5)
    shot = run.shot("password-prompt")
    prompts = int(agents[0]) if agents else 0
    pkexecs = int(agents[1]) if len(agents) > 1 else 0
    # Never type a password: cancel, then let the test-only stand-in finish the job.
    run.vm.tap("esc")
    time.sleep(3)
    # COSMIC's prompt ignores Escape. Ending the pkexec it serves closes the
    # prompt on any desktop, and TuxTalk sees a cancelled password.
    run.vm.ssh("sudo -n pkill -x pkexec", check=False)
    time.sleep(2)
    if prompts == 1 and pkexecs == 1:
        return Outcome("manual", "The desktop's password prompt appeared once (cancelled here: "
                       "nobody types a password in a VM). Typing the real password is Ron's "
                       "check.", screenshot=shot)
    return Outcome("fail", f"{prompts} password prompts and {pkexecs} pkexec processes",
                   screenshot=shot)


@check("First run", "fix_typing", "Fix Typing finishes with no restart needed")
def fix_typing(run):
    # /usr/local/bin, not ~/.local/bin: TuxTalk already runs with the session's
    # PATH, and Ubuntu and Zorin put ~/.local/bin on it only when it existed
    # at login.
    run.vm.ssh(f"sudo install -D -m 755 ~/{REMOTE}/pkexec-shim.sh /usr/local/bin/pkexec "
               f"&& rm -f ~/{REMOTE}/pkexec-calls.log")
    run.vm.ui("click", "button", "~Fix Typing", WELCOME, check=True)
    if run.vm.ui("find", "label", "Typing Setup Complete!", timeout=60) is None:
        raise Abort("Fix Typing didn't finish: " + " | ".join(texts(run, WELCOME)[-3:]))
    shot = run.shot("fix-typing")
    message = " ".join(texts(run, "Information"))
    calls = run.vm.read(f"~/{REMOTE}/pkexec-calls.log").strip().splitlines()
    helper = run.vm.ssh("systemctl is-active tuxtalk-hotkeys", check=False).stdout.strip()
    groups = run.vm.ssh("id -nG", check=False).stdout.split()
    run.vm.ui("click", "button", "OK", "Information")
    problems = []
    if "restart" in message.lower():
        problems.append("the message mentions a restart")
    if len(calls) != 1:
        problems.append(f"{len(calls)} root calls (one password prompt expected)")
    if helper != "active":
        problems.append(f"hotkey helper is {helper}")
    if "input" in groups:
        problems.append("the user was put in the input group")
    detail = "; ".join(problems) or ("Helper active, one root call, user not in the input group, "
                                     "no restart")
    return Outcome("fail" if problems else "pass", detail, screenshot=shot)


@check("First run", "hotkey_test", "Hotkey test: F8 and F9 both work")
def hotkey_test(run):
    run.vm.ui("click", "button", "Set it up", WELCOME, check=True)
    window = "Test Your Hotkeys"
    if run.vm.ui("find", "dialog", window, timeout=30) is None and \
            run.vm.ui("find", "frame", window, timeout=5) is None:
        raise Abort("the hotkey test never appeared")
    time.sleep(1.5)
    run.vm.tap("f8", hold=0.6)
    time.sleep(1.5)
    run.vm.tap("f9", hold=0.6)
    time.sleep(1.5)
    shot = run.shot("hotkey-test")
    working = texts(run, window).count("✓ Working!")
    run.vm.ui("click", "button", "Continue", window)
    if working == 2:
        return Outcome("pass", "Both show “✓ Working!”", screenshot=shot)
    raise Abort(f"only {working} of the two hotkeys reported working")


@check("First run", "downloads", "Parakeet really downloads, with a progress bar")
def downloads(run):
    seen_window, seen_bar, shot = False, False, None
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        titles = [w["title"] for w in run.vm.ui("windows") or [] if w["app"] == "tuxtalk"]
        # The processor's copy: "Downloading the speech model"; the graphics
        # card's: "Setting Up Vulkan".
        downloading = [t for t in titles if t.startswith("Downloading") or t == "Setting Up Vulkan"]
        if downloading and not seen_window:
            seen_window = True
            # "~" matches any name: the bar is named after its download for
            # screen readers.
            bar = run.vm.ui("find", "progress bar", "~", downloading[0], timeout=3)
            seen_bar = bar is not None
            shot = run.shot("download")
        if "TuxTalk - Ready!" in titles:
            break
        time.sleep(1)
    log = run.log_since(run.first_run_mark)
    downloaded = re.findall(r"Parakeet Ultra downloaded for (\w+)", log)
    problems = []
    if not downloaded:
        problems.append("the log shows no real download (no “Parakeet Ultra downloaded”)")
    if not seen_window:
        problems.append("no download window was seen")
    elif not seen_bar:
        problems.append("the download window had no progress bar")
    if problems:
        return Outcome("fail", "; ".join(problems), screenshot=shot)
    return Outcome("pass", "Download window with a progress bar; log: “Parakeet Ultra downloaded "
                   f"for {', '.join(downloaded)}”", screenshot=shot)


@check("First run", "setup_complete", "Setup Complete: three steps and the F9 and Ctrl+Alt+P tips")
def setup_complete(run):
    window = "TuxTalk - Ready!"
    if run.vm.ui("find", "label", "Setup Complete!", window, timeout=120) is None:
        raise Abort("the Setup Complete screen never appeared")
    lines = texts(run, window)
    shot = run.shot("setup-complete")
    wanted = {"1. Hold F8": "the first step",
              "Or tap F9 to start": "the F9 tip",
              "Press Ctrl+Alt+P to type it again": "the Ctrl+Alt+P tip"}
    problems = [f"no {what}" for needle, what in wanted.items()
                if not any(needle in t for t in lines)]
    pickers = [w["name"] for w in run.vm.ui("dump", window) or []
               if w["role"] in ("combo box", "radio button")]
    if pickers:
        problems.append(f"model choices on it: {pickers}")
    if problems:
        return Outcome("fail", "; ".join(problems), screenshot=shot)
    return Outcome("pass", "“Setup Complete!” with the three steps, the F9 and Ctrl+Alt+P tips "
                   "and no model picker", screenshot=shot)


@check("First run", "ready", "Start Using TuxTalk, then the menu window (no tray)")
def ready(run):
    if run.vm.ui("find", "button", "Start Using TuxTalk!", timeout=120) is None:
        raise Abort("the Ready screen never appeared")
    shot = run.shot("ready")
    mark = run.log_mark()
    run.vm.ui("click", "button", "Start Using TuxTalk!", check=True)
    if run.wait_log(mark, "Dictation service is ready", timeout=120) is None:
        raise Abort("the dictation service never reported ready")
    if run.vm.spec.tray:
        return Outcome("pass", "Dictation service ready", screenshot=shot)
    log = run.log_since(mark)
    menu = run.vm.ui("find", "frame", "TuxTalk", timeout=10)
    menu_shot = run.shot("menu-window")
    if "No system tray and no extension running" in log and menu is not None:
        run.vm.tap("esc")   # close it, so the test windows get the clicks
        return Outcome("pass", "Menu window opened by itself (log: “No system tray and no "
                       "extension running”)", screenshot=menu_shot)
    return Outcome("fail", f"menu window found: {menu is not None}", screenshot=menu_shot)


@check("First run", "defaults", "Default settings are right")
def defaults(run):
    cfg = run.config()
    wanted = {"model": '"parakeet-v3"', "device": run.vm.spec.expected_device,
              "auto_period": "true", "auto_timeout_minutes": "5", "language_mode": '"auto"'}
    wrong = [f"{k} = {cfg.get(k)} (expected {v})" for k, v in wanted.items() if cfg.get(k) != v]
    if wrong:
        return Outcome("fail", "; ".join(wrong))
    return Outcome("pass", ", ".join(f"{k} = {v}" for k, v in wanted.items()))


@check("First run", "helper_hotkeys", "Hotkeys come from the hotkey helper")
def helper_hotkeys(run):
    log = run.log_since(run.first_run_mark)
    if "Hotkeys come from the TuxTalk hotkey helper" in log:
        return Outcome("pass", "Log: “Hotkeys come from the TuxTalk hotkey helper”")
    return Outcome("fail", "The log doesn't say the hotkeys come from the helper")


@check("First run", "ultra", "Dictation runs on Parakeet Ultra, and About credits it")
def ultra(run):
    log = run.log_since(run.first_run_mark)
    cpu = re.search(r"Loading Parakeet from \S*/parakeet-ultra", log)
    gpu = "Parakeet on the graphics card from ggml-parakeet-ultra-q8_0.bin" in log
    problems = [] if cpu or gpu else ["the log never loaded Parakeet Ultra's files"]
    run.dbus("ShowAbout")
    about = " ".join(texts(run, "About TuxTalk"))
    shot = run.shot("about")
    problems += [f"About doesn't credit {name}" for name in ("Moondream", "NVIDIA", "Olicorne")
                 if name not in about]
    run.vm.ui("click", "button", "Close", "About TuxTalk", timeout=5)
    if problems:
        return Outcome("fail", "; ".join(problems), screenshot=shot)
    where = "the graphics card" if gpu else "the processor"
    return Outcome("pass", f"Parakeet Ultra on {where}; About credits Moondream, NVIDIA and "
                   "Olicorne", screenshot=shot)
