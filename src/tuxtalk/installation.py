"""How and where this TuxTalk is installed: an AppImage (and whether it is
the copy in ~/AppImages), a .deb or .rpm, the AUR, a Flatpak, or a source
checkout. Updates, the menu launcher, the uninstaller and problem reports
each act on the answer."""

import functools
import logging
import os
from typing import Optional, Tuple


logger = logging.getLogger(__name__)

# Standard AppImage install location
APPIMAGE_DIR = os.path.expanduser("~/AppImages")
APPIMAGE_NAME = "TuxTalk.AppImage"
APPIMAGE_PATH = os.path.join(APPIMAGE_DIR, APPIMAGE_NAME)


def _own_appimage(appimage_env, appdir_env, module_path) -> str:
    """*appimage_env* (APPIMAGE) when it names the AppImage this code runs
    from, else "". APPIMAGE is inherited by everything an AppImage starts: a
    .deb TuxTalk opened from an AppImage terminal or editor sees that
    program's. It is ours only when this code runs from inside the AppImage,
    mounted or extracted."""
    in_appdir = bool(appdir_env) and module_path.startswith(
        os.path.realpath(appdir_env).rstrip("/") + "/")
    if "/.mount_" not in module_path and not in_appdir:
        return ""
    return appimage_env


def get_running_appimage_path() -> Optional[str]:
    """The AppImage this TuxTalk runs from (the AUR's, under /opt, too), or
    None when it isn't running from one."""
    return _own_appimage(os.environ.get("APPIMAGE", ""), os.environ.get("APPDIR", ""),
                         os.path.realpath(__file__)) or None


@functools.lru_cache(maxsize=None)
def _detect_system_package_manager() -> Optional[str]:
    """Return 'rpm', 'deb', or None by asking the system package DB about
    tuxtalk (or talktype, its name before 0.15.0), falling back to the distro
    family from /etc/os-release. Cached: it runs up to four package queries,
    and the answer can't change while TuxTalk runs."""
    import shutil
    for pkg in ("tuxtalk", "talktype"):
        for kind, query in (("rpm", ["rpm", "-q", pkg]), ("deb", ["dpkg-query", "-W", pkg])):
            if shutil.which(query[0]) and _package_query_succeeds(query):
                return kind
    try:
        with open("/etc/os-release") as f:
            osr = f.read().lower()
    except (OSError, UnicodeDecodeError) as e:
        logger.info(f"Install type: could not read /etc/os-release ({e})")
        return None
    if any(k in osr for k in ("fedora", "rhel", "centos", "suse", "rocky", "alma", "nobara")):
        return "rpm"
    if any(k in osr for k in ("debian", "ubuntu", "mint", "pop")):
        return "deb"
    return None


def _package_query_succeeds(query) -> bool:
    """Whether the package database query *query* found the package. One
    that hangs or fails to run says so in the log: the install type then
    falls back to the distro family, which may answer differently."""
    import subprocess
    try:
        return subprocess.run(query, capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(f"Install type: {' '.join(query)} didn't answer ({e})")
        return False


def get_install_type(*, appimage_env=None, flatpak_env=None, appdir_env=None,
                     module_path=None, pkg_query=None) -> str:
    """How TuxTalk is installed. One of:
      'flatpak'  — running inside a Flatpak sandbox
      'aur'      — pacman-managed AppImage at a system path (/opt, /usr)
      'appimage' — self-managed AppImage the app may swap in place
      'rpm'/'deb'/'package' — extracted package tree under /opt/talktype
      'dev'      — running from a source checkout
    All inputs are injectable for testing; by default they read the real env.
    """
    appimage_env = os.environ.get("APPIMAGE", "") if appimage_env is None else appimage_env
    flatpak_env = os.environ.get("FLATPAK_ID", "") if flatpak_env is None else flatpak_env
    appdir_env = os.environ.get("APPDIR", "") if appdir_env is None else appdir_env
    if module_path is None:
        module_path = os.path.realpath(__file__)
    if pkg_query is None:
        pkg_query = _detect_system_package_manager
    appimage_env = _own_appimage(appimage_env, appdir_env, module_path)

    if flatpak_env:
        return "flatpak"
    if appimage_env:
        # A real, running AppImage. A system path means it is package-managed
        # (the AUR package installs it under /opt); a home path means the
        # user manages it themselves and the app may swap it in place.
        if appimage_env.startswith("/opt/") or appimage_env.startswith("/usr/"):
            return "aur"
        return "appimage"
    if "/.mount_" in module_path:
        # Mounted AppImage without the APPIMAGE env — treat as self-managed.
        return "appimage"
    if module_path.startswith("/opt/"):
        # .deb/.rpm ship the extracted tree under /opt/talktype.
        return pkg_query() or "package"
    return "dev"


def running_appimage_location() -> Tuple[bool, Optional[str]]:
    """(whether the running AppImage is the copy in ~/AppImages, its path),
    or (False, None) when TuxTalk isn't running from an AppImage. It only
    looks; appimage_copy puts a copy there."""
    current_path = get_running_appimage_path()

    if not current_path:
        return (False, None)

    # Normalize paths for comparison
    current_normalized = os.path.realpath(current_path)
    standard_normalized = os.path.realpath(APPIMAGE_PATH)

    if current_normalized == standard_normalized:
        return (True, current_path)

    return (False, current_path)
