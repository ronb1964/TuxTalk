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
from .keyboard_devices import is_keyboard_device
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
    """
    Check if /dev/uinput device exists.
    
    Returns:
        bool: True if /dev/uinput exists, False otherwise
    """
    exists = os.path.exists("/dev/uinput")
    logger.debug(f"/dev/uinput exists: {exists}")
    return exists


def check_uinput_readable():
    """
    Check if current user can read /dev/uinput.
    
    Returns:
        bool: True if readable, False otherwise
    """
    try:
        readable = os.access("/dev/uinput", os.R_OK)
        logger.debug(f"/dev/uinput readable: {readable}")
        return readable
    except Exception as e:
        logger.warning(f"Error checking /dev/uinput read access: {e}")
        return False


def check_uinput_writable():
    """
    Check if current user can write to /dev/uinput.
    
    Returns:
        bool: True if writable, False otherwise
    """
    try:
        writable = os.access("/dev/uinput", os.W_OK)
        logger.debug(f"/dev/uinput writable: {writable}")
        return writable
    except Exception as e:
        logger.warning(f"Error checking /dev/uinput write access: {e}")
        return False


def check_uinput_permission():
    """
    Check if current user has full access to /dev/uinput.
    
    Returns:
        tuple: (has_access: bool, reason: str)
            - has_access: True if user can use uinput for keystroke injection
            - reason: Human-readable explanation of the status
    """
    # Check if device exists
    if not check_uinput_exists():
        return (False, "The /dev/uinput device does not exist. "
                       "The uinput kernel module may not be loaded.")
    
    # Check read access
    if not check_uinput_readable():
        return (False, "Cannot read /dev/uinput. "
                       "You need permission to access this device for typing.")
    
    # Check write access
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
    to. A readable game controller alone does not let a hotkey through."""
    paths = _readable_input_devices()
    try:
        from evdev import InputDevice
    except ImportError:
        return paths
    keyboards = []
    for path in paths:
        try:
            dev = InputDevice(path)
        except OSError:     # unplugged since it was listed, or not ours to open
            continue
        try:
            if is_keyboard_device(dev):
                keyboards.append(path)
        finally:
            dev.close()
    return keyboards


def check_input_read_permission():
    """
    Check whether TuxTalk can read hotkeys from /dev/input.

    Returns:
        tuple: (has_access: bool, reason: str)
    """
    from .hotkey_helper_client import connect_helper
    helper = connect_helper()
    if helper is not None:
        answering = helper.sock is not None
        helper.close()
        if answering:
            return (True, "TuxTalk's hotkey helper reads the hotkeys. Hotkeys should work.")
        # The service waits for an installed helper rather than reading
        # the keyboards itself, so a silent one means no hotkeys.
        return (False, "TuxTalk's hotkey helper is installed but isn't answering, so "
                       "the hotkeys won't work. Fix Typing sets it up again.")
    if _readable_keyboards():
        return (True, "You can read input devices. Hotkeys should work.")
    return (False, "Cannot read any keyboard under /dev/input, so hotkeys "
                   "cannot work. This usually means you are not in the "
                   "'input' group.")


def check_all_device_permissions():
    """
    Check BOTH device permissions TuxTalk needs, not just one.

    Typing keystrokes needs /dev/uinput; reading hotkeys needs /dev/input.
    They are granted by different mechanisms — logind hands out a per-user ACL
    on /dev/uinput, while /dev/input relies on 'input' group membership — so on
    some distributions (Fedora) one works while the other does not.

    Checking uinput alone would skip setup on Fedora, and with it the
    `usermod -a -G input` that grants hotkey access: typing would work, but no
    hotkey could ever be read.

    Returns:
        tuple: (has_access: bool, reason: str)
    """
    ok_uinput, uinput_reason = check_uinput_permission()
    ok_input, input_reason = check_input_read_permission()

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
    """
    Check if our udev rule is already installed.
    
    Returns:
        bool: True if the rule file exists, False otherwise
    """
    exists = os.path.exists(UDEV_RULE_PATH)
    logger.debug(f"Udev rule exists at {UDEV_RULE_PATH}: {exists}")
    return exists


# Why a dictation couldn't be typed, for the "didn't reach the window" notice.
TYPING_RESTART_PENDING = ("TuxTalk can't type yet: restart your computer to finish "
                          "the typing setup.")
TYPING_NO_PERMISSION = ("TuxTalk doesn't have permission to type. Open Preferences, "
                        "Advanced, Fix Typing Permissions, then restart your computer.")


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
        return TYPING_NO_PERMISSION
    return None


def get_fix_script_content():
    """
    Get the shell script content that fixes uinput permissions.
    This script is run with pkexec (admin privileges).
    
    Returns:
        str: Shell script content
    """
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
