#!/usr/bin/env python3
"""
Update checker for TuxTalk

Asks GitHub whether a newer TuxTalk (and GNOME extension) has been released,
and fetches a release's notes. Putting an update in place is update_install;
how this TuxTalk was installed is installation.
"""

import json
import logging
import re
import subprocess
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Tuple

from . import gnome_extensions
from .extension_helper import EXTENSION_UUID

logger = logging.getLogger(__name__)

# GitHub API endpoint for latest release
GITHUB_API_URL = "https://api.github.com/repos/ronb1964/TuxTalk/releases/latest"

# GitHub raw content URL for fetching extension metadata from a specific tag
GITHUB_RAW_URL = "https://raw.githubusercontent.com/ronb1964/TuxTalk"


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
    ext_path = Path(gnome_extensions.extension_folder(EXTENSION_UUID)) / "metadata.json"

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
