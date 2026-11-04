"""Whether TuxTalk may type and read the hotkeys, and the older way of
letting it: the "input" group.

TuxTalk's virtual keyboard (virtual_keyboard.py) types through /dev/uinput,
and the hotkeys are read from /dev/input. Both need permissions a user
doesn't have by default. These checks say whether each works, and why not,
for the welcome screen, System check and the "couldn't type" notice.

The hotkey helper (secure_input.py) is the safer way to grant them. Where it
can't run, setup falls back to the input group, through pkexec, in two steps,
since the udev rule alone is not enough:
    1. a udev rule gives the 'input' group access to /dev/uinput;
    2. `usermod -a -G input` puts the user in that group.
The group change only takes effect in a new session, which is why setup
asks the user to restart the computer (logging out is often not enough).
"""

import os
import grp
import pwd
import shlex
from . import pkexec_runner
from .keyboard_devices import close_devices, is_keyboard_and_mouse, open_new_keyboards
from .logger import setup_logger

logger = setup_logger(__name__)

# The udev rule content that grants access to /dev/uinput.
# It reuses the EXISTING 'input' group rather than creating a new one. That is
# deliberate: /dev/input/event* is already owned by 'input', so a single
# `usermod -a -G input` grants both keystroke injection and hotkey reading.
UDEV_RULE_CONTENT = '''# TuxTalk: Allow users in the 'input' group to access uinput
# This lets TuxTalk's virtual keyboard type dictated text
KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
'''

UDEV_RULE_PATH = "/etc/udev/rules.d/99-talktype-uinput.rules"


def check_uinput_exists():
    """Whether /dev/uinput exists (the uinput module is loaded)."""
    exists = os.path.exists("/dev/uinput")
    logger.debug(f"/dev/uinput exists: {exists}")
    return exists


def check_uinput_readable():
    """Whether this user may read /dev/uinput."""
    readable = os.access("/dev/uinput", os.R_OK)
    logger.debug(f"/dev/uinput readable: {readable}")
    return readable


def check_uinput_writable():
    """Whether this user may write to /dev/uinput, which typing needs."""
    writable = os.access("/dev/uinput", os.W_OK)
    logger.debug(f"/dev/uinput writable: {writable}")
    return writable


def check_uinput_permission():
    """(has_access, reason): whether TuxTalk's virtual keyboard can type
    through /dev/uinput, and why not in words."""
    if not check_uinput_exists():
        return (False, "The /dev/uinput device does not exist. "
                       "The uinput kernel module may not be loaded.")
    if not check_uinput_readable():
        return (False, "Cannot read /dev/uinput. "
                       "You need permission to access this device for typing.")
    if not check_uinput_writable():
        return (False, "Cannot write to /dev/uinput. "
                       "You need write permission for keystroke injection.")
    return (True, "You have full access to /dev/uinput. Typing should work.")


def _readable_input_devices():
    """Input device nodes this user can actually open, asked evdev's way.

    evdev's list_devices() keeps only nodes passing os.access(R_OK | W_OK), so
    posing the question exactly as evdev poses it is the only answer that
    predicts whether hotkeys will work. Without evdev, or when a node goes
    while evdev lists them, the same question is asked of each node directly.

    Group membership is deliberately not used as a proxy here: a user can be in
    'input' while the nodes carry the wrong mode, and an ACL can grant access
    with no group at all. Ask about the capability, not about the mechanism.
    """
    try:
        from evdev import list_devices
        return list(list_devices())
    except ImportError:
        pass
    except OSError as e:    # it checks that a node exists, then stats it: an unplug between
        logger.info(f"A device went while listing the input devices: {e}")
    import glob
    return [p for p in glob.glob("/dev/input/event*")
            if os.access(p, os.R_OK | os.W_OK)]


