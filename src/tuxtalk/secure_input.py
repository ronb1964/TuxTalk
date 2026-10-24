"""Setting up hotkeys and typing without the "input" group.

TuxTalk used to ask for the user to be put in the "input" group: hotkeys
need to read the keyboard, and typing needs /dev/uinput. That group lets
every program the user runs read every key, passwords included.

This sets up the safer way, in the same single password prompt:

1. The hotkey helper (hotkey_helper.py) as a system service. It reads the
   keyboards under a throwaway user (systemd's DynamicUser) that has no
   network, no home folder, a read-only system and no way to gain more
   rights, and tells the user at the screen only about hotkeys.
2. A udev rule that gives /dev/uinput to the user at the screen (logind's
   "uaccess"), the way Steam does for its controller support. Programs can
   then make a virtual keyboard to type with, but /dev/uinput can't be used
   to read anything.

Nothing needs a restart: the service starts at once, and logind grants the
uinput access as soon as udev re-applies its rules.

The helper is copied to a root-owned folder, so no program the user runs
can change what the service runs. The install script goes to pkexec on
standard input, never through a file the user's programs could edit while
the password dialog is open.
"""
import os
import subprocess
import time

from .logger import setup_logger

logger = setup_logger(__name__)

SERVICE = "tuxtalk-hotkeys.service"
HELPER_DIR = "/usr/local/libexec/tuxtalk"
HELPER_PATH = f"{HELPER_DIR}/tuxtalk-hotkeys"
UNIT_PATH = f"/etc/systemd/system/{SERVICE}"
UINPUT_RULE_PATH = "/etc/udev/rules.d/70-tuxtalk-uinput.rules"
PYTHON = "/usr/bin/python3"

# Numbered below 73, where systemd's 73-seat-late.rules turns the uaccess tag
# into an access list for the user at the screen.
UINPUT_RULE = """\
# TuxTalk: the user at the screen may create virtual keyboards (to type
# dictated text), as Steam's 60-steam-input.rules does. /dev/uinput can't be
# used to read keys. Remove this file, or uninstall from TuxTalk, to undo it.
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
"""

UNIT = f"""\
[Unit]
Description=TuxTalk hotkey helper (hotkeys, and Right Ctrl or Right Alt going down; never typing)
Documentation=https://github.com/ronb1964/TuxTalk

[Service]
Type=simple
ExecStart={PYTHON} -I -S {HELPER_PATH}
Restart=on-failure
RestartSec=2
# select() can't watch descriptors from 1024 up; the helper never needs many.
LimitNOFILE=512
# A flood of events from programs' virtual keyboards can't take the machine.
CPUQuota=25%
MemoryMax=64M
TasksMax=4

# A throwaway user whose only extra right is reading the keyboards.
DynamicUser=yes
SupplementaryGroups=input
RuntimeDirectory=tuxtalk-hotkeys
RuntimeDirectoryMode=0755
UMask=0077

# Nothing else: no devices but input ones, no network, no home folders, no
# writing anywhere but its own folder in /run, no way to gain privileges.
DevicePolicy=closed
DeviceAllow=char-input rw
PrivateNetwork=yes
RestrictAddressFamilies=AF_UNIX
IPAddressDeny=any
ProtectHome=yes
ProtectSystem=strict
PrivateTmp=yes
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible
ProcSubset=pid
LockPersonality=yes
MemoryDenyWriteExecute=yes
RestrictRealtime=yes
RestrictNamespaces=yes
RestrictSUIDSGID=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources @mount @swap @reboot @raw-io @module @obsolete

[Install]
WantedBy=multi-user.target
"""

PKEXEC_TIMEOUT = 300


def helper_source():
    """The helper as it's installed: this TuxTalk's own hotkey_helper.py."""
    from . import hotkey_helper
    with open(hotkey_helper.__file__, encoding="utf-8") as f:
        return f.read()


def _heredoc(path, content, delimiter):
    if delimiter in content:
        raise ValueError(f"{delimiter} appears in the content for {path}")
    return f"cat > {path} <<'{delimiter}'\n{content}{'' if content.endswith(chr(10)) else chr(10)}{delimiter}\n"


