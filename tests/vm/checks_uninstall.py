"""Uninstall TuxTalk (uninstall.py): Preferences -> Updates & Backup ->
Uninstall TuxTalk..., and tuxtalk --uninstall with TuxTalk not running. Last
in a run, since it takes everything away. RELEASE_CHECKLIST.md, the "Uninstall"
line. The password goes through the pkexec stand-in that first run put in,
which logs each call: one call is one password prompt for a real user.
"""
import json
import time

from checks_firstrun import WELCOME
from harness import Outcome, check
from vmlib import REMOTE

SECTION = "Uninstall"
PREFS = "TuxTalk Preferences"
QUESTION = "Uninstall TuxTalk?"
DONE = "TuxTalk is uninstalled"
# AT-SPI names a message box by its kind, not its title.
ASK_BOX, DONE_BOX = "Question", "Information"
DELETE_BOX = "Also delete my settings, Your Words, snippets and stats"
# Its words follow what the folder holds (uninstall.shared_label).
SHARED_BOX = "~in the shared download folder (other apps may use"
UUID = "talktype@ronb1964.github.io"

# One look at everything the uninstall should take away, as JSON. The
# patterns in brackets keep grep from finding this script's own command line.
STATE = r"""
apps=$(ls -A ~/AppImages 2>/dev/null | tr '\n' ' ')
data=$(ls -A ~/.local/share/TuxTalk 2>/dev/null | tr '\n' ' ')
config=$(ls -A ~/.config/tuxtalk 2>/dev/null | tr '\n' ' ')
models=$(ls -A ~/.cache/huggingface/hub 2>/dev/null | grep -E 'Systran|parakeet|whisper|Qwen' | tr '\n' ' ')
rules=$(ls /etc/udev/rules.d | grep -E '[t]uxtalk|[t]alktype' | tr '\n' ' ')
procs=$(ps -u "$USER" -o args= | grep -E '[t]uxtalk\.|[T]uxTalk-|[w]hisper-server|[l]lama-server' | grep -v ' tt/' | head -3 | tr '\n' ';')
left=$(find ~ -path ~/tt -prune -o \( -iname '*tuxtalk*' -o -iname '*talktype*' \) -print 2>/dev/null | grep -vx "$HOME/tt" | grep -vxF -f ~/tt/before-install.txt | head -20 | tr '\n' ' ')
python3 - "$apps" "$data" "$config" "$models" "$rules" "$procs" "$left" <<'EOF'
import json, os, subprocess, sys
names = ["appimages", "data", "config", "models", "rules", "processes", "left"]
out = dict(zip(names, (a.strip() for a in sys.argv[1:])))
out["helper"] = subprocess.run(["systemctl", "is-active", "tuxtalk-hotkeys"],
                               capture_output=True, text=True).stdout.strip()
out["helper_file"] = os.path.exists("/usr/local/libexec/tuxtalk/tuxtalk-hotkeys")
home = os.path.expanduser("~")
for key, path in (("autostart", ".config/autostart/tuxtalk.desktop"),
                  ("launcher", ".local/share/applications/tuxtalk.desktop"),
                  ("extension", ".local/share/gnome-shell/extensions/talktype@ronb1964.github.io")):
    out[key] = os.path.lexists(os.path.join(home, path))
print(json.dumps(out))
EOF
"""


def state(run):
    return json.loads(run.vm.ssh("bash -s", input=STATE).stdout.strip().splitlines()[-1])


def root_calls(run):
    return len(run.vm.read(f"~/{REMOTE}/pkexec-calls.log").strip().splitlines())


def texts(run, window):
    return [t for t in (run.vm.ui("texts", window) or []) if t]


def quit_tuxtalk(run):
    """Quit, then anything left. Killing only the AppImage's own process
    would unmount its files under a TuxTalk that's still running."""
    run.dbus("Quit")
    time.sleep(4)
    run.vm.ssh("pkill -f '[t]uxtalk\\.'; sleep 1; pkill -f '[T]uxTalk-v'", check=False)
    time.sleep(2)


