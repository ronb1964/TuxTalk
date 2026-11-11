"""Hotkeys the desktop holds back for TuxTalk, and the ones it leaves to the
app, and the dictation keys that need no Fn (Right Ctrl, Right Alt, Scroll
Lock). KDE, GNOME and COSMIC hold a letter shortcut back by its letter, so on
a German keyboard Ctrl+Alt+Y is the key labelled Y (where US keyboards have
Z). And a dictation key pressed with other modifiers (Alt+F8 resizes the
window on KDE and GNOME) belongs to the app, so it mustn't dictate as well.

On GNOME these run after the extension is installed: only it holds keys back.
"""
import json
import sys
import time
from pathlib import Path

from checks_dictation import type_in_terminal
from checks_prefs import PREFS, open_fold
from harness import Abort, Outcome, check, heard_in
from vmlib import REMOTE

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from tuxtalk.hotkey_helper import VERSION  # noqa: E402

SECTION = "Hotkeys and keyboard layouts"
COSMIC_XKB = "~/.config/cosmic/com.system76.CosmicComp/v1/xkb_config"


def _reboot(run):
    """Restart the VM and wait for TuxTalk, which starts at login."""
    mark = run.log_mark()
    run.vm.reboot()
    run.vm.ssh(f"~/{REMOTE}/vmic.sh")     # PipeWire modules don't outlive a reboot
    if run.wait_log(mark, "Dictation service is ready", timeout=120) is None:
        raise Abort("TuxTalk didn't start again after a reboot")
    time.sleep(2)


def _set_layout(run, layout):
    """Switch the desktop's keyboard to *layout*; returns what puts it back."""
    tags = run.vm.spec.tags
    if "gnome" in tags:
        keys = ("sources", "mru-sources")
        before = {k: run.vm.session("gsettings", "get", "org.gnome.desktop.input-sources",
                                    k).stdout.strip() for k in keys}
        for k in keys:
            run.vm.session("gsettings", "set", "org.gnome.desktop.input-sources", k,
                           f"[('xkb', '{layout}')]")

        def restore():
            for k in keys:
                run.vm.session("gsettings", "set", "org.gnome.desktop.input-sources", k,
                               before[k])
    elif "kde" in tags:
        # KWin 6.7 ignores kxkbrc's reloadConfig signal (System Settings'
        # way), and reads the file again only at login.
        before = run.vm.ssh("cat ~/.config/kxkbrc 2>/dev/null", check=False).stdout
        for key, value in (("Use", "true"), ("LayoutList", layout)):
            run.vm.session("kwriteconfig6", "--file", "kxkbrc", "--group", "Layout",
                           "--key", key, value)
        _reboot(run)

        def restore():
            if before:
                run.vm.ssh("cat > ~/.config/kxkbrc", input=before)
            else:
                run.vm.ssh("rm -f ~/.config/kxkbrc")
            _reboot(run)
    else:   # COSMIC applies its config file as soon as it changes
        before = run.vm.ssh(f"cat {COSMIC_XKB} 2>/dev/null", check=False).stdout
        run.vm.ssh(f"mkdir -p $(dirname {COSMIC_XKB}) && cat > {COSMIC_XKB}",
                   input=f'(rules: "", model: "pc105", layout: {json.dumps(layout)}, '
                         'variant: "", options: None, repeat_delay: 600, repeat_rate: 25)\n')

        def restore():
            if before:
                run.vm.ssh(f"cat > {COSMIC_XKB}", input=before)
            else:
                run.vm.ssh(f"rm -f {COSMIC_XKB}")
    time.sleep(2)
    return restore


@check(SECTION, "layout_letter_hotkey",
       "On a German keyboard, Ctrl+Alt+Y is the key labelled Y", desktops=("kde", "gnome", "cosmic"))