def _readable_keyboards():
    """Readable input nodes that are keyboards, the ones the service listens
    to. A readable game controller alone does not let a hotkey through, nor
    a keyboard that's a mouse too that the user reaches only through a rule
    for that one device: a Logitech mouse is such a keyboard, and OpenRGB's
    udev rule (on Nobara and Bazzite) lets the user open it without the
    input group. Counted, it said typing works while F8 on the keyboard
    reached nothing, and Fix Typing wasn't offered. One reached through
    the input group counts, an MX Keys or kanata's: that group is how the
    system gives out keyboards."""
    paths = _readable_input_devices()
    try:
        from evdev import InputDevice
    except ImportError:
        return paths
    keyboards = open_new_keyboards([], lambda: paths, InputDevice)
    found = [dev.path for dev in keyboards
             if not is_keyboard_and_mouse(dev) or _through_input_group(dev.path)]
    close_devices(keyboards)
    return found


def _through_input_group(path) -> bool:
    """Whether this session reaches the node at *path* through the input
    group: it's in the group, and the group's own entry for the node (the
    owning group's bits, or an ACL entry naming it) lets it read and
    write, as helper_may judges it. What everyone else may do doesn't
    count, nor does root's way in."""
    from .helper_keyboard_access import ACL_OTHER, READ, WRITE, UnknownAccess, acl_of, helper_may
    if os.geteuid() == 0:
        return True
    try:
        gid = grp.getgrnam("input").gr_gid
        st = os.stat(path)
        acl = acl_of(path)
    except (KeyError, OSError, UnknownAccess):
        return False
    if gid not in os.getgroups() and gid != os.getegid():
        return False
    group_only = None if acl is None else [e for e in acl if e[0] != ACL_OTHER]
    return helper_may(st.st_mode & ~0o007, st.st_gid, group_only, gid, READ | WRITE)


SYSTEM_CHECK_PATH = "Preferences → Advanced → System check"
# System check's button (prefs_system_check), named by every notice that sends people there.
FIX_TYPING_LABEL = "Fix Typing Permissions"
FIX_TYPING_PATH = f"{SYSTEM_CHECK_PATH} → {FIX_TYPING_LABEL}"
REPORT_A_PROBLEM_PATH = "Help → Problems → Report a Problem"

# Said by System check, the hotkey test, first run and the notice at start
# when the hotkey helper answers but reads no keyboard (issue #26). Its
# journal names each device it left out and why, and Report a Problem
# includes that.
HELPER_SEES_NO_KEYBOARD = ("The hotkey helper can't see your keyboard, so the dictation keys "
                           f"can't work. {REPORT_A_PROBLEM_PATH} has its log")
# Said by System check and the notice at start. The service keeps trying an
# installed helper (connect_helper) rather than reading the keyboards
# itself, and System check's Update starts it again.
HELPER_NOT_ANSWERING = ("TuxTalk's hotkey helper is installed but isn't answering, so the "
                        "dictation key won't work. Updating it starts it again")


def helper_expected(socket_file):
    """Whether the hotkeys come from the hotkey helper, answering or not: its
    socket (*socket_file*) is there, or it's installed and the user can't
    read a keyboard themselves. The service keeps trying an installed helper
    (connect_helper) rather than reading the keyboards itself, so a silent
    one means no hotkeys until it answers again. Someone still in the input
    group reads them as before while the socket isn't there."""
    from . import secure_input
    return os.path.exists(socket_file) or (secure_input.is_installed()
                                           and not _readable_keyboards())


# check_input_read_permission's default: ask the hotkey helper itself.
_ASK = object()


def check_input_read_permission(hello=_ASK):
    """(has_access, reason): whether the dictation service will hear the
    hotkeys, through the hotkey helper or from /dev/input itself.
    *hello* is secure_input.helper_hello()'s answer for a caller that asked
    already (None: nothing answered); each ask is a connection that waits
    for the helper, so a screen checking several things asks once."""
    from . import secure_input
    from .hotkey_helper_client import socket_path
    if hello is _ASK:
        hello = secure_input.helper_hello()
    if hello is not None:
        if hello[1] == 0:           # None: before 1.12 the helper doesn't say
            return (False, f"{HELPER_SEES_NO_KEYBOARD}.")
        return (True, "TuxTalk's hotkey helper reads the hotkeys. Hotkeys should work.")
    if helper_expected(socket_path()):
        return (False, f"{HELPER_NOT_ANSWERING}.")
    if _readable_keyboards():
        return (True, "You can read input devices. Hotkeys should work.")
    return (False, "Cannot read any keyboard under /dev/input, so hotkeys "
                   f"cannot work. {FIX_TYPING_LABEL} sets that up.")


