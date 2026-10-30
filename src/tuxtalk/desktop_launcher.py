"""
TuxTalk's own menu launcher and icon, for a self-managed AppImage: the icon in
the user's icon folders, the Applications-menu entry with its right-click
shortcuts, and the hidden entry that gives first-run windows TuxTalk's icon.
Packages ship their own launcher and icon.
"""

import os
import threading
from pathlib import Path
from typing import Optional, Tuple

from . import menu_cache, update_checker
from .autostart import desktop_exec_program
from .logger import setup_logger

logger = setup_logger(__name__)

# The Applications-menu entry first run writes (a hidden one before it).
DESKTOP_FILE_PATH = os.path.expanduser("~/.local/share/applications/tuxtalk.desktop")


def _app_icon_path() -> str:
    """Where install_app_icon puts the icon its name stands for."""
    return os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps/tuxtalk.png")


def installed_app_icon() -> Optional[str]:
    """The icon name ("tuxtalk") once install_app_icon has put it in place,
    else None. Only looks: installing refreshes icon caches, which can take
    seconds, and the caller may be a window's thread."""
    return "tuxtalk" if os.path.exists(_app_icon_path()) else None


def _icon_source() -> Optional[str]:
    """The PNG to install: the running AppImage's own, else the project's
    (running from the source); None when there is neither."""
    candidates = []
    appdir = os.environ.get("APPDIR", "") if os.environ.get("APPIMAGE") else ""
    if appdir:
        candidates += [os.path.join(appdir, "io.github.ronb1964.TuxTalk.png"),
                       os.path.join(appdir, "tuxtalk.png"),
                       os.path.join(appdir, "usr", "share", "icons", "hicolor", "256x256",
                                    "apps", "tuxtalk.png")]
    project_root = Path(__file__).parent.parent.parent
    candidates.append(str(project_root / "io.github.ronb1964.TuxTalk.png"))
    return next((path for path in candidates if os.path.exists(path)), None)


def _refresh_icon_caches(icons_base: str) -> None:
    """Tell GTK's and the XDG icon caches about the new icon. Neither is
    needed everywhere, so a missing tool or a failure is only noted."""
    import subprocess
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


def install_app_icon() -> Optional[str]:
    """Put TuxTalk's icon in the user's icon folders, for the launcher to
    name: the icon name ("tuxtalk"), or None when it couldn't be installed.
    Several sizes from one file, since KDE looks for one of its own sizes
    and may not scale another."""
    import shutil
    if os.path.exists(_app_icon_path()):
        return "tuxtalk"
    source = _icon_source()
    if not source:
        logger.warning("Could not find TuxTalk icon to install")
        return None
    icons_base = os.path.expanduser("~/.local/share/icons/hicolor")
    sizes = ("256x256", "128x128", "64x64", "48x48", "32x32")
    try:
        for size in sizes:
            icon_dir = os.path.join(icons_base, size, "apps")
            os.makedirs(icon_dir, exist_ok=True)
            shutil.copy2(source, os.path.join(icon_dir, "tuxtalk.png"))
    except OSError as e:
        logger.warning(f"Could not install TuxTalk icon: {e}")
        return None
    logger.info(f"Installed TuxTalk icon from {source} to {icons_base} ({len(sizes)} sizes)")
    _refresh_icon_caches(icons_base)
    return "tuxtalk"


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
    """Write the Applications-menu launcher for the AppImage installed in
    ~/AppImages, or for *appimage_exec_path*: the running AppImage, when the
    copy there failed. (success, message for the user)."""
    try:
        os.makedirs(os.path.dirname(DESKTOP_FILE_PATH), exist_ok=True)
        icon_name = install_app_icon() or "audio-input-microphone"
        version = update_checker.get_current_version()
        appimage_exec_path = appimage_exec_path or update_checker.APPIMAGE_PATH
        logger.info(f"Desktop launcher Exec path: {appimage_exec_path}")
        # Quoted where it must be: a download named "TuxTalk (1).AppImage"
        # was cut at the space, and the menu entry opened nothing.
        program = desktop_exec_program(appimage_exec_path)
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
        with open(DESKTOP_FILE_PATH, "w") as f:
            f.write(desktop_content)
        os.chmod(DESKTOP_FILE_PATH, 0o755)
        # The menu cache (KDE's above all) is refreshed in the background, as
        # add_launcher_actions does: it takes seconds, and Setup Complete waits.
        threading.Thread(target=menu_cache.refresh_menu_cache, daemon=True).start()
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
        if update_checker.get_install_type(pkg_query=lambda: "package") != "appimage":
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
        threading.Thread(target=menu_cache.refresh_menu_cache, daemon=True).start()
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
        if update_checker.get_install_type(pkg_query=lambda: "package") != "appimage":
            return False        # other installs ship their own launcher
        if os.path.lexists(DESKTOP_FILE_PATH):
            return False
        icon_name = install_app_icon() or "audio-input-microphone"
        os.makedirs(os.path.dirname(DESKTOP_FILE_PATH), exist_ok=True)
        # Exec is required for a valid entry; the running AppImage exists now.
        exec_path = desktop_exec_program(update_checker.get_running_appimage_path()
                                         or update_checker.APPIMAGE_PATH)
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
        threading.Thread(target=menu_cache.refresh_menu_cache, daemon=True).start()
        logger.info("Hidden desktop entry written so setup windows show TuxTalk's icon")
        return True
    except Exception as e:
        logger.warning(f"Could not write the hidden desktop entry: {e}")
        return False
