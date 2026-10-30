#!/usr/bin/env python3
"""
uinput Helper Module for TuxTalk

Handles detection and fixing of the /dev/uinput permission TuxTalk's virtual
keyboard (virtual_keyboard.py) needs to type, the older way: through the
"input" group. secure_input.py sets up the safer way.

The problem:
- TuxTalk types through the Linux uinput subsystem
- /dev/uinput requires special permissions (usually root, or a group such
  as 'input')
- Without access, typing injection silently fails

The solution:
- Detect if user can access /dev/uinput
- If not, offer an opt-in setup step (via pkexec) that does TWO separate
  things -- the udev rule alone is not enough:
    1. installs a udev rule giving the 'input' group access to /dev/uinput
    2. runs `usermod -a -G input` to actually put the user in that group
- The group change only takes effect in a new session, which is why setup
  asks the user to restart the computer (logging out is often not enough)
"""

import os
import signal
import subprocess
import grp
import pwd
import shlex
from . import pkexec_runner
from .logger import setup_logger
from .virtual_keyboard import NAME as VIRTUAL_KEYBOARD_NAME

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

try:
    from evdev import ecodes as _ecodes
except ImportError:     # no evdev: nothing can read /dev/input anyway
    _ecodes = None

# Letter keys, used to tell a real keyboard from a device that merely reports
# key events (power buttons, lid switches, mouse buttons, game controllers,
# consumer-control nodes). Built by name, not as a numeric range: evdev codes
# follow the physical scancode order of a PC keyboard, so KEY_A..KEY_Z spans
# the home row and several punctuation keys while leaving KEY_M outside it.
LETTER_KEYS = frozenset(
    getattr(_ecodes, f"KEY_{letter}") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
) if _ecodes else frozenset()


def is_keyboard_device(dev) -> bool:
    """True if *dev* can deliver the hotkey and is therefore worth reading.

    Reading everything under /dev/input would also take in mice, touchpads,
    audio jacks and the power button. And logind lets any user open a game
    controller, which reports key events too, so "some device with keys
    opened" does not mean the keyboard did.

    Two capability checks, in this order:

    1. It must report letter keys. Power buttons, lid switches, audio jacks,
       game controllers and consumer-control nodes report none, so this drops
       them immediately.
    2. A real device must not be a pointing device. Logitech Unifying
       receivers advertise a superset HID descriptor, so a wireless mouse
       claims all 26 letter keys and all 12 F-keys just like a keyboard does.
       What separates them is real pointer motion (REL_X) combined with mouse
       buttons, the same test libinput uses. A uinput keyboard of that shape
       is read all the same (is_remapper_keyboard): kanata takes the real
       keyboards for itself and passes every key on through one, so skipping
       it left TuxTalk hearing no key at all (issue #23).

    TuxTalk's own virtual keyboard, which it types with, is never read: it is
    told by its name.

    Note the check is REL_X specifically, not the EV_REL event type: the K800
    keyboard declares EV_REL while exposing no motion axis, so testing the type
    alone would throw out a real keyboard and let the hotkey leak through.
    """
    caps = _keyboard_capabilities(dev)
    if caps is None:
        return False
    return not _is_pointer(caps) or is_uinput_device(getattr(dev, "path", ""))


def is_remapper_keyboard(dev) -> bool:
    """Whether *dev* is a keyboard read only because a program made it,
    shaped like a mouse too: a key remapper's (kanata's), which carries
    the real keyboards' keys and the mouse's motion, and the text TuxTalk
    types when the remapper has taken its virtual keyboard as well. Never
    grabbed or written to: held, it would freeze the mouse and swallow the
    dictation being typed."""
    caps = _keyboard_capabilities(dev)
    return (caps is not None and _is_pointer(caps)
            and is_uinput_device(getattr(dev, "path", "")))


def _keyboard_capabilities(dev):
    """*dev*'s capabilities when it reports letter keys and isn't TuxTalk's
    own keyboard, else None."""
    if _ecodes is None or getattr(dev, "name", None) == VIRTUAL_KEYBOARD_NAME:
        return None
    try:
        caps = dev.capabilities()
    except Exception as e:      # unplugged, or a node evdev can't describe
        logger.info(f"Could not read what {getattr(dev, 'name', dev)} reports: {e}")
        return None
    keys = set(caps.get(_ecodes.EV_KEY) or [])
    return caps if len(keys & LETTER_KEYS) >= 3 else None


def _is_pointer(caps) -> bool:
    return (_ecodes.REL_X in (caps.get(_ecodes.EV_REL) or [])
            and _ecodes.BTN_LEFT in (caps.get(_ecodes.EV_KEY) or []))


def is_uinput_device(path) -> bool:
    """Whether the input device at *path* (/dev/input/eventN) is one a program
    made through uinput: its sysfs node is under /sys/devices/virtual/input.
    A Bluetooth LE keyboard or mouse (uhid) is elsewhere under
    /sys/devices/virtual and counts as hardware. False when it can't be told."""
    node = os.path.realpath(f"/sys/class/input/{os.path.basename(path)}/device")
    return node.startswith("/sys/devices/virtual/input/")


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