def check_all_device_permissions(hello=_ASK):
    """(has_access, reason) for both permissions TuxTalk needs: typing
    through /dev/uinput, and the hotkeys (the helper, or /dev/input).
    *hello*: the same as for check_input_read_permission.

    They are granted by different mechanisms (logind can hand out an ACL on
    /dev/uinput while /dev/input needs the helper or the 'input' group), so
    on some distributions (Fedora) one works while the other does not.
    Checking uinput alone would skip setup there: typing would work, but no
    hotkey could ever be read.
    """
    ok_uinput, uinput_reason = check_uinput_permission()
    ok_input, input_reason = check_input_read_permission(hello)

    if ok_uinput and ok_input:
        return (True, "Typing and hotkeys both have the access they need.")
    if not ok_uinput and not ok_input:
        return (False, f"{uinput_reason} Also: {input_reason}")
    return (False, uinput_reason if not ok_uinput else input_reason)


def check_user_in_input_group():
    """Whether this user is a member of the 'input' group in the group
    database (not yet in this session's groups, maybe: see
    input_group_pending_restart). The user is the passwd entry the fix
    script adds, so System check answers for the same one; a login name
    from the terminal (os.getlogin) is another user's under su."""
    username = _username()
    if not username:
        return False
    try:
        in_group = username in grp.getgrnam('input').gr_mem
    except KeyError:        # no 'input' group on this system
        return False
    logger.debug(f"User '{username}' in 'input' group: {in_group}")
    return in_group


def check_udev_rule_exists():
    """Whether the input group's udev rule for /dev/uinput is installed."""
    exists = os.path.exists(UDEV_RULE_PATH)
    logger.debug(f"Udev rule exists at {UDEV_RULE_PATH}: {exists}")
    return exists


# Why a dictation couldn't be typed, for the "didn't reach the window" notice.
# A restart is only owed where the fix is the input group: the hotkey
# helper's typing rule applies at once.
TYPING_RESTART_PENDING = ("TuxTalk can't type yet: restart your computer to finish "
                          "the typing setup.")
TYPING_NO_PERMISSION = f"TuxTalk doesn't have permission to type. Open {FIX_TYPING_PATH}."
TYPING_NO_PERMISSION_RESTART = (f"TuxTalk doesn't have permission to type. Open "
                                f"{FIX_TYPING_PATH}, then restart your computer.")


def _username():
    """This user's login name, from the passwd database."""
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:    # no passwd entry for this uid (a container, say)
        return os.environ.get("USER", "")


def input_group_pending_restart():
    """True when setup has added this user to 'input' but the login session
    predates it. os.getgroups() is fixed at login; the group's member list
    changes the moment usermod runs."""
    try:
        group = grp.getgrnam("input")
    except KeyError:
        return False
    return _username() in group.gr_mem and group.gr_gid not in os.getgroups()


def typing_blocked_reason():
    """Why TuxTalk can't type right now, as advice for the user, or None
    (also when there's nothing to check: the Flatpak, or no /dev/uinput)."""
    if os.environ.get("FLATPAK_ID") or not os.path.exists("/dev/uinput"):
        return None
    if not check_uinput_writable():
        if input_group_pending_restart():
            return TYPING_RESTART_PENDING
        from .secure_input import can_install
        return TYPING_NO_PERMISSION if can_install()[0] else TYPING_NO_PERMISSION_RESTART
    return None