def layout_letter_hotkey(run):
    with run.settings_put_back("paste_last_hotkey"):
        restore = _set_layout(run, "de")
        try:
            # QEMU names keys by where they sit on a US keyboard: "y" is the key
            # labelled Z on a German one, "z" the key labelled Y.
            run.sink_ready("other", "TT Other", fresh=True)
            run.vm.tap("z")
            time.sleep(1)
            if run.sink_text("other") != "y":
                return Outcome("error", "The VM's keyboard didn't switch to German (the key "
                               f"labelled Y typed {run.sink_text('other')!r})")
            run.set_config(paste_last_hotkey="Ctrl+Alt+Y")
            run.sink_ready(fresh=True)
            typed, heard, _log = run.say("paste_last")
            run.sink_ready("other", "TT Other", fresh=True)
            mark = run.log_mark()
            run.vm.tap("ctrl", "alt", "y")
            time.sleep(3)
            wrong_key = "Paste last dictation" in run.log_since(mark)
            mark = run.log_mark()
            run.vm.tap("ctrl", "alt", "z")
            run.wait_log(mark, "Paste last dictation", timeout=10)
            time.sleep(3)
            pasted = run.sink_text("other")
            shot = run.shot("german-ctrl-alt-y")
        finally:
            run.close_sink("other")
            restore()
    if wrong_key:
        return Outcome("fail", "Ctrl+Alt with the key labelled Z pasted too",
                       typed=pasted, screenshot=shot)
    if typed and pasted.rstrip(" ") == typed.rstrip(" "):
        return Outcome("pass", "German layout: the key labelled Y pasted the last dictation, "
                       "the one labelled Z did nothing, and no stray letter was typed",
                       typed=pasted, screenshot=shot)
    return Outcome("fail", f"first window: {typed!r}; second window after Ctrl+Alt+Y: "
                   f"{pasted!r}", expected=typed, typed=pasted, heard=heard, screenshot=shot)


@check(SECTION, "modifier_f8_to_app",
       "Alt+F8, Ctrl+F8 and Shift+F8 (voice editing off) go to the app and don't dictate")
def modifier_f8_to_app(run):
    run.sink_ready(fresh=True)
    dictated, shot = [], None
    for mod in ("alt", "ctrl", "shift"):
        mark = run.log_mark()
        run.vm.key_down(mod, "f8")
        time.sleep(0.4)
        run.play("basic")
        time.sleep(0.3)
        if mod == "alt":
            shot = run.shot("alt-f8")   # KDE and GNOME: resizing the window
        run.vm.key_up(mod, "f8")
        run.vm.tap("esc")               # ends a resize
        time.sleep(3)
        if "Recording stopped" in run.log_since(mark):
            dictated.append(f"{mod.capitalize()}+F8")
    stray = run.sink_text()
    # The same window still dictates with plain F8, so the quiet above is real.
    run.sink_ready(fresh=True)
    typed, heard, _log = run.say("basic")
    if dictated or stray.strip():
        return Outcome("fail", f"dictated with: {', '.join(dictated) or 'none'}; "
                       f"typed: {stray!r}", typed=stray, screenshot=shot)
    if not typed:
        return Outcome("fail", "plain F8 afterwards typed nothing, so the check proves nothing",
                       heard=heard, screenshot=shot)
    return Outcome("pass", "No dictation with Alt, Ctrl or Shift held (log: no “Recording "
                   "stopped”); plain F8 still dictates", typed=typed, screenshot=shot)


BASIC = "This is a test of the dictation system."


@check(SECTION, "no_fn_hold_keys", "Right Ctrl and Right Alt dictate as the hold key")
def no_fn_hold_keys(run):
    with run.settings_put_back("hotkey"):
        for name, qcode in (("Right Ctrl", "ctrl_r"), ("Right Alt", "alt_r")):
            run.set_config(hotkey=name)
            run.sink_ready(fresh=True)
            typed, heard, _log = run.say("basic", key=qcode)
            outcome = run.expect_typed("basic", BASIC, typed, heard)
            if outcome.status != "pass":
                outcome.detail = f"held {name}: {outcome.detail}".rstrip(": ")
                outcome.screenshot = run.shot(f"hold-{qcode}")
                return outcome
    return Outcome("pass", "Held Right Ctrl, then Right Alt: each dictated",
                   expected=BASIC, typed=typed.rstrip(" "), heard=heard)