def reboot_system() -> bool:
    """Reboot the machine via systemd-logind.

    `systemctl reboot` asks logind, which lets an active local desktop session
    reboot without a password. Returns False rather than raising if the command
    is missing or refused, so the caller can fall back to telling the user to
    restart manually.
    """
    try:
        result = subprocess.run(
            ['systemctl', 'reboot'],
            capture_output=True, text=True, errors="replace", timeout=10,
        )
        if result.returncode != 0 and result.stderr:
            logger.warning(f"systemctl reboot refused: {result.stderr.strip()}")
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Could not reboot: {e}")
        return False


def check_user_in_input_group():
    """
    Check if current user is in the 'input' group.
    
    Returns:
        bool: True if user is in 'input' group, False otherwise
    """
    try:
        username = os.getlogin()
    except OSError:
        # Fallback for some environments
        username = os.environ.get('USER', '')
    
    if not username:
        return False
    
    try:
        input_group = grp.getgrnam('input')
        in_group = username in input_group.gr_mem
        logger.debug(f"User '{username}' in 'input' group: {in_group}")
        return in_group
    except KeyError:
        # 'input' group doesn't exist
        logger.debug("'input' group does not exist")
        return False
    except Exception as e:
        logger.warning(f"Error checking input group membership: {e}")
        return False


def check_udev_rule_exists():
    """
    Check if our udev rule is already installed.
    
    Returns:
        bool: True if the rule file exists, False otherwise
    """
    exists = os.path.exists(UDEV_RULE_PATH)
    logger.debug(f"Udev rule exists at {UDEV_RULE_PATH}: {exists}")
    return exists


# --- ydotoold, which TuxTalk typed through until 0.18 ------------------------
#
# 0.18 types on its own virtual keyboard. Until then the tray started ydotoold
# itself, and Fix Typing could set up a systemd user unit that starts it at
# every login. retire_ydotoold() takes both away, so nothing runs ydotoold for
# TuxTalk any more; the uninstaller looks for the unit too.

YDOTOOLD_UNIT = os.path.join(".config", "systemd", "user", "ydotoold.service")


def our_ydotoold_unit(home):
    """The ydotoold systemd user unit under *home* if TuxTalk wrote it, else
    None. The name is generic, so its description has to say it's ours."""
    path = os.path.join(home, YDOTOOLD_UNIT)
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None
    if "(TuxTalk keystroke injection)" in text or "(TalkType keystroke injection)" in text:
        return path
    return None


def retire_ydotoold():
    """Stop the ydotoold an older TuxTalk started and remove its systemd user
    unit. Never raises: a leftover daemon only wastes a little memory."""
    stop_ydotoold_we_started()
    unit = our_ydotoold_unit(os.path.expanduser("~"))
    if unit is None:
        return
    try:
        result = subprocess.run(["systemctl", "--user", "disable", "--now", "ydotoold.service"],
                                capture_output=True, text=True, errors="replace", timeout=15)
        if result.returncode != 0:
            # Kept, so the next start tries again: removed while systemd still
            # runs it, the daemon would go on until logout.
            logger.warning(f"Could not stop the old ydotoold service: {result.stderr.strip()}")
            return
        os.remove(unit)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=15)
        logger.info("Removed the ydotoold service TuxTalk used to type with")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Could not remove the old ydotoold service: {e}")


def _our_ydotoold_pidfile():
    """Where an older TuxTalk noted the ydotoold it started itself."""
    from .single_instance import session_runtime_dir
    return os.path.join(session_runtime_dir(), "tuxtalk-ydotoold.pid")


def stop_ydotoold_we_started():
    """Stop the ydotoold an older TuxTalk started. Never raises.

    Only that one: a ydotoold run by another program may still be needed.
    The noted process must still be a ydotoold, as the number may have been
    handed to another program since."""
    path = _our_ydotoold_pidfile()
    try:
        with open(path) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return
    try:
        with open(f"/proc/{pid}/comm") as f:
            if f.read().strip() == "ydotoold":
                os.kill(pid, signal.SIGTERM)
                logger.info("Stopped the ydotoold an older TuxTalk started")
    except (OSError, ValueError) as e:
        logger.debug(f"The ydotoold TuxTalk started is already gone: {e}")
    try:
        os.remove(path)
    except OSError:
        pass


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


def _input_group_pending_restart():
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
        if _input_group_pending_restart():
            return TYPING_RESTART_PENDING
        return TYPING_NO_PERMISSION
    return None


# =============================================================================
# PortAudio Detection and Installation
# =============================================================================