def _install_file(path, content, delimiter, mode):
    """Shell lines writing *content* to *path* the safe way: into a new file
    beside it, then moved over it, so it's never there half-written or
    with the wrong permissions."""
    folder = os.path.dirname(path)
    name = os.path.basename(path)
    return (f'tmp=$(mktemp {folder}/.{name}.XXXXXX)\n'
            + _heredoc('"$tmp"', content, delimiter)
            + f'chown root:root "$tmp"\nchmod {mode} "$tmp"\nmv -fT "$tmp" {path}\n')


def install_script(leave_group=False):
    """The script pkexec runs as root to set it all up. With *leave_group*
    (switching from the input group) the user also leaves that group, in
    the same password prompt."""
    leave = leave_group_lines() if leave_group else ""
    return f"""set -euo pipefail
umask 022
# The folder the service runs from must be root's own, never a link.
if [ -L {HELPER_DIR} ] || [ -L {os.path.dirname(HELPER_DIR)} ]; then
    echo "{HELPER_DIR} is a symbolic link; not installing there" >&2
    exit 1
fi
install -d -m 0755 -o root -g root {HELPER_DIR}
{_install_file(HELPER_PATH, helper_source(), "TUXTALK_HELPER_EOF", "0755")}{_install_file(UNIT_PATH, UNIT, "TUXTALK_UNIT_EOF", "0644")}{_install_file(UINPUT_RULE_PATH, UINPUT_RULE, "TUXTALK_RULE_EOF", "0644")}{leave}systemctl daemon-reload
systemctl enable {SERVICE}
# restart, not start: a TuxTalk update brings a new helper with it
systemctl restart {SERVICE}
# Until uinput is loaded /dev/uinput is a bare static node the trigger can't
# reach, and typing waited for a reboot (Fedora, 2026-10-22). The test VMs
# didn't show it: their SPICE agent loads uinput at boot.
modprobe uinput 2>/dev/null || true
udevadm control --reload-rules
udevadm trigger --action=change --sysname-match=uinput
for i in $(seq 50); do
    [ -S /run/tuxtalk-hotkeys/hotkeys.sock ] && exit 0
    sleep 0.1
done
echo "The hotkey helper didn't start. See: journalctl -u {SERVICE}" >&2
exit 1
"""


def uninstall_lines():
    """Shell lines, run as root, taking the helper and both typing rules
    (this one and the input group's old one) away again. Part of the
    uninstall's root script (uninstall.py)."""
    from .uinput_helper import UDEV_RULE_PATH
    return f"""systemctl disable --now {SERVICE} 2>/dev/null
rm -f {UNIT_PATH} {HELPER_PATH} {UINPUT_RULE_PATH} {UDEV_RULE_PATH}
rmdir --ignore-fail-on-non-empty {HELPER_DIR} 2>/dev/null
systemctl daemon-reload
udevadm control --reload-rules
udevadm trigger --action=change --sysname-match=uinput
if systemctl is-active --quiet {SERVICE} || [ -e {UNIT_PATH} ] || [ -e {HELPER_PATH} ]; then
    echo "{NOT_REMOVED} helper"
fi
if [ -e {UINPUT_RULE_PATH} ] || [ -e {UDEV_RULE_PATH} ]; then
    echo "{NOT_REMOVED} rule"
fi
"""


def leave_group_lines():
    """Shell lines taking the user who asked (pkexec's PKEXEC_UID, which they
    can't fake) out of the input group, with the old rule that gave that
    group /dev/uinput."""
    from .uinput_helper import UDEV_RULE_PATH
    return ('user=$(id -nu "${PKEXEC_UID:?}")\n'
            'gpasswd -d "$user" input || true\n'
            + _still_in_group_check()
            + f'rm -f {UDEV_RULE_PATH}\n')


# Printed by the root script when the user is still in the input group after
# gpasswd (the group lives in /usr/lib/group on an immutable system, or
# gpasswd failed), so Preferences doesn't claim they left it.
STILL_IN_GROUP = "TUXTALK_STILL_IN_INPUT_GROUP"

