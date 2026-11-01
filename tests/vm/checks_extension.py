"""GNOME with the TuxTalk extension: installed from Preferences, then a
reboot (an extension only loads at login). The reboot also checks that
TuxTalk and the hotkey helper start by themselves. With the extension the
checks that need to know the focused window run again.
"""
import json
import re
import time
from pathlib import Path

from checks_dictation import type_in_terminal
from checks_prefs import open_fold
from checks_send import switch_during_countdown
from harness import Abort, Outcome, check
from vmlib import REMOTE

PREFS = "TuxTalk Preferences"
UUID = "talktype@ronb1964.github.io"
LOCAL_EXTENSION = Path(__file__).resolve().parents[2] / "gnome-extension" / UUID
SECTION = "With the GNOME extension"


@check(SECTION, "ext_install", "Preferences installs the GNOME extension", desktops=("gnome",))
def ext_install(run):
    run.dbus("OpenPreferences")
    if run.vm.ui("tab", "Advanced", PREFS, timeout=30) is None:
        raise Abort("Preferences didn't open")
    open_fold(run, "Show system check")
    run.vm.ui("click", "button", "Install Extension", PREFS, check=True)
    if run.vm.ui("click", "button", "~Yes", timeout=15) is None:
        raise Abort("no “Install GNOME Extension?” question")
    done = run.vm.ui("find", "label", "Extension Installed Successfully!", timeout=90)
    shot = run.shot("extension-installed")
    run.vm.ui("click", "button", "OK", timeout=5)
    run.vm.ui("click", "button", "Cancel", PREFS, timeout=5)
    if done is None:
        raise Abort("the extension didn't install (no “Extension Installed Successfully!”)")
    folder = f"~/.local/share/gnome-shell/extensions/{UUID}"
    found = re.search(r'"version": *(\d+)', run.vm.read(f"{folder}/metadata.json"))
    published = int(found.group(1)) if found else None
    # Preferences downloads it from the newest GitHub release, so before a
    # release goes out that's the previous version's extension. The one this
    # release ships goes in its place before the reboot loads it, so the
    # checks after it test what users will get.
    ours = json.loads((LOCAL_EXTENSION / "metadata.json").read_text())["version"]
    if published is None or published < ours:
        for part in LOCAL_EXTENSION.iterdir():
            # scp takes a path without "~/" from the home folder.
            run.vm.put(part, f"{folder[2:]}/{part.name}")
        return Outcome("pass", f"“Extension Installed Successfully!” (version {published}, from "
                       f"the newest published release); replaced by this release's version "
                       f"{ours} before the reboot", screenshot=shot)
    return Outcome("pass", f"“Extension Installed Successfully!” (version {published}, from the "
                   "newest published release)", screenshot=shot)


@check(SECTION, "ext_after_reboot",
       "After a reboot TuxTalk and the hotkey helper start by themselves", desktops=("gnome",))
def ext_after_reboot(run):
    run.vm.reboot()
    run.vm.ssh(f"~/{REMOTE}/vmic.sh")     # PipeWire modules don't outlive a reboot
    # Only log lines from this boot: they start with their time, like uptime -s.
    boot = run.vm.ssh("date -d \"$(uptime -s)\" '+%Y-%m-%d %H:%M:%S'").stdout.strip()
    deadline = time.monotonic() + 120
    log = ""
    while time.monotonic() < deadline:
        lines = run.vm.read("~/.config/tuxtalk/tuxtalk.log").splitlines()
        log = "\n".join(line for line in lines if line[:19] >= boot)
        if "Dictation service is ready" in log:
            break
        time.sleep(3)
    helper = run.vm.ssh("systemctl is-active tuxtalk-hotkeys", check=False).stdout.strip()
    shot = run.shot("after-reboot")
    problems = []
    if "Dictation service is ready" not in log:
        problems.append("TuxTalk didn't start at login")
    if helper != "active":
        problems.append(f"the hotkey helper is {helper}")
    if "Hotkeys come from the TuxTalk hotkey helper" not in log:
        problems.append("the log doesn't say the hotkeys come from the helper")
    if problems:
        raise Abort("; ".join(problems))
    return Outcome("pass", "TuxTalk started at login; helper active; log: “Hotkeys come from the "
                   "TuxTalk hotkey helper”", screenshot=shot)


@check(SECTION, "ext_active", "The extension is running after the login", desktops=("gnome",))
def ext_active(run):
    info = run.vm.session("gnome-extensions", "info", UUID, check=False).stdout
    state = next((line.split(":", 1)[1].strip() for line in info.splitlines()
                  if line.strip().startswith("State")), "not installed")
    if state in ("ACTIVE", "ENABLED"):
        return Outcome("pass", f"gnome-extensions says {state}")
    return Outcome("fail", f"gnome-extensions says {state}")


@check(SECTION, "ext_terminal", "Terminal: plain text and no stray F8 (with the extension)",
       desktops=("gnome",))
def ext_terminal(run):
    return type_in_terminal(run, "ext-terminal")


@check(SECTION, "ext_send_switch", "Switching windows during the send wait (with the extension)",
       desktops=("gnome",))
def ext_send_switch(run):
    run.set_config(press_enter_after=True, press_enter_delay=2.0)
    outcome = switch_during_countdown(run, "ext-send-switch")
    run.set_config(press_enter_after=False)
    return outcome