def check_portaudio_installed():
    """
    Check if PortAudio library is installed on the system.
    PortAudio is required by sounddevice for audio recording.

    Returns:
        tuple: (installed: bool, message: str)
    """
    import ctypes.util

    # Try to find libportaudio
    portaudio_lib = ctypes.util.find_library('portaudio')

    if portaudio_lib:
        logger.debug(f"PortAudio found: {portaudio_lib}")
        return (True, f"PortAudio library found: {portaudio_lib}")

    # Fallback: Try common library paths directly
    common_paths = [
        '/usr/lib/libportaudio.so.2',
        '/usr/lib64/libportaudio.so.2',
        '/usr/lib/x86_64-linux-gnu/libportaudio.so.2',
        '/usr/lib/aarch64-linux-gnu/libportaudio.so.2',
    ]

    for path in common_paths:
        if os.path.exists(path):
            logger.debug(f"PortAudio found at: {path}")
            return (True, f"PortAudio library found: {path}")

    logger.warning("PortAudio library not found")
    return (False, "PortAudio library not found. Audio recording requires libportaudio.")


def get_portaudio_install_command():
    """
    Get the package manager command to install PortAudio.

    Returns:
        tuple: (package_manager: str, install_command: list) or (None, None) if not detected
    """
    import shutil

    # Package names differ by distro
    # Ubuntu/Debian: libportaudio2
    # Fedora/RHEL: portaudio
    # Arch: portaudio
    # openSUSE: portaudio
    package_managers = [
        ('dnf', ['dnf', 'install', '-y', 'portaudio']),           # Fedora, RHEL, CentOS
        ('apt', ['apt', 'install', '-y', 'libportaudio2']),       # Debian, Ubuntu
        ('pacman', ['pacman', '-S', '--noconfirm', 'portaudio']), # Arch, Manjaro
        ('zypper', ['zypper', 'install', '-y', 'portaudio']),     # openSUSE
        ('apk', ['apk', 'add', 'portaudio']),                     # Alpine
    ]

    for pm_name, install_cmd in package_managers:
        if shutil.which(pm_name):
            logger.debug(f"PortAudio install command for {pm_name}: {install_cmd}")
            return (pm_name, install_cmd)

    logger.warning("No supported package manager detected for PortAudio")
    return (None, None)


def install_portaudio_with_pkexec():
    """
    Install PortAudio using the system package manager with pkexec for privilege escalation.

    Returns:
        tuple: (success: bool, message: str)
    """
    # Check if already installed
    installed, _ = check_portaudio_installed()
    if installed:
        return (True, "PortAudio is already installed!")

    # Detect package manager and get install command
    pm_name, install_cmd = get_portaudio_install_command()

    if not pm_name:
        return (False, "Could not detect your package manager.\n"
                      "Please install PortAudio manually:\n"
                      "  Ubuntu/Debian: sudo apt install libportaudio2\n"
                      "  Fedora: sudo dnf install portaudio\n"
                      "  Arch: sudo pacman -S portaudio")

    logger.info(f"Installing PortAudio using {pm_name}: pkexec {' '.join(install_cmd)}")
    try:
        outcome = pkexec_runner.run(install_cmd, pkexec_runner.INSTALL_TIMEOUT_S)
    except OSError as e:
        logger.error(f"Error installing PortAudio: {e}")
        return (False, f"Error: {e}")
    if outcome.status in (pkexec_runner.DONE, pkexec_runner.UNKNOWN):
        # Past the time limit the install ran on as root: whether it's there
        # now says how it went.
        if check_portaudio_installed()[0]:
            logger.info("PortAudio installed successfully")
            return (True, "PortAudio installed successfully!")
        return (False, "Installation appeared to succeed but PortAudio not found. "
                       "You may need to restart TuxTalk.")
    if outcome.status == pkexec_runner.DECLINED:
        logger.info("User cancelled pkexec authentication for PortAudio install")
        return (False, "Installation was cancelled.")
    if outcome.status == pkexec_runner.TIMED_OUT:
        logger.error("PortAudio installation timed out")
        return (False, "Installation timed out. Please try again or install manually.")
    if outcome.status == pkexec_runner.MISSING:
        logger.error("pkexec not found")
        return (False, "pkexec not found. Please install polkit or install PortAudio manually.")
    error_msg = outcome.detail or "Unknown error"
    logger.error(f"Failed to install PortAudio: {error_msg}")
    return (False, f"Installation failed:\n{error_msg}")


def get_portaudio_status():
    """
    Get comprehensive status of PortAudio installation.

    Returns:
        dict: Status information with keys:
            - installed: bool
            - needs_install: bool
            - message: str
    """
    installed, _ = check_portaudio_installed()

    if installed:
        return {
            'installed': True,
            'needs_install': False,
            'message': "PortAudio is installed - audio recording ready"
        }

    return {
        'installed': False,
        'needs_install': True,
        'message': "PortAudio not installed - required for audio recording"
    }


def get_fix_script_content():
    """
    Get the shell script content that fixes uinput permissions.
    This script is run with pkexec (admin privileges).
    
    Returns:
        str: Shell script content
    """
    # Resolve the username from the passwd database (canonical — can't be
    # spoofed via the USER env var) and shell-quote it. This script runs as
    # ROOT via pkexec, so an unquoted/attacker-controlled username would be a
    # root shell-injection vector.
    try:
        username = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:    # no passwd entry for this uid (a container, say)
        username = os.environ.get('USER', '')
    quoted_user = shlex.quote(username)

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
