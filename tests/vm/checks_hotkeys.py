"""Hotkeys the desktop holds back for TuxTalk, and the ones it leaves to the
app, and the dictation keys that need no Fn (Right Ctrl, Right Alt, Scroll
Lock). KDE, GNOME and COSMIC hold a letter shortcut back by its letter, so on
a German keyboard Ctrl+Alt+Y is the key labelled Y (where US keyboards have
Z). And a dictation key pressed with other modifiers (Alt+F8 resizes the
window on KDE and GNOME) belongs to the app, so it mustn't dictate as well.

On GNOME these run after the extension is installed: only it holds keys back.
"""
import json
import time

from checks_dictation import type_in_terminal
from harness import Abort, Outcome, check, heard_in
from vmlib import REMOTE

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
        run.set_config(paste_last_hotkey="Ctrl+Alt+P")
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
    try:
        for name, qcode in (("Right Ctrl", "ctrl_r"), ("Right Alt", "alt_r")):
            run.set_config(hotkey=name)
            run.sink_ready(fresh=True)
            typed, heard, _log = run.say("basic", key=qcode)
            outcome = run.expect_typed("basic", BASIC, typed, heard)
            if outcome.status != "pass":
                outcome.detail = f"held {name}: {outcome.detail}".rstrip(": ")
                outcome.screenshot = run.shot(f"hold-{qcode}")
                return outcome
    finally:
        run.set_config(hotkey="F8")
    return Outcome("pass", "Held Right Ctrl, then Right Alt: each dictated",
                   expected=BASIC, typed=typed.rstrip(" "), heard=heard)


@check(SECTION, "right_ctrl_toggle",
       "Right Ctrl as the tap key starts and stops when it's let go")
def right_ctrl_toggle(run):
    run.set_config(toggle_hotkey="Right Ctrl")
    try:
        run.sink_ready(fresh=True)
        mark = run.log_mark()
        run.vm.tap("ctrl_r")
        time.sleep(0.6)
        run.play("basic")
        time.sleep(0.3)
        run.vm.tap("ctrl_r")
        typed = run.wait_dictation(mark)
        heard = heard_in(run.log_since(mark))
    finally:
        run.set_config(toggle_hotkey="F9")
    return run.expect_typed("basic", BASIC, typed, heard)


@check(SECTION, "scroll_lock_terminal",
       "Scroll Lock as the hold key: plain text in a terminal, nothing from the key")
def scroll_lock_terminal(run):
    blind = run.window_blind()
    if blind:
        return Outcome("skip", blind)
    run.set_config(hotkey="Scroll Lock")
    try:
        return type_in_terminal(run, "terminal-scroll-lock", key="scroll_lock")
    finally:
        run.set_config(hotkey="F8")
