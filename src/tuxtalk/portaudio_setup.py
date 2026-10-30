"""PortAudio, the library sounddevice records the microphone through: whether
it's installed, and installing it with the system's package manager for the
welcome screen's step that offers it."""
import ctypes.util
import os
import shutil

from . import pkexec_runner
from .logger import setup_logger

logger = setup_logger(__name__)

# Where distributions put the library, for when ctypes can't find it.
_LIBRARY_PATHS = (
    '/usr/lib/libportaudio.so.2',
    '/usr/lib64/libportaudio.so.2',
    '/usr/lib/x86_64-linux-gnu/libportaudio.so.2',
    '/usr/lib/aarch64-linux-gnu/libportaudio.so.2',
)

# Each package manager and its command; the package's name differs by distro.
_INSTALL_COMMANDS = (
    ('dnf', ['dnf', 'install', '-y', 'portaudio']),           # Fedora, RHEL, CentOS
    ('apt', ['apt', 'install', '-y', 'libportaudio2']),       # Debian, Ubuntu
    ('pacman', ['pacman', '-S', '--noconfirm', 'portaudio']), # Arch, Manjaro
    ('zypper', ['zypper', 'install', '-y', 'portaudio']),     # openSUSE
    ('apk', ['apk', 'add', 'portaudio']),                     # Alpine
)


def portaudio_installed() -> bool:
    """Whether the PortAudio library is on this system."""
    found = ctypes.util.find_library('portaudio') or next(
        (path for path in _LIBRARY_PATHS if os.path.exists(path)), None)
    if found:
        logger.debug(f"PortAudio found: {found}")
        return True
    logger.warning("PortAudio library not found")
    return False


def get_portaudio_install_command():
    """(package manager, the command that installs PortAudio with it) for the
    first one on this system, or (None, None)."""
    for pm_name, install_cmd in _INSTALL_COMMANDS:
        if shutil.which(pm_name):
            logger.debug(f"PortAudio install command for {pm_name}: {install_cmd}")
            return (pm_name, install_cmd)
    logger.warning("No supported package manager detected for PortAudio")
    return (None, None)


def install_portaudio_with_pkexec():
    """Install PortAudio with the system's package manager, through pkexec.
    Returns (success, the message the welcome screen shows)."""
    if portaudio_installed():
        return (True, "PortAudio is already installed!")
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
        if portaudio_installed():
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
