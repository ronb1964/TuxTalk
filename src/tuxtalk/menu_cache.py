"""Telling the desktop's app menu and icon caches that TuxTalk's launcher
or icon changed.

Most menus watch ~/.local/share/applications themselves. KDE's reads a cache
that only kbuildsycoca rebuilds, so without this a launcher written or
removed by TuxTalk showed up (or lingered) until the next login."""
import os
import subprocess

from .logger import setup_logger

logger = setup_logger(__name__)


def refresh_menu_cache():
    """Rebuild the app menus' caches: update-desktop-database's, where there
    is one to keep current, then KDE's (kbuildsycoca6, or kbuildsycoca5 on
    Plasma 5), until one works; a quick rebuild that fails is tried once more
    in full (--noincremental). It takes seconds on KDE, so callers run it off
    GTK's thread. A tool that isn't installed is skipped."""
    apps = os.path.expanduser("~/.local/share/applications")
    # Only one already there: a new mimeinfo.cache would then go stale.
    if os.path.exists(os.path.join(apps, "mimeinfo.cache")):
        _run(["update-desktop-database", "-q", apps])
    for argv in (["kbuildsycoca6"], ["kbuildsycoca5"],
                 ["kbuildsycoca6", "--noincremental"], ["kbuildsycoca5", "--noincremental"]):
        if _run(argv) == 0:
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
    if code == 0:
        logger.info(f"Menu cache refreshed ({' '.join(argv[:2])})")
    else:
        logger.info(f"{' '.join(argv[:2])} didn't refresh the menu cache (exit code {code})")
    return code


def refresh_icon_caches(icons_base: str) -> None:
    """Tell GTK's and the XDG icon caches that TuxTalk's icon under
    *icons_base* (a hicolor folder) came or went. Neither is needed
    everywhere, so a missing tool or a failure is only noted."""
    for argv in (["gtk-update-icon-cache", "-f", "-t", icons_base],
                 ["xdg-icon-resource", "forceupdate", "--mode", "user"]):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                                    timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            logger.info(f"{argv[0]} didn't run (not needed everywhere): {e}")
            continue
        if result.returncode:
            logger.info(f"{argv[0]} failed (exit {result.returncode}, not needed everywhere): "
                        f"{result.stderr.strip()[:200]}")
