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
    ext_path = (Path.home() / ".local/share/gnome-shell/extensions" / EXTENSION_UUID
                / "metadata.json")

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

    return parse_version(latest) > parse_version(current)


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
    except Exception as e:      # offline, or an odd answer: the extension check waits
        logger.info(f"Error fetching extension version for {tag}: {e}")
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
    except Exception as e:      # offline, or an odd answer: no release notes this time
        logger.info(f"Error fetching release {tag}: {e}")
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
        return (f"GitHub couldn't give TuxTalk's release information (error {code}). "
                "Try again later.")
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
    from .pkexec_runner import was_declined
    if was_declined(proc.returncode, detail):
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
            return (False, f"{target} is missing or not executable; "
                           "please restart TuxTalk manually.")
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
    except OSError as e:        # the caller shows it and the user restarts by hand
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

        # Only this download: the folder is shared, and the tray and
        # Preferences may each be downloading an update into it.
        try:
            os.remove(downloaded_path)
        except OSError as e:
            logger.info(f"Couldn't remove the downloaded update: {e}")

        logger.info(f"Update installed to {APPIMAGE_PATH}")

        # Tells the new version it was just updated, so it can say so.
        try:
            just_updated_file = os.path.join(APPIMAGE_DIR, ".tuxtalk_just_updated")
            with open(just_updated_file, "w") as f:
                from . import __version__
                f.write(__version__)
        except OSError as e:    # only the "updated" notice is lost
            logger.info(f"Couldn't leave the just-updated note: {e}")

        return (True, "Update installed.")

    except Exception as e:
        logger.error(f"Failed to install update: {e}")
        return (False, f"Failed to install update: {e}")


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
    except (OSError, UnicodeDecodeError) as e:
        logger.info(f"Error checking just_updated flag: {e}")
        # Try to clean up the file anyway
        try:
            os.remove(just_updated_file)
        except OSError:
            pass        # it can't be read or removed: nothing more to do about a note
        return None
