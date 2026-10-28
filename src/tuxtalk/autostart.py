"""Launch-at-login (XDG autostart) management, shared by Preferences and onboarding.

Both the Preferences "Launch at login" toggle and first-run onboarding need to
write the same autostart .desktop file with the same launch command. Keeping the
logic here means there is exactly one definition of how TuxTalk relaunches
itself — the command that a fresh install writes and the command Preferences
writes can never drift apart.

The launch command specifically must resolve to the env-setting wrapper
(`/usr/bin/tuxtalk` → AppRun), not a bare `python3 -m tuxtalk.tray`: the bundled
interpreter has no PYTHONPATH/LD_LIBRARY_PATH/GI_TYPELIB_PATH, so a raw-Python
autostart line dies silently at login with ModuleNotFoundError.
"""

import os
import sys
import glob
import subprocess

from .logger import setup_logger

logger = setup_logger(__name__)


def _project_root() -> str:
    """Repo root, for dev-mode paths. autostart.py is src/tuxtalk/autostart.py."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Where setup installs the AppImage; the Applications-menu launcher points here
# too (update_checker.create_desktop_launcher).
INSTALLED_APPIMAGE = os.path.expanduser("~/AppImages/TuxTalk.AppImage")

# How long the Flatpak waits for the Background portal's answer. Long enough
# for the user to read and answer the desktop's question.
_PORTAL_TIMEOUT_S = 60


# Characters the desktop-entry spec says an Exec argument must be quoted for.
_EXEC_RESERVED = set(' \t\n"\'\\><~|&;$*?#()`')


def desktop_exec_program(path: str) -> str:
    """*path* as the program of an Exec= line. GLib drops an entry whose
    program it can't find, so a path with a space must be quoted; inside the
    quotes ", `, $ and \\ are backslash-escaped, then every backslash is
    escaped again as the key file's string rule requires, which also keeps a
    newline from ending the line. % is doubled because it starts a field code."""
    path = path.replace("%", "%%")
    if not any(c in _EXEC_RESERVED for c in path):
        return path
    quoted = "".join("\\" + c if c in '"`$\\' else c for c in path)
    return '"' + quoted.replace("\\", "\\\\").replace("\n", "\\n") + '"'


def get_launch_command(prefer_installed_appimage: bool = False) -> str:
    """Return the most portable command to launch the tray.

    Order: dev script → AppImage path → packaged launcher (absolute) → PATH
    lookup → bare interpreter (last resort). See the module docstring for why the
    packaged launcher must win over a bare-Python invocation.

    From an AppImage, the installed copy (~/AppImages/TuxTalk.AppImage) wins
    over the file actually running, which on first run is usually the one in
    ~/Downloads, so cleaning up Downloads can't silently stop TuxTalk starting
    at login. Setup copies the AppImage in
    the background, so it passes *prefer_installed_appimage* rather than
    relying on the copy having finished.

    A path comes back quoted for the Exec line (desktop_exec_program): a
    home folder with a space would otherwise start nothing at login.
    """
    from . import config
    if config.DEV_MODE:
        root = _project_root()
        run_dev = os.path.join(root, 'run-dev.sh')
        if os.path.isfile(run_dev) and os.access(run_dev, os.X_OK):
            return desktop_exec_program(run_dev)
        return (f'/bin/bash -c "cd {root} && DEV_MODE=1 '
                f'PYTHONPATH=./src:/usr/lib64/python3.13/site-packages:'
                f'/usr/lib/python3.13/site-packages {sys.executable} -m tuxtalk.tray"')

    # Running from an AppImage: relaunch the AppImage itself (defaults to tray).
    appimage_path = os.environ.get('APPIMAGE')
    if appimage_path and os.path.isfile(appimage_path) and os.access(appimage_path, os.X_OK):
        if prefer_installed_appimage or os.path.isfile(INSTALLED_APPIMAGE):
            return desktop_exec_program(INSTALLED_APPIMAGE)
        return desktop_exec_program(appimage_path)

    # Inside an AppImage mount but APPIMAGE unset: find the AppImage on disk.
    if '/tmp/.mount_' in sys.executable or 'squashfs-root' in sys.executable:
        for pattern in (os.path.expanduser('~/AppImages/TuxTalk-*.AppImage'),
                        os.path.expanduser('~/Downloads/TuxTalk-*.AppImage'),
                        os.path.expanduser('~/AppImages/TalkType-*.AppImage'),
                        os.path.expanduser('~/Downloads/TalkType-*.AppImage')):
            matches = glob.glob(pattern)
            if matches:
                latest = max(matches, key=os.path.getmtime)
                if os.access(latest, os.X_OK):
                    return desktop_exec_program(latest)

    # Installed .deb/.rpm: prefer the KNOWN ABSOLUTE launcher, not `which`
    # (a stray ~/.local/bin/tuxtalk must never be written into autostart).
    for launcher in ("/usr/bin/tuxtalk", "/usr/bin/talktype", "/usr/bin/dictate-tray"):
        if os.path.isfile(launcher) and os.access(launcher, os.X_OK):
            return launcher

    # Non-standard packaged layout: PATH lookup, still preferring the wrapper.
    for cmd in ("tuxtalk", "talktype", "dictate-tray"):
        try:
            result = subprocess.run(['which', cmd], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                path = result.stdout.strip()
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    return desktop_exec_program(path)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Last resort — may lack the bundled environment.
    return f'{sys.executable} -m tuxtalk.tray'


def get_icon_path() -> str:
    """Absolute path to the TuxTalk icon, or a system icon name as fallback."""
    root = _project_root()
    candidates = [
        os.path.join(root, 'icons', 'OFFICIAL_ICON_DO_NOT_CHANGE.svg'),
        os.path.join(os.path.dirname(sys.executable), '..', 'io.github.ronb1964.TuxTalk.svg'),
        os.path.join(root, 'AppDir', 'io.github.ronb1964.TuxTalk.svg'),
        '/usr/share/icons/hicolor/scalable/apps/io.github.ronb1964.TuxTalk.svg',
        '/usr/local/share/icons/hicolor/scalable/apps/io.github.ronb1964.TuxTalk.svg',
    ]
    for path in candidates:
        if os.path.isfile(path) and not _in_running_appimage(os.path.abspath(path)):
            return os.path.abspath(path)
    # The user's icon folder instead: first run puts it there.
    from .update_checker import installed_app_icon
    return installed_app_icon() or 'audio-input-microphone'


def _in_running_appimage(path) -> bool:
    """Whether *path* is one of a running AppImage's own files, mounted or
    extracted (extract-and-run): gone once it exits, so an autostart entry
    naming it has no icon at login."""
    if "/.mount_" in path:
        return True
    appdir = os.environ.get("APPDIR", "") if os.environ.get("APPIMAGE") else ""
    return bool(appdir) and path.startswith(appdir.rstrip("/") + "/")


# The description KDE's Autostart page (and other desktops) shows for the entry.
# Same wording as the store listing's summary. _OLD_COMMENTS are earlier
# descriptions that refresh_autostart_comment() replaces.
AUTOSTART_COMMENT = "Hold a key, talk, and your words are typed for you"
AUTOSTART_COMMENT_DEV = AUTOSTART_COMMENT + " (development version)"
_OLD_COMMENTS = ("AI-powered dictation for Wayland using Faster-Whisper",
                 "AI-powered dictation - Development Version")


def _autostart_desktop_path() -> str:
    """The autostart .desktop file for this build (dev or installed)."""
    from . import config
    autostart_dir = os.path.expanduser("~/.config/autostart")
    filename = "tuxtalk-dev.desktop" if config.DEV_MODE else "tuxtalk.desktop"
    return os.path.join(autostart_dir, filename)


def _enabled_entry():
    """The start-at-login entry's lines when it's in place and on, else None.
    Turning it off leaves a Hidden=true stub (set_autostart)."""
    try:
        with open(_autostart_desktop_path()) as f:
            lines = [line.strip() for line in f]
    except OSError:
        return None
    if "Hidden=true" in lines or "X-GNOME-Autostart-enabled=false" in lines:
        return None
    return lines


def enabled_entry_exists() -> bool:
    """Whether a start-at-login entry is in place and on, whatever the settings say."""
    return _enabled_entry() is not None


def _set_autostart_flatpak(enable: bool) -> bool:
    """Enable/disable launch-at-login on the Flatpak via the Background portal.

    A sandboxed app CANNOT make itself autostart by writing ~/.config/autostart:
    inside the sandbox that path is the app's PRIVATE config, which the host
    desktop never reads. RequestBackground(autostart=…) is the sanctioned
    path: the portal writes a HOST autostart entry whose Exec is
    `flatpak run … talktype-launcher` — the only command that launches with the
    bundled environment. The desktop may prompt the user the first time.

    Returns True if the portal request completed. Never raises.
    """
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import GLib
        from . import portal_common as pc

        bus = pc.session_bus()
        loop = GLib.MainLoop()
        st = {"ok": False}

        def builder(token):
            opts = {
                "handle_token": GLib.Variant("s", token),
                "reason": GLib.Variant(
                    "s", "Start TuxTalk automatically when you log in"),
                "autostart": GLib.Variant("b", enable),
                "background": GLib.Variant("b", False),
                # Command to run inside the sandbox at login; the portal wraps it
                # as `flatpak run --command=talktype-launcher <app-id>`.
                "commandline": GLib.Variant("as", ["talktype-launcher"]),
            }
            return GLib.Variant("(sa{sv})", ("", opts))  # parent_window="", options

        def on_response(code, results):
            st["ok"] = (code == 0)
            loop.quit()

        def give_up():
            logger.warning("Background portal did not answer within "
                           f"{_PORTAL_TIMEOUT_S} s")
            st["timed_out"] = True
            loop.quit()
            return False

        unsubscribe = pc.call_portal(bus, "org.freedesktop.portal.Background",
                                     "RequestBackground", builder, on_response,
                                     on_error=lambda e: loop.quit())
        # A portal that accepts the call but never answers must not leave
        # Preferences' Apply waiting forever.
        timeout_id = GLib.timeout_add_seconds(_PORTAL_TIMEOUT_S, give_up)
        loop.run()
        if not st.get("timed_out"):
            GLib.source_remove(timeout_id)
        unsubscribe()
        logger.info("Flatpak autostart %s via Background portal (ok=%s)",
                    "enabled" if enable else "disabled", st["ok"])
        return st["ok"]
    except Exception as e:
        logger.error(f"Flatpak autostart via Background portal failed: {e}")
        return False


def set_autostart(enable: bool, prefer_installed_appimage: bool = False,
                  command: str | None = None) -> bool:
    """Create or disable the autostart entry. Returns True on success.
    *command* is the Exec line's command when the caller knows it (a
    running AppImage), in place of get_launch_command's choice.

    On the Flatpak this goes through the Background portal (the sandbox can't
    write the host autostart file). Off Flatpak it writes the XDG autostart
    .desktop directly; disabling writes a `Hidden=true` stub rather than deleting
    the file — more reliable across desktops (KDE caches entries), and it
    overrides any existing entry.
    """
    if os.environ.get("FLATPAK_ID"):
        return _set_autostart_flatpak(enable)

    from . import config
    desktop_file = _autostart_desktop_path()
    autostart_dir = os.path.dirname(desktop_file)

    try:
        os.makedirs(autostart_dir, exist_ok=True)
    except Exception as e:
        logger.error(f"Could not create autostart dir: {e}")
        return False

    if enable:
        exec_cmd = command or get_launch_command(prefer_installed_appimage)
        icon_path = get_icon_path()
        app_name = "TuxTalk (Dev)" if config.DEV_MODE else "TuxTalk"
        comment = AUTOSTART_COMMENT_DEV if config.DEV_MODE else AUTOSTART_COMMENT
        path_line = f"Path={_project_root()}\n" if config.DEV_MODE else ""
        content = f"""[Desktop Entry]
