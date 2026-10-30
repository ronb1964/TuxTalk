"""PortAudio, the library sounddevice records the microphone through: whether
it's installed, and installing it with the system's package manager for the
welcome screen's step that offers it."""
import os

from . import pkexec_runner
from .logger import setup_logger

logger = setup_logger(__name__)


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