def ask_from_preferences(run):
    """Preferences -> Updates & Backup -> Uninstall TuxTalk...; the question's
    text, or None if it never came."""
    run.dbus("OpenPreferences")
    if run.vm.ui("find", "page tab", "Updates & Backup", PREFS, timeout=30) is None:
        return None
    run.vm.ui("tab", "Updates & Backup", PREFS)
    time.sleep(0.8)
    run.vm.ui("click", "button", "~Uninstall TuxTalk", PREFS, check=True)
    if run.vm.ui("find", "label", QUESTION, ASK_BOX, timeout=30) is None:
        return None
    return texts(run, ASK_BOX)


def wait_done(run, timeout=180):
    """The closing message's text once it shows (then closed with OK), or None."""
    if run.vm.ui("find", "label", DONE, DONE_BOX, timeout=timeout) is None:
        return None
    words = texts(run, DONE_BOX)
    shot = run.shot("uninstall-done")
    run.vm.ui("click", "button", "OK", DONE_BOX)
    time.sleep(3)
    return words, shot


@check(SECTION, "uninstall_cancel", "Uninstall asks first, lists what's here, Cancel changes nothing")
def uninstall_cancel(run):
    before = state(run)
    words = ask_from_preferences(run)
    if words is None:
        return Outcome("fail", "Preferences has no Uninstall TuxTalk… button, or no question came")
    shot = run.shot("uninstall-question")
    box = run.vm.ui("find", "check box", DELETE_BOX, ASK_BOX, timeout=5)
    shared = run.vm.ui("find", "check box", SHARED_BOX, ASK_BOX, timeout=5)
    run.vm.ui("click", "button", "Cancel", ASK_BOX, check=True)
    time.sleep(2)
    after = state(run)
    run.vm.ui("click", "button", "Cancel", PREFS)
    question = " ".join(words)
    problems = []
    for wanted in ("TuxTalk itself (its AppImage)", "hotkey helper", "to its own folder (",
                   "starting at login", "password once"):
        if wanted not in question:
            problems.append(f"the question doesn't say “{wanted}”")
    if box is None or "checked" in box["states"]:
        problems.append("the delete-my-settings box is missing or ticked")
    # The box shows only when the shared cache holds TuxTalk's models.
    if bool(before["models"]) != (shared is not None):
        problems.append(f"the shared-models box is {'missing' if shared is None else 'shown'} "
                        f"with models {before['models']!r}")
    elif shared is not None and "checked" in shared["states"]:
        problems.append("the shared-models box is ticked")
    for key in ("appimages", "helper", "autostart", "models", "data"):
        if before[key] != after[key]:
            problems.append(f"Cancel changed {key}: {before[key]!r} -> {after[key]!r}")
    if before["helper"] != "active":
        problems.append(f"the hotkey helper wasn't running before ({before['helper']})")
    return Outcome("fail" if problems else "pass",
                   "; ".join(problems) or "Asked first, listed the AppImage, helper, downloads "
                   "and login entry; Cancel left everything", screenshot=shot)


