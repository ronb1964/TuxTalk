#!/usr/bin/env python3
"""
Update checker for TuxTalk

Checks GitHub for new TuxTalk releases and GNOME extension versions, and
downloads and installs updates for each install type.
"""

import functools
import json
import logging
import os
import re
import subprocess
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Callable, Tuple

from .autostart import desktop_exec_program

logger = logging.getLogger(__name__)

# GitHub API endpoint for latest release
GITHUB_API_URL = "https://api.github.com/repos/ronb1964/TuxTalk/releases/latest"

# GitHub raw content URL for fetching extension metadata from a specific tag
GITHUB_RAW_URL = "https://raw.githubusercontent.com/ronb1964/TuxTalk"

# Extension UUID and path
EXTENSION_UUID = "talktype@ronb1964.github.io"

# Download directory for updates
UPDATE_DIR = os.path.expanduser("~/.local/share/TuxTalk/updates")

# Anything in UPDATE_DIR this old is left over from an earlier update. An hour
# is far longer than any installer takes to open its package.
STALE_UPDATE_S = 60 * 60

# Standard AppImage install location
APPIMAGE_DIR = os.path.expanduser("~/AppImages")
APPIMAGE_NAME = "TuxTalk.AppImage"
APPIMAGE_PATH = os.path.join(APPIMAGE_DIR, APPIMAGE_NAME)

# Desktop launcher location
DESKTOP_FILE_PATH = os.path.expanduser("~/.local/share/applications/tuxtalk.desktop")


def get_current_version() -> str:
    """
    Get current TuxTalk version.

    Returns:
        str: Version string like "0.4.0"
    """
    from . import __version__
    return __version__


def get_extension_version() -> Optional[int]:
    """
    Get installed GNOME extension version.

    Reads version from the installed extension's metadata.json file.

    Returns:
        int: Version number (e.g., 2), or None if not installed
    """
    ext_path = Path.home() / ".local/share/gnome-shell/extensions" / EXTENSION_UUID / "metadata.json"

    if not ext_path.exists():
        return None

    try:
        with open(ext_path, "r") as f:
            metadata = json.load(f)
            return metadata.get("version")
    except Exception as e:
        logger.warning(f"Could not read extension version: {e}")
        return None


def compare_versions(current: str, latest: str) -> bool:
    """
    Compare semantic versions.

    Args:
        current: Current version string (e.g., "0.4.0")
        latest: Latest version string (e.g., "0.5.0")

    Returns:
        bool: True if latest > current
    """
    def parse_version(v: str) -> Tuple[int, ...]:
        """Parse version string to tuple of integers."""
        # Remove 'v' prefix if present
        v = v.lstrip("v")
        # Split on dots and convert to integers
        parts = []
        for part in v.split("."):
            # Handle parts like "0-beta" by taking just the number
            num_match = re.match(r"(\d+)", part)
            if num_match:
                parts.append(int(num_match.group(1)))
            else:
                parts.append(0)
        return tuple(parts)

    try:
        current_tuple = parse_version(current)
        latest_tuple = parse_version(latest)
        return latest_tuple > current_tuple
    except Exception:
        return False


def fetch_extension_version_from_release(tag: str) -> Optional[int]:
    """
    Fetch extension version from GitHub for a specific release tag.

    This allows us to compare the installed extension version with the
    version in the release, to determine if extension reinstall is needed.

    Args:
        tag: Release tag (e.g., "v0.5.0")

    Returns:
        int: Extension version number, or None if fetch failed
    """
    # URL to the extension metadata.json in the repo at the given tag
    metadata_url = f"{GITHUB_RAW_URL}/{tag}/gnome-extension/{EXTENSION_UUID}/metadata.json"

    try:
        request = urllib.request.Request(
            metadata_url,
            headers={"User-Agent": "TuxTalk-UpdateChecker"}
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("version")

    except urllib.error.HTTPError as e:
        logger.debug(f"Could not fetch extension metadata for {tag}: {e.code}")
        return None
    except Exception as e:
        logger.debug(f"Error fetching extension version for {tag}: {e}")
        return None


def fetch_release_by_tag(tag: str) -> Optional[dict]:
    """
    Fetch release info for a specific version tag.

    Args:
        tag: Version tag (e.g., "v0.5.1")

    Returns:
        dict: Release info with body (release notes) and html_url, or None if failed
    """
    # Ensure tag has 'v' prefix
    if not tag.startswith("v"):
        tag = f"v{tag}"

    url = f"https://api.github.com/repos/ronb1964/TuxTalk/releases/tags/{tag}"

    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "TuxTalk-UpdateChecker",
                "Accept": "application/vnd.github.v3+json"
            }
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            return {
                "tag_name": data.get("tag_name", ""),
                "name": data.get("name", ""),
                "body": data.get("body", ""),
                "html_url": data.get("html_url", ""),
                "published_at": data.get("published_at", ""),
            }

    except urllib.error.HTTPError as e:
        logger.debug(f"Could not fetch release for {tag}: {e.code}")
        return None
    except Exception as e:
        logger.debug(f"Error fetching release {tag}: {e}")
        return None


def get_releases_url() -> str:
    """Get the URL to the GitHub releases page."""
    return "https://github.com/ronb1964/TuxTalk/releases"


# Why this thread's last fetch_latest_release() failed: ("network" | "http" |
# "other", HTTP code or None), so the message can say what really happened.
# One per thread: the tray's daily check and a manual one can overlap.
_fetch_error = threading.local()


def _fetch_failure_message():
    kind, code = getattr(_fetch_error, "reason", ("other", None))
    if kind == "network":
        return "Could not connect to GitHub. Check your internet connection."
    if kind == "http" and code in (403, 429):
        return "GitHub is limiting update checks right now. Try again later."
    if kind == "http":
        return f"GitHub couldn't give TuxTalk's release information (error {code}). Try again later."
    return "Couldn't check for updates. Try again later."