@check(SECTION, "right_ctrl_toggle",
       "Right Ctrl as the tap key: one tap starts, the next tap stops")
def right_ctrl_toggle(run):
    with run.settings_put_back("toggle_hotkey"):
        run.set_config(toggle_hotkey="Right Ctrl")
        run.sink_ready(fresh=True)
        mark = run.log_mark()
        run.vm.tap("ctrl_r")
        time.sleep(0.6)
        run.play("basic")
        time.sleep(0.3)
        run.vm.tap("ctrl_r")
        typed = run.wait_dictation(mark)
        heard = heard_in(run.log_since(mark))
    return run.expect_typed("basic", BASIC, typed, heard)


@check(SECTION, "scroll_lock_terminal",
       "Scroll Lock as the hold key: plain text in a terminal, nothing from the key")
def scroll_lock_terminal(run):
    blind = run.window_blind()
    if blind:
        return Outcome("skip", blind)
    with run.settings_put_back("hotkey"):
        run.set_config(hotkey="Scroll Lock")
        return type_in_terminal(run, "terminal-scroll-lock", key="scroll_lock")


# Left Ctrl+Space

CTRL_SPACE = "Left Ctrl+Space"
LISTEN_FILE = "helper-heard.txt"
OTHER_LISTEN_FILE = "helper-heard-f8.txt"
# What the helper must say to a client watching "L:57" for: Ctrl taps alone
# only as Left Ctrl's own press and release, and the pair as a whole.
CTRL_TAP = ["DOWN 29 -", "UP 29"]
PAIR = ["DOWN 29 -", "DOWN 57 29", "UP 57", "UP 29"]


def helper_report_problem(heard, expected):
    """What is wrong with the lines a helper_listen.py client heard, or
    None: they open with the hello and OK WATCH, and *expected* follows with
    nothing else. An empty file is a problem, never a pass."""
    if len(heard) < 2 or not heard[0].startswith("HELLO 1 ") or heard[1] != "OK WATCH":
        return f"no hello and OK WATCH from the helper (heard {heard[:2]})"
    if heard[2:] != expected:
        return f"the helper reported {heard[2:]}, not {expected}"
    return None


def helper_version(heard):
    """The helper's version from the hello it answered, or None."""
    words = heard[0].split() if heard else []
    return words[2] if len(words) > 2 and words[0] == "HELLO" else None


def _listen(run, name, *specs):
    """Start helper_listen.py in the VM, writing to ~/tt/NAME; the lines it
    has heard once the helper has answered the WATCH (empty when it never
    did)."""
    path = f"~/{REMOTE}/{name}"
    run.vm.ssh(f"rm -f {path}; setsid -f python3 ~/{REMOTE}/helper_listen.py {path} "
               f"{' '.join(specs)} >/dev/null 2>&1 </dev/null")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        heard = _heard(run, name)
        if len(heard) >= 2:
            return heard
        time.sleep(0.5)
    return _heard(run, name)


def _heard(run, name):
    return run.vm.ssh(f"cat ~/{REMOTE}/{name} 2>/dev/null", check=False).stdout.splitlines()


def _stop_listening(run):
    # -f: the process is python3, so its name is no use. The brackets keep the
    # pattern from matching the shell ssh runs pkill in, whose command line
    # holds the pattern too.
    run.vm.ssh("pkill -f '[h]elper_listen.py'", check=False)


def _helper_missing(run):
    """Why the hotkey helper can't be listened to here, or None."""
    if run.vm.ssh("systemctl is-active tuxtalk-hotkeys", check=False).stdout.strip() != "active":
        return "Skipped: no hotkey helper on this VM (the input group is used)"
    return None


@check(SECTION, "ctrl_space_helper_only_the_pair",
       "The helper reports Left Ctrl+Space and never Space alone or Left Ctrl+letter")