def input_access_advice() -> str:
    """What to do when a keyboard is plugged in and none is readable, for
    the user: the service's log, the terminal it was started from and the
    notice at start. evdev's list_devices() skips the nodes this user can't
    open, rather than raising, so without this the service looks healthy
    while ignoring every key."""
    from .secure_input import can_install
    if can_install()[0]:
        return (f"Open {FIX_TYPING_PATH}: it installs TuxTalk's hotkey helper, "
                "with no restart.")
    try:
        group = grp.getgrnam("input")
    except KeyError:
        return ("Fix Typing can't help on this system, which has no 'input' group. "
                f"{REPORT_A_PROBLEM_PATH} has what's needed to find out why.")
    if group.gr_gid in os.getgroups():
        return ("You're in the 'input' group, yet the keyboard can't be read, so Fix "
                f"Typing can't help. {REPORT_A_PROBLEM_PATH} has what's needed to find "
                "out why.")
    if input_group_pending_restart():
        # A new login isn't always enough: a lingering user session keeps
        # the old group list.
        return ("Setup already put you in the 'input' group: restart the computer "
                "to finish it.")
    return f"Open {FIX_TYPING_PATH}, then restart the computer."


def get_fix_script_content():
    """The script pkexec runs as root for the input group's fix: the udev
    rule for /dev/uinput, and this user in the 'input' group."""
    # This script runs as ROOT via pkexec. The name comes from the passwd
    # database (or $USER when this uid has no entry), so shlex.quote is what
    # keeps any name from turning into shell commands.
    quoted_user = shlex.quote(_username())

    return f'''#!/bin/bash
# TuxTalk uinput permission fix script
# This script is run with administrator privileges via pkexec

set -e

# Username resolved and shell-quoted by TuxTalk; used only via "$TUXTALK_USER"
TUXTALK_USER={quoted_user}

echo "Setting up uinput access for TuxTalk..."

# Create the udev rule
cat > {UDEV_RULE_PATH} << 'RULE'
{UDEV_RULE_CONTENT}RULE

echo "Created udev rule at {UDEV_RULE_PATH}"

# Add user to the input group. Members are skipped: usermod fails for users
# outside /etc/passwd (LDAP, SSSD, systemd-homed) even when nothing would change.
# The group list is split one name per line so "input-foo" never counts.
id -nG "$TUXTALK_USER" | tr ' ' '\\n' | grep -qx input || usermod -a -G input "$TUXTALK_USER"
echo "$TUXTALK_USER is in the input group"

# Reload udev rules and apply them to /dev/uinput only, not every device
udevadm control --reload-rules
udevadm trigger --sysname-match=uinput

echo ""
echo "Success! Uinput permissions configured."
echo ""
echo "IMPORTANT: Restart the computer to apply the group changes."
echo "(Logging out and back in is often not enough — a lingering user"
echo " session keeps the old group list alive.)"
'''


def install_udev_rule_with_pkexec():
    """Set up the "input" group and its udev rule through pkexec, for
    systems where the hotkey helper can't run. Returns (success, message).

    The script goes to root's bash on stdin, as secure_input does. Written to
    a file first, any program running as this user could have rewritten it
    while the password prompt was open, and it would then have run as root."""
    logger.info("Running the input group setup with pkexec")
    outcome = pkexec_runner.run(["/bin/bash", "-s"], pkexec_runner.SCRIPT_TIMEOUT_S,
                                script=get_fix_script_content())
    if outcome.status == pkexec_runner.DONE:
        logger.info("Udev rule installed successfully")
        return (True, "Permissions configured successfully!\n\n"
                      "Restart your computer to apply (logging out is often not enough).")
    if outcome.status == pkexec_runner.DECLINED:
        logger.info("User cancelled pkexec authentication")
        return (False, "Authentication was cancelled.")
    if outcome.status == pkexec_runner.TIMED_OUT:
        logger.error("pkexec timed out")
        return (False, "The operation timed out. Please try again.")
    if outcome.status == pkexec_runner.UNKNOWN:
        logger.warning("The input group setup ran past the time-out as root; "
                       "its result is unknown")
        return (False, "That took too long to tell whether it worked. Restart your "
                       "computer; if typing still doesn't work, try again.")
    if outcome.status == pkexec_runner.MISSING:
        logger.error("pkexec not found")
        return (False, "pkexec not found. Please install polkit to use this feature.")
    error_msg = outcome.detail or "Unknown error"
    logger.error(f"Failed to install udev rule: {error_msg}")
    return (False, f"Failed to configure permissions:\n{error_msg}")