Type=Application
Name={app_name}
GenericName=Voice Dictation
Comment={comment}
Exec={exec_cmd}
Icon={icon_path}
Terminal=false
Categories=Utility;
Keywords=dictation;voice;speech;whisper;ai;transcription;
StartupNotify=true
StartupWMClass=TuxTalk
X-GNOME-Autostart-enabled=true
{path_line}"""
        try:
            with open(desktop_file, "w") as f:
                f.write(content)
            logger.info(f"Autostart enabled: {desktop_file} (Exec={exec_cmd})")
            return True
        except Exception as e:
            logger.error(f"Failed to write autostart file: {e}")
            return False

    # Disable: Hidden=true stub.
    try:
        with open(desktop_file, "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=TuxTalk\nHidden=true\n")
        logger.info(f"Autostart disabled: {desktop_file}")
        return True
    except Exception as e:
        logger.error(f"Failed to disable autostart file: {e}")
        return False


def refresh_autostart_comment() -> bool:
    """Swap an old description in an existing launch-at-login entry for the
    current one.

    Entries are only written when launch at login is switched on, so without
    this every existing user would keep the stale "Faster-Whisper" text on
    KDE's Autostart page for good. Only the Comment= line changes, and only
    when it holds one of the old texts: Exec and everything else stay exactly
    as they were. Flatpak entries come from the portal and are left alone.
    Returns True if the entry was rewritten.
    """
    if os.environ.get("FLATPAK_ID"):
        return False
    from . import config
    path = _autostart_desktop_path()
    try:
        with open(path) as f:
            lines = f.read().splitlines(keepends=True)
    except OSError:
        return False
    new = AUTOSTART_COMMENT_DEV if config.DEV_MODE else AUTOSTART_COMMENT
    changed = False
    for i, line in enumerate(lines):
        if line.rstrip("\n") in (f"Comment={old}" for old in _OLD_COMMENTS):
            lines[i] = f"Comment={new}\n"
            changed = True
    if not changed:
        return False
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.writelines(lines)
        os.replace(tmp, path)
    except OSError as e:
        logger.debug(f"Could not refresh the login entry's description: {e}")
        return False
    logger.info("Updated the launch-at-login description")
    return True


def _exec_program(exec_cmd):
    """(the program an Exec= value runs, whether the desktop can run it as
    written), reading back what desktop_exec_program writes. An AppImage
    path with a space that isn't quoted (written before 0.19) can't be run,
    whether or not the file is still there: the desktop looks for the part
    before the space, which is no program ("/home/Jo"). A hand-made
    "/usr/bin/env A=1 .../TuxTalk.AppImage" starts with one, and runs."""
    whole = exec_cmd.strip()
    if (" " in whole and not whole.startswith('"') and whole.lower().endswith(".appimage")
            and not os.path.isfile(whole.split()[0])):
        return whole, False
    # The key file's escaping first (\\ is one backslash), then the
    # desktop-entry quoting: inside the quotes a backslash takes the next
    # character as it is.
    text = whole.replace("\\\\", "\\").replace("%%", "%")
    if not text.startswith('"'):
        return (text.split()[0] if text.split() else ""), True
    program, i = [], 1
    while i < len(text) and text[i] != '"':
        if text[i] == "\\" and i + 1 < len(text):
            i += 1
        program.append(text[i])
        i += 1
    return "".join(program), True


def repair_stale_appimage_autostart() -> bool:
    """Point an existing launch-at-login entry at the installed AppImage.

    Entries written by older setups launch whichever AppImage the user ran
    setup from, usually the one in ~/Downloads: an old version, one deleted
    when Downloads is cleaned, or (on Ubuntu 26.04) one that needs libfuse2
    and never starts. TuxTalk then silently stops starting at login.

    Only the AppImage repairs, only when the installed copy exists, and only
    an enabled entry that launches some other TuxTalk AppImage. Disabled
    entries, the dev version, .deb/.rpm and Flatpak installs are never
    touched. Returns True if the entry was rewritten.
    """
    if os.environ.get("FLATPAK_ID") or not os.environ.get("APPIMAGE"):
        return False
    from . import config
    if config.DEV_MODE or not os.path.isfile(INSTALLED_APPIMAGE):
        return False
    lines = _enabled_entry()
    if lines is None:
        return False
    exec_cmd = next((line[len("Exec="):] for line in lines if line.startswith("Exec=")), "")
    target, runnable = _exec_program(exec_cmd)
    name = os.path.basename(target).lower()
    if not (name.endswith(".appimage") and ("tuxtalk" in name or "talktype" in name)):
        return False
    if runnable and os.path.realpath(target) == os.path.realpath(INSTALLED_APPIMAGE):
        return False
    if set_autostart(True, prefer_installed_appimage=True):
        logger.info(f"Repaired launch at login: it pointed at {target}, "
                    f"now {INSTALLED_APPIMAGE}")
        return True
    return False