def ctrl_space_helper_only_the_pair(run):
    missing = _helper_missing(run)
    if missing:
        return Outcome("skip", missing)
    run.sink_ready(fresh=True)
    try:
        _listen(run, LISTEN_FILE, "L:57", "66")
        _listen(run, OTHER_LISTEN_FILE, "66")
        for _ in range(5):
            run.vm.tap("spc")
        # Never Ctrl+W or Ctrl+Q: they would close the test window.
        for letter in ("a", "z", "l"):
            run.vm.tap("ctrl", letter)
        run.vm.tap("ctrl_r", "spc")
        run.vm.tap("ctrl", "spc")
        time.sleep(1)
        heard, other = _heard(run, LISTEN_FILE), _heard(run, OTHER_LISTEN_FILE)
    finally:
        _stop_listening(run)
    problem = helper_report_problem(heard, CTRL_TAP * 3 + PAIR)
    if problem is None and helper_report_problem(other, []) is not None:
        problem = f"a client watching only F8 heard {other[2:]}"
    if problem:
        return Outcome("fail", problem)
    return Outcome("pass", "Five Space taps, Ctrl+A, Ctrl+Z, Ctrl+L and Right Ctrl+Space were not "
                   "reported as the pair (the Ctrl taps only as Left Ctrl's own press and "
                   "release); Left Ctrl+Space was, in order; a client watching F8 alone heard "
                   "nothing")


@check(SECTION, "ctrl_space_hold", "Left Ctrl+Space as the hold key dictates",
       desktops=("kde", "gnome", "cosmic", "cinnamon"))
def ctrl_space_hold(run):
    with run.settings_put_back("hotkey"):
        run.set_config(hotkey=CTRL_SPACE)
        run.sink_ready(fresh=True)
        typed, heard, _log = run.say("basic", key=("ctrl", "spc"))
        outcome = run.expect_typed("basic", BASIC, typed, heard)
        outcome.screenshot = run.shot("hold-ctrl-space")
    return outcome


@check(SECTION, "ctrl_space_ctrl_kept",
       "Left Ctrl kept down after a Left Ctrl+Space hold is still Ctrl",
       desktops=("kde", "gnome", "cosmic", "cinnamon"))
def ctrl_space_ctrl_kept(run):
    """Space let go first, Ctrl kept down and A tapped: TuxTalk gave the
    desktop its Ctrl back, so that's Ctrl+A, which selects the text already
    in the sink, and the dictation, typed once Ctrl is let go, replaces it.
    An "a" typed, or Ctrl+A swallowed, leaves the old text in the sink."""
    with run.settings_put_back("hotkey"):
        run.set_config(hotkey=CTRL_SPACE)
        run.sink_ready(fresh=True)
        for key in ("o", "l", "d"):
            run.vm.tap(key)
        mark = run.log_mark()
        run.vm.key_down("ctrl", "spc")
        try:
            time.sleep(0.4)
            run.play("basic")
            time.sleep(0.3)
            run.vm.key_up("spc")
            time.sleep(0.5)
            run.vm.tap("a")
            time.sleep(0.3)
        finally:
            run.vm.key_up("ctrl", "spc")
        typed = run.wait_dictation(mark)
        heard = heard_in(run.log_since(mark))
        outcome = run.expect_typed("basic", BASIC, typed, heard)
        outcome.screenshot = run.shot("ctrl-space-ctrl-kept")
    return outcome


@check(SECTION, "ctrl_space_toggle",
       "Left Ctrl+Space as the tap key: one tap starts, the next tap stops",
       desktops=("kde", "gnome", "cosmic", "cinnamon"))
def ctrl_space_toggle(run):
    with run.settings_put_back("toggle_hotkey"):
        run.set_config(toggle_hotkey=CTRL_SPACE)
        run.sink_ready(fresh=True)
        mark = run.log_mark()
        run.vm.tap("ctrl", "spc")
        time.sleep(0.6)
        run.play("basic")
        time.sleep(0.3)
        run.vm.tap("ctrl", "spc")
        typed = run.wait_dictation(mark)
        heard = heard_in(run.log_since(mark))
    return run.expect_typed("basic", BASIC, typed, heard)


@check(SECTION, "ctrl_space_terminal",
       "Left Ctrl+Space in a terminal: plain text, no stray space or Ctrl+Space")
