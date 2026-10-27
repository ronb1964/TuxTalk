#!/usr/bin/env python3
"""
GNOME Extension installer for TuxTalk
"""

import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Optional, Callable

from . import desktop_detect

logger = logging.getLogger(__name__)


EXTENSION_UUID = 'talktype@ronb1964.github.io'
# Every release publishes the zip under this TalkType name and as
# tuxtalk-gnome-extension.zip, so older installs find it too.
EXTENSION_ZIP_NAME = 'talktype-gnome-extension.zip'
EXTENSION_DOWNLOAD_URL = f'https://github.com/ronb1964/TuxTalk/releases/latest/download/{EXTENSION_ZIP_NAME}'
# The release publishes SHA256SUMS.txt alongside the zip. The extension
# contains JavaScript that runs in GNOME Shell, so verifying the download
# closes a code-execution vector (a corrupted or tampered zip).
EXTENSION_CHECKSUMS_URL = 'https://github.com/ronb1964/TuxTalk/releases/latest/download/SHA256SUMS.txt'


class ChecksumUnavailableError(Exception):
    """The release publishes checksums, but the extension's could not be read.

    Distinct from "this release publishes no checksums at all", which is a
    supported state (every release up to v0.5.16) and returns None instead.
    """


def _fetch_extension_sha256() -> Optional[str]:
    """Fetch the expected sha256 of the extension zip from the release.

    Returns None when the release publishes no SHA256SUMS.txt at all (a 404),
    which is true of every release up to and including v0.5.16 — those must
    keep installing, verified by the size/truncation check only.

    Raises ChecksumUnavailableError when the checksums file exists but the
    extension is not listed in it, or when the fetch fails for any other
    reason: an unverifiable zip must not be treated like a release that never
    published checksums, or the check would miss the very cases it exists for.
    """
    from .download_utils import parse_sha256sums
    try:
        request = urllib.request.Request(
            EXTENSION_CHECKSUMS_URL, headers={"User-Agent": "TuxTalk"})
        with urllib.request.urlopen(request, timeout=10) as response:
            sums = parse_sha256sums(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # A 404 is also what a release that can't be found at all gives
            # (the 0.15.0 test run, before the repo was renamed), so the log
            # says both, with the address, rather than blaming the release.
            logger.info(
                f"No SHA256SUMS.txt at {EXTENSION_CHECKSUMS_URL} (404): either the "
                "release publishes none, or the release can't be found. Installing "
                "the extension with size verification only"
            )
            return None
        raise ChecksumUnavailableError(f"Could not fetch extension checksums: {e}")
    except Exception as e:
        raise ChecksumUnavailableError(f"Could not fetch extension checksums: {e}")

    digest = sums.get(EXTENSION_ZIP_NAME)
    if not digest:
        raise ChecksumUnavailableError(
            f"{EXTENSION_ZIP_NAME} is not listed in the release's SHA256SUMS.txt"
        )
    return digest


def is_extension_available() -> bool:
    """
    Check if GNOME extension feature is available

    Returns:
        bool: True if running GNOME desktop
    """
    return desktop_detect.is_gnome()


def is_extension_installed() -> bool:
    """Check if TuxTalk GNOME extension is installed"""
    return desktop_detect.is_extension_installed(EXTENSION_UUID)


def is_extension_enabled() -> bool:
    """Check if TuxTalk GNOME extension is enabled"""
    return desktop_detect.is_extension_enabled(EXTENSION_UUID)


def get_extension_status() -> dict:
    """
    Get comprehensive extension status

    Returns:
        dict: Status information
    """
    return {
        'available': is_extension_available(),
        'installed': is_extension_installed(),
        'enabled': is_extension_enabled(),
        'gnome_version': desktop_detect.get_gnome_shell_version(),
        'wayland': desktop_detect.is_wayland(),
    }


def _swap_in(source_dir, target_dir):
    """Put a copy of *source_dir* at *target_dir*, the old one going only
    once the new one is all there: removed first, a copy that failed
    partway (a full disk) left GNOME with no TuxTalk menu at all. The copy
    is made beside the extensions folder, on the same filesystem, where
    GNOME doesn't look for extensions."""
    parent = os.path.dirname(os.path.dirname(target_dir))
    staging = os.path.join(parent, f".{EXTENSION_UUID}.new")
    old = os.path.join(parent, f".{EXTENSION_UUID}.old")
    for leftover in (staging, old):
        shutil.rmtree(leftover, ignore_errors=True)
    shutil.copytree(source_dir, staging)
    if os.path.exists(target_dir):
        os.rename(target_dir, old)
    os.rename(staging, target_dir)
    shutil.rmtree(old, ignore_errors=True)


def install_extension_from_local(source_dir: str, auto_enable: bool = True) -> bool:
    """
    Install extension from local directory

    Args:
        source_dir: Path to extension source directory
        auto_enable: If True, automatically enable the extension after installation

    Returns:
        bool: True if successful
    """
    try:
        ext_dir = desktop_detect.get_extension_dir()
        target_dir = os.path.join(ext_dir, EXTENSION_UUID)

        os.makedirs(ext_dir, exist_ok=True)
        _swap_in(source_dir, target_dir)

        print(f"Extension installed to {target_dir}")

        # Auto-enable if requested
        if auto_enable:
            if enable_extension():
                print("Extension enabled - will be active after logout/login")
            else:
                print("Failed to auto-enable extension - you can enable it manually")

        return True

    except Exception as e:
        print(f"Failed to install extension: {e}")
        return False


def get_bundled_extension_path() -> Optional[str]:
    """
    Find the bundled GNOME extension in the AppImage or dev environment.

    Returns:
        str: Path to bundled extension directory, or None if not found
    """
    import sys

    # Possible locations for bundled extension
    possible_paths = []

    # Check if running from AppImage
    appimage_path = os.environ.get('APPIMAGE')
    if appimage_path:
        # AppImage mount point - extension should be in usr/share/gnome-extension/
        appimage_mount = os.path.dirname(sys.executable)
        possible_paths.append(
            os.path.join(appimage_mount, '..', 'share', 'gnome-extension', EXTENSION_UUID)
        )

    # Check squashfs mount point (AppImage internal path)
    if '/tmp/.mount_' in sys.executable or 'squashfs-root' in sys.executable:
        base = os.path.dirname(os.path.dirname(sys.executable))
        possible_paths.append(
            os.path.join(base, 'share', 'gnome-extension', EXTENSION_UUID)
        )

    # Development environment - relative to this file
    this_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(this_dir))
    possible_paths.append(
        os.path.join(project_root, 'gnome-extension', EXTENSION_UUID)
    )

    # Check each path
    for path in possible_paths:
        normalized = os.path.normpath(path)
        metadata_file = os.path.join(normalized, 'metadata.json')
        if os.path.isfile(metadata_file):
            print(f"Found bundled extension at: {normalized}")
            return normalized

    return None


