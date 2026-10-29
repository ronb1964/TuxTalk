"""Telling the desktop's app menu that TuxTalk's launcher changed.

Most menus watch ~/.local/share/applications themselves. KDE's reads a cache
that only kbuildsycoca rebuilds, so without this a launcher written or
removed by TuxTalk showed up (or lingered) until the next login."""
import os
import subprocess

from .logger import setup_logger

logger = setup_logger(__name__)


def refresh_menu_cache():
    """Rebuild the app menus' caches: update-desktop-database's, then KDE's
    (kbuildsycoca6, or kbuildsycoca5 on Plasma 5). It takes seconds on KDE,
    so callers run it off GTK's thread. A tool that isn't installed is
    skipped."""
    _run(["update-desktop-database", "-q", os.path.expanduser("~/.local/share/applications")])
    for tool in ("kbuildsycoca6", "kbuildsycoca5"):
        if _run([tool]) is not None:
            return


def _run(argv):
    """Run *argv* quietly: its exit code, or None when it isn't installed."""
    try:
        code = subprocess.run(argv, capture_output=True, timeout=30).returncode
    except FileNotFoundError:
        return None
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(f"Could not refresh the menu cache with {argv[0]}: {e}")
        return -1
    logger.info(f"Menu cache refreshed ({argv[0]}, exit code {code})")
    return code