def fetch_latest_release() -> Optional[dict]:
    """
    Fetch latest release info from GitHub API.

    Returns:
        dict: Release info with keys:
            - tag_name: Version tag (e.g., "v0.5.0")
            - body: Release notes markdown
            - html_url: URL to release page
            - assets: List of downloadable assets
            - appimage_url: Direct download URL for AppImage (if found)
            - extension_url: Direct download URL for extension (if found)
        None: If fetch failed
    """
    try:
        # Create request with User-Agent (GitHub requires this)
        request = urllib.request.Request(
            GITHUB_API_URL,
            headers={
                "User-Agent": "TuxTalk-UpdateChecker",
                "Accept": "application/vnd.github.v3+json"
            }
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

            # Extract useful info
            result = {
                "tag_name": data.get("tag_name", ""),
                "name": data.get("name", ""),
                "body": data.get("body", ""),
                "html_url": data.get("html_url", ""),
                "published_at": data.get("published_at", ""),
                "assets": data.get("assets", []),
                "appimage_url": None,
                "extension_url": None,
                "checksums_url": None,
                "rpm_url": None,
                "deb_url": None,
            }

            # Find AppImage, package, extension, and checksums URLs in assets
            for asset in result["assets"]:
                name = asset.get("name", "").lower()
                url = asset.get("browser_download_url", "")

                if name in ("sha256sums.txt", "checksums.txt"):
                    result["checksums_url"] = url
                elif name.endswith(".appimage"):
                    result["appimage_url"] = url
                    result["appimage_name"] = asset.get("name", "")
                elif name.endswith(".rpm"):
                    result["rpm_url"] = url
                    result["rpm_name"] = asset.get("name", "")
                elif name.endswith(".deb"):
                    result["deb_url"] = url
                    result["deb_name"] = asset.get("name", "")
                elif "extension" in name and name.endswith(".zip"):
                    result["extension_url"] = url

            return result

    except urllib.error.HTTPError as e:
        _fetch_error.reason = ("http", e.code)
        if e.code == 403:
            logger.warning("GitHub API rate limit exceeded. Try again later.")
        else:
            logger.warning(f"GitHub API error: {e.code} {e.reason}")
        return None
    except urllib.error.URLError as e:
        _fetch_error.reason = ("network", None)
        logger.warning(f"Network error checking for updates: {e.reason}")
        return None
    except Exception as e:
        _fetch_error.reason = ("other", None)
        logger.warning(f"Error fetching latest release: {e}")
        return None


def check_for_updates() -> dict:
    """
    Check for available updates.

    Main entry point for update checking. Compares current versions
    against latest GitHub release.

    Returns:
        dict: Update status with keys:
            - success: True if check completed successfully
            - error: Error message if failed
            - current_version: Current AppImage version
            - latest_version: Latest available version
            - update_available: True if AppImage update is available
            - extension_current: Current extension version (or None)
            - extension_latest: Latest extension version (parsed from release)
            - extension_update: True if extension update is available
            - release: Full release info dict (if successful)
    """
    result = {
        "success": False,
        "error": None,
        "current_version": get_current_version(),
        "latest_version": None,
        "update_available": False,
        "extension_current": get_extension_version(),
        "extension_latest": None,
        "extension_update": False,
        "release": None,
    }

    # Fetch latest release
    release = fetch_latest_release()
    if not release:
        result["error"] = _fetch_failure_message()
        return result

    result["release"] = release

    # Parse latest version from tag (strip 'v' prefix)
    tag = release.get("tag_name", "")
    latest_version = tag.lstrip("v") if tag else None
    result["latest_version"] = latest_version

    # Check if AppImage update is available
    if latest_version and result["current_version"] != "unknown":
        result["update_available"] = compare_versions(
            result["current_version"],
            latest_version
        )

    # Check extension update - smart comparison
    # Fetch the extension version from the release to compare with installed version
    tag = release.get("tag_name", "")
    if tag:
        latest_ext_version = fetch_extension_version_from_release(tag)
        result["extension_latest"] = latest_ext_version

        if result["extension_current"] is not None and latest_ext_version is not None:
            # Compare integer versions
            result["extension_update"] = latest_ext_version > result["extension_current"]
        elif result["extension_current"] is None and latest_ext_version is not None:
            # Extension not installed but available in release
            result["extension_update"] = False  # Not an "update", would be new install
        else:
            # Couldn't determine, assume no update needed
            result["extension_update"] = False

    result["success"] = True
    return result


def fetch_expected_sha256(checksums_url: str, filename: str) -> Optional[str]:
    """Fetch the release's SHA256SUMS.txt and return the hash for *filename*.

    Returns None when the file can't be fetched or doesn't list the filename
    (e.g. releases published before checksums were added).
    """
    from .download_utils import parse_sha256sums
    try:
        request = urllib.request.Request(
            checksums_url,
            headers={"User-Agent": "TuxTalk-UpdateChecker"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            sums = parse_sha256sums(response.read().decode("utf-8"))
        return sums.get(filename)
    except Exception as e:
        logger.warning(f"Could not fetch checksums file: {e}")
        return None


def download_update(
    url: str,
    filename: str,
    progress_callback: Optional[Callable[[str, int], None]] = None,
    checksums_url: Optional[str] = None,
) -> Optional[str]:
    """
    Download an update file with progress tracking and integrity checks.

    The file is downloaded to a temp name and only renamed into place after
    the size matches Content-Length and — when the release publishes a
    SHA256SUMS.txt — the sha256 matches. A truncated or corrupted download
    can therefore never be handed to the installer.

    Args:
        url: Download URL
        filename: Name for saved file
        progress_callback: Optional function(message, percent) for progress updates
        checksums_url: Optional URL of the release's SHA256SUMS.txt asset

    Returns:
        str: Path to downloaded file, or None if failed
    """
    from .download_utils import download_file, remove_stale_files

    try:
        # Create update directory
        os.makedirs(UPDATE_DIR, exist_ok=True)
        # A package whose installer outlived TuxTalk is never deleted by it.
        remove_stale_files(UPDATE_DIR, "*", STALE_UPDATE_S)
        dest_path = os.path.join(UPDATE_DIR, filename)

        if progress_callback:
            progress_callback("Starting download...", 0)

        # Look up the expected hash. Releases published before checksums
        # existed pass checksums_url=None, and only those may skip the hash.
        #
        # If the release DOES publish a checksums file and we still cannot get
        # a hash for this filename, that is a failure, not a licence to
        # proceed: the asset may be truncated, malformed, or serving someone
        # else's content, which are exactly the cases the check exists for.
        expected_sha256 = None
        if checksums_url:
            expected_sha256 = fetch_expected_sha256(checksums_url, filename)
            if not expected_sha256:
                logger.error(
                    f"{filename} is not listed in the release's SHA256SUMS.txt — "
                    f"refusing to install an unverified update"
                )
                if progress_callback:
                    progress_callback("Could not verify the update's integrity", 0)
                return None
            logger.info(f"Verifying download against published sha256 for {filename}")

        # Each 64 KB chunk calls this; only a new whole percent is passed on,
        # since every callback queues GTK redraws on the main loop.
        last_percent = [-1]

        def progress_hook(downloaded, total):
            if progress_callback and total > 0:
                percent = min(100, int((downloaded / total) * 100))
                if percent == last_percent[0]:
                    return
                last_percent[0] = percent
                downloaded_mb = downloaded / (1024 * 1024)
                total_mb = total / (1024 * 1024)
                progress_callback(
                    f"Downloading... {downloaded_mb:.1f} / {total_mb:.1f} MB",
                    percent
                )

        if not download_file(
            url, dest_path,
            timeout=60,
            progress_hook=progress_hook,
            expected_sha256=expected_sha256,
            user_agent="TuxTalk-UpdateChecker",
        ):
            if progress_callback:
                progress_callback("Download failed or file was corrupted", 0)
            return None

        if progress_callback:
            progress_callback("Download complete!", 100)

        # Make AppImage executable
        if filename.endswith(".AppImage"):
            os.chmod(dest_path, 0o755)

        return dest_path

    except Exception as e:
        logger.error(f"Download failed: {e}")
        if progress_callback:
            progress_callback(f"Download failed: {e}", 0)
        return None


def open_release_page(url: str):
    """Open release page in browser."""
    try:
        subprocess.run(["xdg-open", url], check=True)
    except Exception as e:
        logger.error(f"Could not open release page: {e}")


def should_check_today(last_check: str) -> bool:
    """
    Determine if we should check for updates (once per day).

    Args:
        last_check: ISO timestamp string of last check, or empty string

    Returns:
        bool: True if we should check (no check today or never checked)
    """
    from datetime import datetime, date

    if not last_check:
        return True

    try:
        # Parse the ISO timestamp
        last_check_time = datetime.fromisoformat(last_check)
        last_check_date = last_check_time.date()
        today = date.today()
        return last_check_date < today
    except (ValueError, TypeError):
        # Invalid timestamp, check anyway
        return True


def get_current_timestamp() -> str:
    """
    Get current time as ISO timestamp string.

    Returns:
        str: Current time in ISO format
    """
    from datetime import datetime
    return datetime.now().isoformat()


def is_running_from_appimage() -> bool:
    """
    Check if currently running from an AppImage.

    Returns:
        bool: True if running from AppImage, False otherwise
    """
    import sys
    # AppImages run from /tmp/.mount_* or use APPIMAGE env var
    appimage_env = os.environ.get("APPIMAGE", "")
    return bool(appimage_env) or "/tmp/.mount_" in sys.executable


def get_running_appimage_path() -> Optional[str]:
    """
    Get the path to the currently running AppImage.

    Returns:
        str: Path to AppImage, or None if not running from AppImage
    """
    return os.environ.get("APPIMAGE")


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
    # APPIMAGE is inherited by everything an AppImage starts: a .deb TuxTalk
    # opened from an AppImage terminal sees that terminal's. It is ours only
    # when this code runs from inside the AppImage.
    in_appdir = bool(appdir_env) and module_path.startswith(
        os.path.realpath(appdir_env).rstrip("/") + "/")
    if "/.mount_" not in module_path and not in_appdir:
        appimage_env = ""

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


def get_update_guidance(install_type: str, latest_version: str,
                        release_url: str = "") -> dict:
    """How to update a given install type. Returns a dict with:
      update_method: 'appimage_swap' (download+swap the AppImage in place),
                     'pkexec_package' (download the .rpm/.deb, install as root
                     via a PolicyKit password prompt), or 'manual' (show the
                     command; the app can't do it safely).
      can_auto_update: True for appimage_swap and pkexec_package.
      title, message: user-facing text. command: copy-paste command for manual.
    """
    v = latest_version
    if install_type == "appimage":
        return {"update_method": "appimage_swap", "can_auto_update": True,
                "title": "Update Available",
                "message": f"TuxTalk {v} is available.",
                "command": None, "release_url": release_url}
    if install_type in ("rpm", "deb"):
        return {"update_method": "pkexec_package", "can_auto_update": True,
                "title": "Update Available",
                "message": (f"TuxTalk {v} is available. TuxTalk can download and "
                            f"install it for you — your system will ask for your "
                            f"password to complete the install."),
                "command": None, "release_url": release_url}
    if install_type == "aur":
        cmd = "yay -S tuxtalk-appimage   # or: paru -S tuxtalk-appimage"
        msg = (f"TuxTalk {v} is available. You installed from the AUR — "
               f"update with your AUR helper:")
    elif install_type == "flatpak":
        from .config import APP_ID
        cmd = f"flatpak update {APP_ID}"
        msg = (f"TuxTalk {v} is available. Update through Flatpak (your software "
               f"center may also update it automatically):")
    elif install_type == "dev":
        cmd = "git pull   # in your TuxTalk project folder"
        msg = (f"TuxTalk {v} is available. You're running the development "
               f"version — update with git:")
    else:  # 'package' / unknown
        cmd = None
        msg = (f"TuxTalk {v} is available. Please update using your system's "
               f"package manager, or download it from the release page.")
    return {"update_method": "manual", "can_auto_update": False,
            "title": "Update Available",
            "message": msg, "command": cmd, "release_url": release_url}


def get_package_asset(release: dict, install_type: str) -> Tuple[Optional[str], Optional[str]]:
    """(download_url, filename) of the .rpm/.deb asset for this install type."""
    if install_type == "rpm":
        return (release.get("rpm_url"), release.get("rpm_name", "tuxtalk-update.rpm"))
    if install_type == "deb":
        return (release.get("deb_url"), release.get("deb_name", "tuxtalk-update.deb"))
    return (None, None)


def package_install_argv(install_type: str, package_path: str,
                         which: Optional[Callable[[str], Optional[str]]] = None):
    """The argv to install a downloaded package as root via pkexec, choosing the
    system package manager (so dependencies resolve). None if unsupported.
    `which` is injectable for testing."""
    if which is None:
        import shutil
        which = shutil.which
    if install_type == "rpm":
        if which("dnf"):
            return ["pkexec", "dnf", "install", "-y", package_path]
        if which("zypper"):
            return ["pkexec", "zypper", "--non-interactive", "install", package_path]
        if which("rpm"):
            return ["pkexec", "rpm", "-U", package_path]
    elif install_type == "deb":
        if which("apt"):
            return ["pkexec", "apt", "install", "-y", package_path]
        if which("apt-get"):
            return ["pkexec", "apt-get", "install", "-y", package_path]
        if which("dpkg"):
            return ["pkexec", "dpkg", "-i", package_path]
    return None


# Installing a package can legitimately take minutes: the package manager may be
# waiting on another transaction's lock. This bounds only how long TuxTalk WAITS
# — it never interrupts the installer. See install_package_update().
PACKAGE_INSTALL_TIMEOUT_S = 300.0


def _remove_package(package_path: str) -> None:
    """Delete a downloaded update package. It wraps the whole AppImage, and
    every version has its own file name, so each one left behind is
    hundreds of MB."""
    try:
        os.remove(package_path)
    except OSError:
        pass


def _finish_in_background(proc, package_path: str) -> None:
    """Wait for an installer that outlived PACKAGE_INSTALL_TIMEOUT_S on a
    background thread, then delete its package. If TuxTalk exits first the
    installer is unaffected, and the next update download sweeps the package."""

    def wait():
        try:
            proc.wait()
            logger.info(f"Background package install finished with exit code {proc.returncode}")
        except Exception as e:
            logger.warning(f"Background package install: {e}")
        _remove_package(package_path)

    threading.Thread(target=wait, name="package-install", daemon=True).start()


def install_package_update(package_path: str, install_type: str) -> Tuple[bool, str]:
    """Install a downloaded .rpm/.deb as root via pkexec (PolicyKit prompts for
    the password). Returns (success, message). Does NOT restart TuxTalk.
    The package file is deleted once the installer is done with it."""
    import tempfile
    argv = package_install_argv(install_type, package_path)
    if not argv:
        return (False, "No supported package manager was found to install the update.")
    # The installer writes to an unnamed file rather than a pipe: a pipe closes
    # when TuxTalk exits, and the installer's next write would then kill it
    # part-way through the transaction. The file disappears once both close it.
    with tempfile.TemporaryFile(dir=os.path.dirname(package_path) or None) as output:
        try:
            proc = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT)
        except FileNotFoundError:
            return (False, "pkexec (PolicyKit) is not available. Please install the "
                           "update through your package manager.")

        try:
            proc.wait(timeout=PACKAGE_INSTALL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            # Deliberately NOT killed. The child runs as root, so a user process
            # could not reap it anyway — and interrupting dpkg or rpm mid-transaction
            # can leave the package database broken, which is far worse than an
            # update that takes a long time. A package lock (unattended-upgrades on
            # Ubuntu, dnf-makecache on Fedora) is the usual cause and clears itself.
            # Let it finish in the background and give the dialog something to say.
            logger.warning(
                f"Package install still running after {PACKAGE_INSTALL_TIMEOUT_S}s; "
                f"leaving it to finish")
            _finish_in_background(proc, package_path)
            return (False, "The installer is taking longer than expected — your system "
                           "may be waiting on another package operation. It will keep "
                           "running in the background; check again in a few minutes.")
        output.seek(0)
        detail = output.read().decode("utf-8", "replace").strip()
    _remove_package(package_path)
    if proc.returncode == 0:
        return (True, "Update installed. Restart TuxTalk to use the new version.")
    from .uinput_helper import pkexec_was_declined
    if pkexec_was_declined(subprocess.CompletedProcess(argv, proc.returncode, stderr=detail)):
        return (False, "Authorization was cancelled — the update was not installed.")
    if proc.returncode == 127:
        return (False, "Could not start the installer (authorization failed).")
    # The last lines hold the error; apt and dnf print pages of progress first.
    detail = "\n".join(detail.splitlines()[-8:])
    return (False, f"Install failed: {detail or ('exit code ' + str(proc.returncode))}")


def restart_via_launcher(target: Optional[str] = None) -> Tuple[bool, str]:
    """Spawn a fresh, DETACHED TuxTalk that waits for THIS process to fully
    exit before starting. That clean-slate start is essential: an in-place
    os.execv restart brings the new tray up while the old process's
    dictation-service child, D-Bus name and single-instance lock are still
    held, so the new tray either exits at once or keeps the old service.
    The caller MUST quit cleanly afterwards (the tray's quit_app, which stops
    the service + releases the name/lock); this function does NOT exit the
    current process itself.

    target: the AppImage to start (its AppRun opens the tray with no
    arguments). Without one, the package launcher is used: /usr/bin/tuxtalk,
    or /usr/bin/talktype on packages from before 0.15.0."""
    import shlex
    if target:
        command = shlex.quote(target)
        if not os.access(target, os.X_OK):
            return (False, f"{target} is missing or not executable; please restart TuxTalk manually.")
    else:
        launcher = next((p for p in ("/usr/bin/tuxtalk", "/usr/bin/talktype")
                         if os.path.exists(p)), "/usr/bin/tuxtalk")
        if not os.path.exists(launcher):
            return (False, f"Launcher not found at {launcher}; please restart TuxTalk manually.")
        command = f"{shlex.quote(launcher)} tray"
    # start_new_session detaches it so it survives this process exiting.
    script = handover_script(os.getpid(), command)
    try:
        subprocess.Popen(["/bin/sh", "-c", script],
                         start_new_session=True, close_fds=True)
    except Exception as e:
        return (False, f"Could not start the new instance: {e}")
    return (True, "restarting")


def handover_script(oldpid: int, command: str) -> str:
    """The shell script that starts *command* once process *oldpid* (the old
    tray, waiting for its service to type what it holds, or for the first-run
    copy) has exited, however long that takes. Never while it runs: the new
    tray would find its lock, exit, and nothing would be left running."""
    return f"while kill -0 {oldpid} 2>/dev/null; do sleep 0.5; done; exec {command}"


def _not_enough_space(src: str, dest_dir: str) -> Optional[str]:
    """A message when dest_dir can't hold a second full copy of src, else None.
    The 50 MB margin keeps a nearly full disk usable after the copy."""
    from .download_utils import free_space_bytes
    try:
        needed = os.path.getsize(src)
    except OSError:
        return None
    free = free_space_bytes(dest_dir)
    if needed and free and free < needed + 50 * 1024 * 1024:
        return (f"Not enough disk space: need ~{needed / (1024 ** 2):.0f} MB "
                f"free in {dest_dir}")
    return None


# How long a copy into ~/AppImages waits for another to finish (the
# first-run copy of a large AppImage on a slow disk).
INSTALL_WAIT_S = 300


def _install_appimage_file(src: str, dest: str = APPIMAGE_PATH) -> None:
    """Copy src onto dest so that dest is always either the old file or the
    complete new one. Copies take turns, in the order they began: an update
    installed while the first-run copy ran was replaced by that older copy
    when it finished. Raises on failure."""
    from . import single_instance
    # No private folder for the lock (a hostile /tmp) raises: copying anyway
    # could put the older file last.
    with single_instance.open_lock("appimage-install.lock") as lock:
        if not single_instance.claim(lock, wait_s=INSTALL_WAIT_S):
            raise OSError("another copy into ~/AppImages didn't finish")
        _copy_into_place(src, dest)


def _copy_into_place(src: str, dest: str) -> None:
    """_install_appimage_file's copy, its lock held. The copy goes to a
    hidden sibling first and is renamed into place: os.replace() unlinks a
    running AppImage rather than writing into it ("Text file busy"), and a
    full disk or a quit part-way through leaves dest untouched; the partial
    copy is removed."""
    import glob
    import shutil
    import tempfile
    from .download_utils import STALE_PARTIAL_S, remove_stale_files
    dest_dir = os.path.dirname(dest)
    os.makedirs(dest_dir, exist_ok=True)
    prefix = f".{os.path.basename(dest)}."
    remove_stale_files(dest_dir, glob.escape(prefix) + "*.part", STALE_PARTIAL_S)
    fd, staging_path = tempfile.mkstemp(dir=dest_dir, prefix=prefix, suffix=".part")
    os.close(fd)
    try:
        shutil.copy2(src, staging_path)
        os.chmod(staging_path, 0o755)
        os.replace(staging_path, dest)
    except BaseException:
        try:
            os.remove(staging_path)
        except OSError:
            pass
        raise


def install_appimage_update(
    downloaded_path: str,
    progress_callback: Optional[Callable[[str, int], None]] = None
) -> Tuple[bool, str]:
    """
    Install a downloaded AppImage update at APPIMAGE_PATH
    (~/AppImages/TuxTalk.AppImage). It does NOT restart anything: only the
    tray can do that cleanly (its restart_app stops the dictation service,
    then starts the new copy), so the caller asks the tray afterwards.

    Args:
        downloaded_path: Path to the downloaded AppImage
        progress_callback: Optional function(message, percent) for progress updates

    Returns:
        Tuple[bool, str]: (success, message)
    """
    import shutil

    try:
        if progress_callback:
            progress_callback("Installing update...", 50)

        space_problem = _not_enough_space(downloaded_path, APPIMAGE_DIR)
        if space_problem:
            msg = f"Could not install the update. {space_problem}"
            logger.error(msg)
            return (False, msg)

        if progress_callback:
            progress_callback("Copying to AppImages folder...", 60)
        _install_appimage_file(downloaded_path, APPIMAGE_PATH)

        if progress_callback:
            progress_callback("Update installed!", 90)

        # The download folder only ever holds this update.
        update_dir = os.path.dirname(downloaded_path)
        if update_dir and os.path.isdir(update_dir):
            shutil.rmtree(update_dir, ignore_errors=True)

        logger.info(f"Update installed to {APPIMAGE_PATH}")

        # Tells the new version it was just updated, so it can say so.
        try:
            just_updated_file = os.path.join(APPIMAGE_DIR, ".tuxtalk_just_updated")
            with open(just_updated_file, "w") as f:
                from . import __version__
                f.write(__version__)
        except Exception:
            pass  # Not critical

        return (True, "Update installed.")

    except Exception as e:
        logger.error(f"Failed to install update: {e}")
        return (False, f"Failed to install update: {e}")


def _refresh_desktop_menu_cache():
    """
    Refresh the desktop menu cache after creating/removing a .desktop file.

    This is especially needed for KDE/Plasma which doesn't auto-refresh.
    Runs silently - failures are logged but don't raise exceptions.
    """
    import subprocess
    import shutil
    import time

    # Small delay to ensure .desktop file is fully written
    time.sleep(0.5)

    logger.info("Refreshing desktop menu cache...")

    # Try KDE's cache refresh (kbuildsycoca5 or kbuildsycoca6)
    # Check both PATH and common absolute locations (AppImage may not have full PATH)
    kde_commands = [
        '/usr/bin/kbuildsycoca6', '/usr/bin/kbuildsycoca5',
        'kbuildsycoca6', 'kbuildsycoca5',
    ]

    # Plain first, then with --noincremental.
    for parts in [['kbuildsycoca6'], ['kbuildsycoca5'],
                  ['kbuildsycoca6', '--noincremental'], ['kbuildsycoca5', '--noincremental']]:
        cmd = ' '.join(parts)
        try:
            print(f"Running: {cmd}")
            result = subprocess.run(parts, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                logger.info(f"Desktop menu cache refreshed using: {cmd}")
                print(f"Desktop menu refreshed with: {cmd}")
                return
            else:
                logger.debug(f"{cmd} returned {result.returncode}: {result.stderr}")
                print(f"{cmd} returned {result.returncode}")
        except Exception as e:
            logger.debug(f"Could not run {cmd}: {e}")
            print(f"Could not run {cmd}: {e}")

    # Fallback: try direct path execution
    for cmd in kde_commands:
        # Check if command exists (either in PATH or as absolute path)
        if cmd.startswith('/'):
            cmd_exists = os.path.exists(cmd)
            actual_cmd = cmd
        else:
            actual_cmd = shutil.which(cmd)
            cmd_exists = actual_cmd is not None

        logger.debug(f"Checking {cmd}: exists={cmd_exists}")

        if cmd_exists:
            try:
                result = subprocess.run([actual_cmd, '--noincremental'],
                             capture_output=True, text=True, timeout=15)
                if result.returncode == 0:
                    logger.info(f"Desktop menu cache refreshed using {actual_cmd}")
                    return
                else:
                    logger.warning(f"{actual_cmd} returned {result.returncode}: {result.stderr}")
            except Exception as e:
                logger.debug(f"Could not run {cmd}: {e}")

    # Try update-desktop-database for other desktops
    for cmd in ['update-desktop-database', '/usr/bin/update-desktop-database']:
        cmd_exists = shutil.which(cmd) if not cmd.startswith('/') else os.path.exists(cmd)
        if cmd_exists:
            try:
                actual_cmd = cmd if cmd.startswith('/') else shutil.which(cmd)
                apps_dir = os.path.dirname(DESKTOP_FILE_PATH)
                subprocess.run([actual_cmd, apps_dir],
                             capture_output=True, timeout=15)
                logger.info("Desktop menu cache refreshed using update-desktop-database")
                return
            except Exception as e:
                logger.debug(f"Could not run {cmd}: {e}")

    logger.warning("Could not refresh desktop menu cache - no suitable command found")


def _app_icon_path() -> str:
    """Where _install_app_icon puts the icon its name stands for."""
    return os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps/tuxtalk.png")


def installed_app_icon() -> Optional[str]:
    """The icon name ("tuxtalk") once _install_app_icon has put it in place,
    else None. Only looks: installing refreshes icon caches, which can take
    seconds, and the caller may be a window's thread."""
    return "tuxtalk" if os.path.exists(_app_icon_path()) else None


def _install_app_icon() -> Optional[str]:
    """
    Install the TuxTalk icon to the user's icon directory.

    Copies the app icon from the AppImage or source to ~/.local/share/icons/
    so it can be referenced by the desktop launcher.

    For KDE/Plasma compatibility, installs icons in multiple sizes.

    Returns:
        str: Icon name to use in .desktop file, or None if installation failed
    """
    import shutil

    icons_base = os.path.expanduser("~/.local/share/icons/hicolor")
    primary_icon_path = _app_icon_path()

    # If icon already installed, just return the name
    if os.path.exists(primary_icon_path):
        logger.debug("TuxTalk icon already installed")
        return "tuxtalk"

    # Find the source icon
    icon_source = None

    # Try 1: Look in AppImage location
    appimage_path = os.environ.get("APPIMAGE")
    if appimage_path:
        # Icon should be next to the AppImage or inside mounted AppDir
        appdir = os.environ.get("APPDIR", "")
        if appdir:
            possible_paths = [
                os.path.join(appdir, "io.github.ronb1964.TuxTalk.png"),
                os.path.join(appdir, "tuxtalk.png"),
                os.path.join(appdir, "usr", "share", "icons", "hicolor", "256x256", "apps", "tuxtalk.png"),
            ]
            for p in possible_paths:
                if os.path.exists(p):
                    icon_source = p
                    logger.info(f"Found icon in AppImage: {p}")
                    break

    # Try 2: Look relative to this module (for dev mode)
    if not icon_source:
        module_dir = Path(__file__).parent
        project_root = module_dir.parent.parent
        possible_paths = [
            project_root / "io.github.ronb1964.TuxTalk.png",
            project_root / "icons" / "TT_square_light.png",
        ]
        for p in possible_paths:
            if p.exists():
                icon_source = str(p)
                logger.info(f"Found icon at: {p}")
                break

    if not icon_source:
        logger.warning("Could not find TuxTalk icon to install")
        print("Could not find TuxTalk icon source")
        return None

    try:
        # Install icon in multiple sizes for better KDE/Plasma compatibility
        # KDE looks for icons in various sizes and may not scale properly
        icon_sizes = ["256x256", "128x128", "64x64", "48x48", "32x32"]

        for size in icon_sizes:
            icon_dir = os.path.join(icons_base, size, "apps")
            icon_path = os.path.join(icon_dir, "tuxtalk.png")

            # Create icon directory
            os.makedirs(icon_dir, exist_ok=True)

            # Copy icon (same source for all sizes - KDE will handle scaling)
            shutil.copy2(icon_source, icon_path)
            logger.debug(f"Installed icon to {icon_path}")

        logger.info(f"Installed TuxTalk icon to {icons_base} ({len(icon_sizes)} sizes)")
        print(f"Installed TuxTalk icon ({len(icon_sizes)} sizes)")

        # Update icon cache (for GTK-based desktops)
        import subprocess
        try:
            subprocess.run(['gtk-update-icon-cache', '-f', '-t', icons_base],
                         capture_output=True, timeout=10)
            logger.debug("Updated GTK icon cache")
        except Exception as e:
            logger.debug(f"GTK icon cache update failed (not critical): {e}")

        # Also try KDE's icon cache (breeze-icon-theme uses this)
        try:
            # xdg-icon-resource is more universal
            subprocess.run(['xdg-icon-resource', 'forceupdate', '--mode', 'user'],
                         capture_output=True, timeout=10)
            logger.debug("Updated XDG icon cache")
        except Exception as e:
            logger.debug(f"XDG icon cache update failed (not critical): {e}")

        return "tuxtalk"

    except Exception as e:
        logger.warning(f"Could not install TuxTalk icon: {e}")
        print(f"Failed to install icon: {e}")
        return None


# The launcher's right-click shortcuts (tuxtalk --open / --quit, control.py).
# Plain GNOME has no tray, so without the extension these and opening TuxTalk
# again (its menu window) are how people reach Preferences and the rest.
# Same list as io.github.ronb1964.TuxTalk.desktop and aur/tuxtalk.desktop.
DESKTOP_ACTIONS = (("Preferences", "Preferences", "--open preferences"),
                   ("TranscribeFile", "Transcribe a File", "--open transcribe"),
                   ("Help", "Help", "--open help"),
                   ("Quit", "Quit TuxTalk", "--quit"))


def desktop_actions(program: str) -> str:
    """The Actions= line and [Desktop Action] groups for *program*."""
    out = "Actions=" + "".join(f"{aid};" for aid, _, _ in DESKTOP_ACTIONS) + "\n"
    for aid, name, args in DESKTOP_ACTIONS:
        out += f"\n[Desktop Action {aid}]\nName={name}\nExec={program} {args}\n"
    return out


def point_at_running_appimage(running: str) -> None:
    """Point launch-at-login and the menu launcher back at the AppImage
    *running* when first-run setup's copy into ~/AppImages failed, so
    neither starts a file that isn't there. Files only, so any thread."""
    try:
        from .autostart import enabled_entry_exists, set_autostart
        # The file, not the setting: written by first run with the setting
        # left unsaved (a read-only settings folder), it still points here.
        # One the user turned off (a Hidden=true stub) stays off.
        # The running file by name: the installed copy may be the older
        # version the failed copy didn't replace.
        if enabled_entry_exists():
            if set_autostart(True, command=desktop_exec_program(running)):
                logger.info("Launch at login points at the running AppImage instead")
            else:
                logger.warning("Could not repoint launch at login at the running AppImage")
    except Exception as e:
        logger.warning(f"Could not repoint launch at login: {e}")
    ok, msg = create_desktop_launcher(running)
    if ok:
        logger.info("The menu launcher points at the running AppImage instead")
    else:
        logger.warning(f"Could not repoint the menu launcher: {msg}")


def create_desktop_launcher(appimage_exec_path: Optional[str] = None) -> Tuple[bool, str]:
    """
    Create a desktop launcher (.desktop file) for TuxTalk.

    Creates a launcher in ~/.local/share/applications/ that points to
    the standard AppImage location (APPIMAGE_PATH, ~/AppImages/TuxTalk.AppImage),
    or to *appimage_exec_path*: the running AppImage, when the copy there failed.

    Returns:
        Tuple[bool, str]: (success, message)
    """
    try:
        # Ensure applications directory exists
        apps_dir = os.path.dirname(DESKTOP_FILE_PATH)
        os.makedirs(apps_dir, exist_ok=True)

        # Install the TuxTalk icon and get the icon name to use
        icon_name = _install_app_icon()
        if not icon_name:
            icon_name = "audio-input-microphone"  # Fallback to system icon

        # Get the current version for the comment
        version = get_current_version()

        # The file first-run setup and the updater install to.
        appimage_exec_path = appimage_exec_path or APPIMAGE_PATH
        logger.info(f"Desktop launcher Exec path: {appimage_exec_path}")
        # Quoted where it must be: a download named "TuxTalk (1).AppImage"
        # was cut at the space, and the menu entry opened nothing.
        program = desktop_exec_program(appimage_exec_path)

        # Desktop file content
        desktop_content = f"""[Desktop Entry]
Name=TuxTalk
Comment=Hold a key, talk, and your words are typed for you (v{version})
Exec={program}
Icon={icon_name}
Type=Application
Categories=Utility;Accessibility;AudioVideo;
Keywords=dictation;speech;voice;transcription;whisper;
StartupNotify=true
Terminal=false
{desktop_actions(program)}"""

        # Write the desktop file
        with open(DESKTOP_FILE_PATH, "w") as f:
            f.write(desktop_content)

        # Make it executable (not strictly required but good practice)
        os.chmod(DESKTOP_FILE_PATH, 0o755)

        # The menu cache (KDE's above all) is refreshed in the background, as
        # add_launcher_actions does: it takes seconds, and Setup Complete waits.
        threading.Thread(target=_refresh_desktop_menu_cache, daemon=True).start()

        logger.info(f"Desktop launcher created at {DESKTOP_FILE_PATH}")
        return (True, "Desktop launcher created!\n\nTuxTalk is now in your Applications menu.")

    except Exception as e:
        logger.error(f"Failed to create desktop launcher: {e}")
        return (False, f"Failed to create launcher: {e}")


# Marks the hidden entry below, so first run still writes the real launcher.
_HIDDEN_ENTRY_MARK = "X-TuxTalk-Hidden-Entry=true"

def add_launcher_actions() -> bool:
    """Give a menu launcher written before 0.16 the right-click shortcuts
    (desktop_actions). Only a self-managed AppImage's own launcher is
    touched, and only once (it then has an Actions= line). True if changed."""
    try:
        if get_install_type(pkg_query=lambda: "package") != "appimage":
            return False        # packages ship their launcher with the shortcuts
        with open(DESKTOP_FILE_PATH) as f:
            text = f.read()
        lines = text.splitlines()
        if _HIDDEN_ENTRY_MARK in text or any(l.startswith("Actions=") for l in lines):
            return False
        exec_line = next((l for l in lines if l.startswith("Exec=")), None)
        if exec_line is None:
            return False
        # The program as the launcher runs it, without field codes like %U.
        program = " ".join(w for w in exec_line[len("Exec="):].split(" ")
                           if not (len(w) == 2 and w.startswith("%")))
        # The new groups go at the end, so Actions= must land in the main group.
        main_end = next((i for i, l in enumerate(lines[1:], 1) if l.startswith("[")), len(lines))
        actions = desktop_actions(program).split("\n", 1)
        new_lines = lines[:main_end] + [actions[0]] + lines[main_end:]
        tmp = DESKTOP_FILE_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(new_lines).rstrip("\n") + "\n" + actions[1])
        os.replace(tmp, DESKTOP_FILE_PATH)
        threading.Thread(target=_refresh_desktop_menu_cache, daemon=True).start()
        logger.info("Added the right-click shortcuts to the menu launcher")
        return True
    except OSError:
        return False            # no launcher yet
    except Exception as e:
        logger.warning(f"Could not add the launcher's right-click shortcuts: {e}")
        return False


def desktop_launcher_exists() -> bool:
    """True when the menu launcher exists (the hidden entry doesn't count)."""
    try:
        with open(DESKTOP_FILE_PATH) as f:
            return _HIDDEN_ENTRY_MARK not in f.read()
    except OSError:
        return False


def create_hidden_desktop_entry() -> bool:
    """Write a hidden tuxtalk.desktop for a self-managed AppImage that has no
    launcher yet. KDE and GNOME find a window's icon through the .desktop file
    its program name names, so without this first-run setup shows the
    desktop's generic icon. NoDisplay keeps it out of the app menu, and the
    Ready screen replaces it with the real launcher. Returns True if written."""
    try:
        if get_install_type(pkg_query=lambda: "package") != "appimage":
            return False        # other installs ship their own launcher
        if os.path.lexists(DESKTOP_FILE_PATH):
            return False
        icon_name = _install_app_icon() or "audio-input-microphone"
        os.makedirs(os.path.dirname(DESKTOP_FILE_PATH), exist_ok=True)
        # Exec is required for a valid entry; the running AppImage exists now.
        exec_path = desktop_exec_program(get_running_appimage_path() or APPIMAGE_PATH)
        tmp = DESKTOP_FILE_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write("[Desktop Entry]\n"
                    "Name=TuxTalk\n"
                    f"Exec={exec_path}\n"
                    f"Icon={icon_name}\n"
                    "Type=Application\n"
                    "NoDisplay=true\n"
                    "Terminal=false\n"
                    f"{_HIDDEN_ENTRY_MARK}\n")
        os.replace(tmp, DESKTOP_FILE_PATH)
        # Plasma 5's menu cache doesn't rebuild by itself; this takes a moment.
        threading.Thread(target=_refresh_desktop_menu_cache, daemon=True).start()
        logger.info("Hidden desktop entry written so setup windows show TuxTalk's icon")
        return True
    except Exception as e:
        logger.warning(f"Could not write the hidden desktop entry: {e}")
        return False


def ensure_appimage_in_standard_location() -> Tuple[bool, str]:
    """
    Ensure the AppImage is in the standard location.

    If running from an AppImage that's not in ~/AppImages/, offer to copy it there.

    Returns:
        Tuple[bool, str]: (already_there, current_path)
            - already_there: True if AppImage is already in standard location
            - current_path: Path to the running AppImage (or None if not AppImage)
    """
    current_path = get_running_appimage_path()

    if not current_path:
        return (False, None)

    # Normalize paths for comparison
    current_normalized = os.path.realpath(current_path)
    standard_normalized = os.path.realpath(APPIMAGE_PATH)

    if current_normalized == standard_normalized:
        return (True, current_path)

    return (False, current_path)


def copy_appimage_to_standard_location() -> Tuple[bool, str]:
    """
    Copy the currently running AppImage to the standard location.

    Returns:
        Tuple[bool, str]: (success, message)
    """
    current_path = get_running_appimage_path()
    if not current_path:
        return (False, "Not running from an AppImage.")

    try:
        space_problem = _not_enough_space(current_path, APPIMAGE_DIR)
        if space_problem:
            logger.error(f"Could not copy the AppImage. {space_problem}")
            return (False, space_problem)

        # The desktop launcher and autostart already point at APPIMAGE_PATH,
        # so it must never hold a half-written copy.
        _install_appimage_file(current_path, APPIMAGE_PATH)

        logger.info(f"AppImage copied to {APPIMAGE_PATH}")
        return (True, f"AppImage installed to:\n{APPIMAGE_PATH}")

    except Exception as e:
        logger.error(f"Failed to copy AppImage: {e}")
        return (False, f"Failed to copy AppImage: {e}")


def check_just_updated() -> Optional[str]:
    """
    Check if the app was just updated and return the previous version.

    This checks for a flag file written by install_appimage_update()
    before the app restarted. If found, the flag is deleted and the
    previous version is returned so a notification can be shown.

    Returns:
        str: Previous version string if just updated, None otherwise
    """
    # A 0.14.x (TalkType) updater leaves the marker under the old name.
    for marker in (".tuxtalk_just_updated", ".talktype_just_updated"):
        just_updated_file = os.path.join(APPIMAGE_DIR, marker)
        if os.path.exists(just_updated_file):
            break
    else:
        return None

    try:
        with open(just_updated_file, "r") as f:
            previous_version = f.read().strip()

        # Delete the flag file
        os.remove(just_updated_file)

        return previous_version
    except Exception as e:
        logger.debug(f"Error checking just_updated flag: {e}")
        # Try to clean up the file anyway
        try:
            os.remove(just_updated_file)
        except Exception:
            pass
        return None


# Print what the checker sees, for checking it by hand.
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("Testing update checker...")
    print(f"Current version: {get_current_version()}")
    print(f"Extension version: {get_extension_version()}")
    print(f"Running from AppImage: {is_running_from_appimage()}")
    print(f"AppImage path: {get_running_appimage_path()}")
    print(f"Standard location: {APPIMAGE_PATH}")
    print(f"Desktop launcher exists: {desktop_launcher_exists()}")

    print("\nChecking for updates...")
    result = check_for_updates()

    if result["success"]:
        print(f"Latest version: {result['latest_version']}")
        print(f"Update available: {result['update_available']}")
        if result["release"]:
            print(f"Release URL: {result['release'].get('html_url')}")
            print(f"AppImage URL: {result['release'].get('appimage_url')}")
    else:
        print(f"Error: {result['error']}")