def install_extension(progress_callback: Optional[Callable] = None) -> bool:
    """
    Install GNOME extension from bundled files or download from GitHub.

    Tries to install from bundled files first (AppImage or dev environment),
    falls back to downloading from GitHub releases if bundled files not found.

    Args:
        progress_callback: Optional function(message, percent) for progress updates

    Returns:
        bool: True if successful
    """
    if not is_extension_available():
        if progress_callback:
            progress_callback("GNOME desktop not detected", 0)
        return False

    # First, try to install from bundled files
    bundled_path = get_bundled_extension_path()
    if bundled_path:
        if progress_callback:
            progress_callback("Installing bundled extension...", 50)

        if install_extension_from_local(bundled_path):
            if progress_callback:
                progress_callback("Extension installed successfully!", 100)

            # Enable the extension
            if enable_extension():
                print("Extension enabled - will be active after logout/login")
            else:
                print("Failed to auto-enable extension - you can enable it manually from Extensions app")

            return True
        else:
            print("Failed to install bundled extension, trying download...")

    # Fall back to downloading from GitHub
    return download_and_install_extension(progress_callback)


def download_and_install_extension(progress_callback: Optional[Callable] = None) -> bool:
    """
    Download and install GNOME extension from GitHub release.

    This is the fallback when bundled extension is not available.

    Args:
        progress_callback: Optional function(message, percent) for progress updates

    Returns:
        bool: True if successful
    """
    import tempfile
    import zipfile

    if not is_extension_available():
        if progress_callback:
            progress_callback("GNOME desktop not detected", 0)
        return False

    try:
        # Create temp directory
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, 'extension.zip')

            if progress_callback:
                progress_callback("Downloading extension...", 10)

            # Download hook
            def download_progress_hook(downloaded, total_size):
                if progress_callback and total_size > 0:
                    percent = min(90, 10 + int((downloaded / total_size) * 70))
                    progress_callback(f"Downloading... {int((downloaded / total_size) * 100)}%", percent)

            # Download extension zip. The timeout keeps a stalled connection
            # from hanging the install dialog forever. The sha256 (when the
            # release publishes it) verifies the JS bundle before install.
            from .download_utils import download_file
            try:
                expected_sha256 = _fetch_extension_sha256()
            except ChecksumUnavailableError as e:
                # The release publishes checksums but this zip's could not be
                # established. Installing unverified JavaScript into the user's
                # GNOME Shell on the strength of "the check didn't work" is
                # exactly what the check exists to prevent.
                logger.error(f"Refusing to install an unverified extension: {e}")
                if progress_callback:
                    progress_callback("Could not verify the extension download", 0)
                return False
            if not download_file(
                EXTENSION_DOWNLOAD_URL,
                zip_path,
                timeout=60,
                progress_hook=download_progress_hook,
                expected_sha256=expected_sha256,
            ):
                if progress_callback:
                    progress_callback("Extension download failed or was corrupted", 0)
                return False

            if progress_callback:
                progress_callback("Extracting extension...", 85)

            # Extract zip
            extract_dir = os.path.join(temp_dir, 'extracted')
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)

            # Find extension directory in extracted files
            extension_source = os.path.join(extract_dir, EXTENSION_UUID)
            if not os.path.exists(extension_source):
                # Maybe it's directly in extract_dir
                extension_source = extract_dir

            if progress_callback:
                progress_callback("Installing extension...", 90)

            # Install
            if not install_extension_from_local(extension_source):
                return False

            if progress_callback:
                progress_callback("Extension installed successfully!", 100)

            # Enable the extension after installation
            # This adds it to org.gnome.shell enabled-extensions so it persists across logout/login
            if progress_callback:
                progress_callback("Enabling extension...", 95)

            if enable_extension():
                print("Extension enabled - will be active after logout/login")
            else:
                print("Failed to auto-enable extension - you can enable it manually from Extensions app")

            return True

    except Exception as e:
        if progress_callback:
            progress_callback(f"Installation failed: {e}", 0)
        print(f"Failed to download/install extension: {e}")
        return False