# Printed by the uninstall's root script when the package manager refused
# to remove TuxTalk's package (another install running, say), followed by
# its last lines. Everything else was still removed.
PACKAGE_NOT_REMOVED = "TUXTALK_PACKAGE_NOT_REMOVED"

# Printed by uninstall_lines(), followed by "helper" or "rule", for what is
# still there after removing it, so the uninstall doesn't claim it's gone.
NOT_REMOVED = "TUXTALK_NOT_REMOVED"

# _pkexec()'s message when the password prompt stayed open past
# PKEXEC_TIMEOUT: pkexec was stopped there, and nothing ran as root.
TIMED_OUT = "That took too long. Please try again."

# _pkexec()'s message, with ok=True, when the time ran out after the
# password: the script, running as root, couldn't be stopped and was waited
# for, but its output and exit status were lost.
RAN_TOO_LONG = "TUXTALK_RAN_TOO_LONG"


def _still_in_group_check():
    # No pipe: under pipefail, grep -q quitting early could fail the test.
    return (f'case " $(id -nG "$user") " in *" input "*) echo {STILL_IN_GROUP} ;; esac\n')


def leave_input_group_script():
    """The script pkexec runs as root to take the user who asked (pkexec's
    PKEXEC_UID, which they can't fake) out of the input group, with the old
    rule that gave that group /dev/uinput."""
    from .uinput_helper import UDEV_RULE_PATH
    return f"""set -uo pipefail
user=$(id -nu "${{PKEXEC_UID:?}}")
gpasswd -d "$user" input
{_still_in_group_check()}rm -f {UDEV_RULE_PATH}
udevadm control --reload-rules
udevadm trigger --action=change --sysname-match=uinput
exit 0
"""


def leave_input_group_with_pkexec():
    """(ok, message): out of the input group; takes effect after a restart."""
    return _run_as_root(leave_input_group_script(), "left the input group")