def ctrl_space_terminal(run):
    blind = run.window_blind()
    if blind:
        return Outcome("skip", blind)
    with run.settings_put_back("hotkey"):
        run.set_config(hotkey=CTRL_SPACE)
        return type_in_terminal(run, "terminal-ctrl-space", key=("ctrl", "spc"))


def _input_method_clash(run):
    """(who, None) for the input method on this VM that takes Ctrl+Space, or
    (None, why there is none)."""
    if run.vm.ssh("pgrep -x fcitx5", check=False).returncode == 0:
        return "Fcitx 5", None
    if run.vm.ssh("pgrep -x ibus-daemon", check=False).returncode == 0:
        triggers = run.vm.session("gsettings", "get", "org.freedesktop.ibus.general.hotkey",
                                  "triggers", check=False).stdout
        if "<Control>space" in triggers:
            return "IBus", None
        return None, f"IBus runs here but switches with {triggers.strip()}, not Ctrl+Space"
    return None, "neither Fcitx 5 nor IBus is running on this VM"


@check(SECTION, "ctrl_space_input_method_log",
       "TuxTalk names an input method that uses Ctrl+Space")
def ctrl_space_input_method_log(run):
    who, why = _input_method_clash(run)
    if who is None:
        return Outcome("skip", f"Skipped: {why}")
    with run.settings_put_back("hotkey"):
        mark = run.log_mark()
        run.set_config(hotkey=CTRL_SPACE)
        log = run.wait_log(mark, f"{who} also uses Ctrl+Space", timeout=30)
    if log is None:
        return Outcome("fail", f"{who} is running but the log never said it also uses "
                       "Ctrl+Space")
    return Outcome("pass", f"The log says {who} also uses Ctrl+Space")


@check(SECTION, "ctrl_space_quiet_helper_update",
       "Helper 1.13 gets no question at start; System check offers the new one quietly")
def ctrl_space_quiet_helper_update(run):
    missing = _helper_missing(run)
    if missing:
        return Outcome("skip", missing)
    try:
        version = helper_version(_listen(run, LISTEN_FILE, "66"))
    finally:
        _stop_listening(run)
    if version != "1.13":
        return Outcome("skip", f"Skipped: the helper here is {version}, not 1.13 (checked on a "
                       "VM that still has the old one)")
    with run.settings_put_back("hotkey", "toggle_hotkey"):
        # Keys 1.13 reports: one it doesn't (Left Ctrl+Space) is asked about.
        run.set_config(hotkey="F8", toggle_hotkey="F9")
        return _quiet_update_outcome(run)


def _quiet_update_outcome(run):
    """ctrl_space_quiet_helper_update's look at the start and System check."""
    mark = run.log_mark()
    _reboot(run)
    time.sleep(30)      # the helper offer comes about 20 seconds after the start
    question = run.vm.ui("find", "label", "TuxTalk's hotkey helper needs an update", timeout=3)
    if question is not None:
        # Left open, it would stand in front of every later check here.
        run.vm.ui("click", "button", "Not Now", timeout=5)
    # Only an answered question is logged; "offer skipped" is an error, not one.
    asked_in_log = any(line in run.log_since(mark) for line in (
        "Hotkey helper update offer: not now", "Updated the hotkey helper from the offer"))
    run.dbus("OpenPreferences")
    if run.vm.ui("tab", "Advanced", PREFS, timeout=30) is None:
        raise Abort("Preferences didn't open")
    open_fold(run, "Show system check")
    texts = run.vm.ui("texts", PREFS) or []
    shot = run.shot("system-check-helper-update")
    run.vm.ui("click", "button", "Cancel", PREFS, timeout=5)
    problems = []
    if question is not None or asked_in_log:
        problems.append("TuxTalk asked about updating the helper at start")
    offer = f"(1.13 → {VERSION})"
    if not any(offer in t for t in texts):
        problems.append(f"System check doesn't offer {offer}")
    if not any("✓ Ready" in t for t in texts):
        problems.append("System check doesn't say ✓ Ready")
    if problems:
        return Outcome("fail", "; ".join(problems), screenshot=shot)
    return Outcome("pass", f"No question at start; System check reads ✓ Ready and offers "
                   f"{offer}", screenshot=shot)