def enable_extension() -> bool:
    """
    Enable the TuxTalk GNOME extension

    Returns:
        bool: True if successful
    """
    return desktop_detect.enable_extension(EXTENSION_UUID)


def uninstall_extension() -> bool:
    """
    Uninstall the TuxTalk GNOME extension

    Returns:
        bool: True if successful
    """
    try:
        ext_dir = desktop_detect.get_extension_dir()
        target_dir = os.path.join(ext_dir, EXTENSION_UUID)

        if os.path.exists(target_dir):
            # First, disable the extension (removes from gsettings)
            try:
                subprocess.run(
                    ['gnome-extensions', 'disable', EXTENSION_UUID],
                    capture_output=True,
                    timeout=10
                )
            except Exception:
                pass  # Ignore errors - extension might not be recognized

        # Disabling puts it on disabled-extensions, and a failed disable
        # (a shell that never loaded it) leaves it on enabled-extensions:
        # an uninstall leaves it on neither. Also when the folder is
        # already gone, so a folder deleted by hand leaves no entry behind.
        desktop_detect.forget_extension(
            EXTENSION_UUID, ("disabled-extensions", "enabled-extensions"))

        if os.path.exists(target_dir):
            # Remove the extension files
            shutil.rmtree(target_dir)
            print("Extension uninstalled")
            return True
        else:
            print("Extension not installed")
            return False

    except Exception as e:
        print(f"Failed to uninstall extension: {e}")
        return False


if __name__ == '__main__':
    # Print the extension's status, for checking by hand.
    print("Testing extension helper...")
    status = get_extension_status()

    print(f"\nGNOME Desktop: {status['available']}")
    print(f"GNOME Version: {status['gnome_version']}")
    print(f"Wayland: {status['wayland']}")
    print(f"Extension Installed: {status['installed']}")
    print(f"Extension Enabled: {status['enabled']}")