def can_install():
    """(True, "") when this system can run the helper, else (False, why).
    Without it, setup falls back to the "input" group."""
    if os.environ.get("FLATPAK_ID"):
        return False, "the Flatpak uses the desktop's portals instead"
    if not os.path.isdir("/run/systemd/system"):
        return False, "this system doesn't run systemd"
    if not os.path.isdir("/run/systemd/seats"):
        return False, "logind isn't tracking who's at the screen"
    try:
        ok = subprocess.run([PYTHON, "-I", "-S", "-c",
                             "import sys; sys.exit(sys.version_info < (3, 8))"],
                            timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        return False, f"{PYTHON} (3.8 or newer) isn't installed"
    if not any(os.access(os.path.join(d, "pkexec"), os.X_OK)
               for d in ("/usr/bin", "/bin", "/usr/local/bin")):
        return False, "pkexec isn't installed"
    return True, ""


def is_installed():
    return os.path.exists(UNIT_PATH) and os.path.exists(HELPER_PATH)


def running_version():
    """The running helper's version, from its hello, or None."""
    from .hotkey_helper_client import HelperKeyboard
    keyboard = HelperKeyboard(connect_timeout=0.5)
    try:
        if not os.path.exists(keyboard.socket_path) or not keyboard.connect():
            return None
        return keyboard.helper_version
    finally:
        keyboard.close()


def _version(text):
    """"1.4" -> (1, 4); anything unreadable counts as oldest."""
    try:
        return tuple(int(part) for part in text.split("."))
    except (AttributeError, ValueError):
        return ()


def is_older(running):
    """Whether the running helper's version *running* is older than the one
    this TuxTalk brings. Never for a newer one (an older TuxTalk run again):
    no password prompt to go back."""
    from .hotkey_helper import VERSION
    return running is not None and _version(running) < _version(VERSION)


# The dictation keys helpers before 1.5 don't report.
RIGHT_MODIFIER_KEYS = ("Right Ctrl", "Right Alt")


def hears_key(running, key):
    """Whether the running helper (version *running*, None when there is
    none) reports dictation key *key* ("F8", "Right Ctrl")."""
    return running is None or key not in RIGHT_MODIFIER_KEYS or _version(running) >= (1, 5)


def unheard_keys(keys):
    """Those of dictation *keys* the running hotkey helper is too old to
    report; the helper is asked its version only when one could be."""
    if not set(keys) & set(RIGHT_MODIFIER_KEYS):
        return []
    running = running_version()
    return [k for k in keys if not hears_key(running, k)]


def _run_as_root(script, what):
    """(ok, message) after running *script* through pkexec. (Tests replace
    this, so a test can never open a password prompt; _pkexec is the work.)"""
    return _pkexec(script, what)


def _pkexec(script, what):
    """(ok, message) after running *script* through pkexec."""
    from .uinput_helper import pkexec_was_declined
    started = time.monotonic()
    try:
        result = subprocess.run(["pkexec", "/bin/bash", "-s"], input=script, text=True,
                                capture_output=True, timeout=PKEXEC_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, TIMED_OUT
    except PermissionError:
        if time.monotonic() - started < PKEXEC_TIMEOUT:
            raise
        # Killing pkexec on the time-out fails once it runs the script as
        # root; subprocess then waits for the script to end.
        logger.warning(f"Hotkey helper {what}: ran past the time-out as root; "
                       "its result is unknown")
        return True, RAN_TOO_LONG
    except FileNotFoundError:
        return False, "pkexec isn't installed, so TuxTalk can't ask for your password."
    if result.returncode == 0:
        logger.info(f"Hotkey helper {what}")
        out = result.stdout or ""
        if STILL_IN_GROUP in out:
            logger.warning("Still in the input group after gpasswd")
        if PACKAGE_NOT_REMOVED in out:
            logger.warning(f"The package manager didn't remove TuxTalk: {out.strip()}")
        if NOT_REMOVED in out:
            logger.warning(f"Not removed as root: {out.strip()}")
        # The script's own report lines (the markers above), one per line.
        return True, "\n".join(line for line in out.splitlines() if line.startswith("TUXTALK_"))
    if pkexec_was_declined(result):
        logger.info(f"Hotkey helper {what}: cancelled at the password prompt")
        return False, "cancelled"
    detail = (result.stderr or result.stdout or "").strip()
    logger.error(f"Hotkey helper {what} failed: {detail}")
    return False, detail or "it failed for an unknown reason"


def fix_permissions():
    """The permission fix behind first-run's Fix Typing and Preferences' Fix
    Typing Permissions: the hotkey helper where this system can run it, the
    "input" group where it can't. Returns (ok, message, needs_restart)."""
    usable, why = can_install()
    if usable:
        ok, message = install_with_pkexec()
        return ok, message, False
    logger.info(f"Hotkey helper not used ({why}); using the input group instead")
    from .uinput_helper import install_udev_rule_with_pkexec
    ok, message = install_udev_rule_with_pkexec()
    return ok, message, ok


def fix_description():
    """What the fix will do, one line each, for the screens that offer it.
    The second item is whether a restart follows."""
    if can_install()[0]:
        return ([
            "Installs TuxTalk's hotkey helper, the only program that reads the keyboard. "
            "It tells TuxTalk about its hotkeys and Right Ctrl or Right Alt going down, "
            "never what you type",
            "Lets TuxTalk type with a virtual keyboard, as Steam does for game controllers",
        ], False)
    return ([
        "Adds a system rule to allow input device access",
        "Adds your user to the 'input' group",
    ], True)


def fallback_note():
    """One line for the screens offering the fix, when it will be the
    "input" group: why the helper can't be used here, and what the group
    means. None where the helper is used (or in the Flatpak, which uses
    neither)."""
    if os.environ.get("FLATPAK_ID"):
        return None
    usable, why = can_install()
    if usable:
        return None
    return (f"TuxTalk's hotkey helper can't run here ({why}), so TuxTalk uses the "
            "'input' group instead. That lets programs you run read the keyboard, "
            "which is how most Linux key tools work.")


def install_with_pkexec(leave_group=False):
    """Install (or update) the helper and the typing rule, and with
    *leave_group* leave the input group too. (ok, message)."""
    return _run_as_root(install_script(leave_group), "installed")