@check(SECTION, "uninstall_keep", "Uninstall from Preferences: everything goes but the settings")
def uninstall_keep(run):
    calls = root_calls(run)
    models = state(run)["models"]
    if ask_from_preferences(run) is None:
        return Outcome("fail", "no question came")
    mark = run.log_mark()
    run.vm.ui("click", "button", "Uninstall", ASK_BOX, check=True)
    finished = wait_done(run)
    if finished is None:
        return Outcome("fail", "no “TuxTalk is uninstalled” message: "
                       + run.log_since(mark)[-300:], screenshot=run.shot("uninstall-stuck"))
    words, shot = finished
    time.sleep(3)
    now = state(run)
    enabled = run.vm.session("gsettings", "get", "org.gnome.shell", "enabled-extensions",
                             check=False).stdout
    problems = []
    if root_calls(run) - calls != 1:
        problems.append(f"{root_calls(run) - calls} password prompts (one expected)")
    if now["helper"] == "active" or now["helper_file"]:
        problems.append(f"hotkey helper still there ({now['helper']})")
    for key in ("rules", "appimages", "processes"):
        if now[key]:
            problems.append(f"{key} left: {now[key]}")
    if now["models"] != models:
        problems.append(f"the shared models changed, though the box was unticked: "
                        f"{models!r} -> {now['models']!r}")
    for key in ("autostart", "launcher", "extension"):
        if now[key]:
            problems.append(f"the {key} is still there")
    if UUID in enabled:
        problems.append("GNOME still lists the extension as enabled")
    if set(now["data"].split()) - {"stats.json", "recent-transcripts.json"}:
        problems.append(f"downloads left in ~/.local/share/TuxTalk: {now['data']}")
    if "config.toml" not in now["config"]:
        problems.append("the settings were deleted, though the box was unticked")
    if "Uninstalled TuxTalk" not in run.log_since(mark):
        problems.append("the log doesn't say “Uninstalled TuxTalk”")
    detail = "; ".join(problems) or ("One password prompt; helper, rules, AppImage, downloads, "
                                     "launcher, autostart and extension gone; settings and "
                                     "shared models kept. "
                                     "Message: " + " ".join(words[1:])[:200])
    return Outcome("fail" if problems else "pass", detail, screenshot=shot)


@check(SECTION, "uninstall_reinstall", "Installing again starts first-run setup")
def uninstall_reinstall(run):
    name = run.appimage.name
    run.vm.put(run.appimage, f"{REMOTE}/{name}")
    run.vm.ssh(f"mkdir -p ~/AppImages && mv -f ~/{REMOTE}/{name} ~/AppImages/{name} "
               f"&& chmod +x ~/AppImages/{name}")
    run.vm.session_background("tuxtalk", f"{run.vm.home}/AppImages/{name}")
    welcome = run.vm.ui("find", "dialog", WELCOME, timeout=120)
    shot = run.shot("uninstall-reinstalled")
    quit_tuxtalk(run)
    if welcome is None:
        return Outcome("fail", "the welcome screen didn't come back", screenshot=shot)
    return Outcome("pass", "The welcome screen opened: setup starts over", screenshot=shot)


@check(SECTION, "uninstall_all", "tuxtalk --uninstall, settings and shared models too: "
                                 "nothing of TuxTalk is left")
def uninstall_all(run):
    name = run.appimage.name
    run.vm.session_background("uninstall", f"{run.vm.home}/AppImages/{name}", "--uninstall")
    if run.vm.ui("find", "label", QUESTION, ASK_BOX, timeout=60) is None:
        return Outcome("fail", "tuxtalk --uninstall asked nothing",
                       screenshot=run.shot("uninstall-cli-none"))
    run.vm.ui("click", "check box", DELETE_BOX, ASK_BOX, check=True)
    if run.vm.ui("find", "check box", SHARED_BOX, ASK_BOX, timeout=5) is not None:
        run.vm.ui("click", "check box", SHARED_BOX, ASK_BOX, check=True)
    run.vm.ui("click", "button", "Uninstall", ASK_BOX, check=True)
    finished = wait_done(run)
    if finished is None:
        return Outcome("fail", "no “TuxTalk is uninstalled” message",
                       screenshot=run.shot("uninstall-cli-stuck"))
    _words, shot = finished
    time.sleep(2)
    now = state(run)
    problems = [f"{key}: {now[key]}" for key in ("appimages", "data", "config", "models",
                                                  "processes", "left") if now[key]]
    return Outcome("fail" if problems else "pass",
                   "; ".join(problems) or "Nothing named TuxTalk or TalkType left in the home "
                   "folder", screenshot=shot)
