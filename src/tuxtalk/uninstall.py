"""Uninstall TuxTalk: Preferences -> Updates & Backup -> Uninstall TuxTalk...,
or `tuxtalk --uninstall`.

Removing the package or deleting the AppImage takes away only the program.
Running it sets up much more: the hotkey helper service and udev rules (as
root), the input group on older setups, a launcher, an icon and start at
login, the GNOME extension, KDE shortcuts, and gigabytes of downloads
(speech models, the Vulkan and AI engines, CUDA libraries). A Zorin OS user
removed the .deb in October 2026 and found all of it still there.

This takes all of it away, with one password prompt. TuxTalk's own
downloads always go; settings, Your Words, snippets and stats only when
asked, so a reinstall can pick up where it left off, and the speech models
in Hugging Face's shared cache only when asked too, since other apps may
use the same ones. Backup files the user saved elsewhere are theirs and
stay.

It runs in the tray's process when TuxTalk is running: an AppImage's files
vanish when its first process (the tray) exits, and this needs them to the
end. Removing the .deb or .rpm deletes this very code mid-run, so every
module it uses afterwards is imported here, up front.
"""
import grp
import os
import pwd
import re
import shutil
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NamedTuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import (ai_download, config, cosmic_hotkeys, custom_words,  # noqa: E402
               desktop_detect, extension_helper, file_transcribe, gnome_extensions,
               gnome_keybindings, installation, kwin_hotkeys, menu_cache, model_helper,
               parakeet_engine, rename_migration, secure_input, service_launcher,
               settings_backup, single_instance, uinput_helper, whisper_vulkan,
               ydotoold_leftovers)
from .logger import LOG_FILE_NAME, setup_logger, stop_file_logging  # noqa: E402
from .ui_style import message_dialog, show_message  # noqa: E402

logger = setup_logger(__name__)

# The files in the data folder that are the user's own: kept unless they
# ask for them to go.
PERSONAL_DATA = ("stats.json", "stats.json.bak", file_transcribe.RECENT_FILE,
                 file_transcribe.OLD_LAST_FILE)
# The rest of what TuxTalk puts there, downloaded or rebuilt by itself
# (tests/test_uninstall.py makes every name in the code pick one of the two).
# .show_welcome_on_restart is only in an old TalkType's folder.
DOWNLOADED_DATA = ("cuda", "ai-engine", "parakeet-ultra", "whisper-vulkan", "kwin", "updates",
                   ".first_run_done", ".hotkey_test_mode", "remote_desktop_token",
                   gnome_keybindings.BACKUP_NAME, ".show_welcome_on_restart")
# The folders in it whose programs TuxTalk runs, stopped before deleting.
ENGINE_FOLDERS = ("whisper-vulkan", "ai-engine", "parakeet-ultra", "cuda")
# What TuxTalk keeps in its settings folder (tests/test_uninstall.py again).
CONFIG_NAMES = ("config.toml", "custom_commands.toml", custom_words.FILE_NAME, "backups",
                "kde-hotkeys.json", "explained.json", ".launchers-renamed",
                rename_migration.NOTICE_FILE, LOG_FILE_NAME, "talktype.log")
# Of those names, the ones TuxTalk makes as folders; it writes the rest as
# files. A folder of the user's named config.toml isn't TuxTalk's. Each of
# its own folders goes whole, downloads and all; one that is a link (an
# engine moved to a bigger disk) loses only the link, and the folder it
# leads to stays, as TuxTalk didn't make it there.
FOLDER_NAMES = ("cuda", "ai-engine", "parakeet-ultra", "whisper-vulkan", "kwin", "updates",
                "backups")


def _copies(*patterns):
    """One regular expression matching any of *patterns* whole."""
    return re.compile("|".join(f"(?:{pattern})" for pattern in patterns))


def _saved(*names):
    """The temp files config.write_text_atomic leaves of *names* when a save
    is killed part-way."""
    return [re.escape(name) + config.ATOMIC_TEMP for name in names]


# The copies TuxTalk's writers leave beside its files, writer by writer, and
# exactly those: config.toml.before-my-holiday is someone else's. Saves keep
# a .bak of the settings, snippets and Your Words; a damaged config.toml is
# kept as .corrupt-<time> (config._recover_damaged_config); the Words tab
# kept custom_words.txt as it was (vocabulary.adopt_fixes); the log rolls
# over to .old.
_CONFIG_COPIES = _copies(
    *_saved("config.toml", "custom_commands.toml", custom_words.FILE_NAME),
    *(re.escape(name) + r"\.bak"
      for name in ("config.toml", "custom_commands.toml", custom_words.FILE_NAME)),
    r"config\.toml\.corrupt-\d{8}-\d{6}",
    re.escape(custom_words.FILE_NAME) + r"\.before-words-tab",
    *(re.escape(name) + r"\.old" for name in (LOG_FILE_NAME, "talktype.log")))
# Stats save through a temp file of their own (stats._write), the
# transcripts through their name and .tmp (file_transcribe).
_PERSONAL_COPIES = _copies(r"\.stats-[a-z0-9_]{8}",
                           re.escape(file_transcribe.RECENT_FILE) + r"\.tmp")
_DOWNLOADED_COPIES = _copies(*_saved("remote_desktop_token", gnome_keybindings.BACKUP_NAME))
# (names, copies) for each kind of file, for _known.
_CONFIG = ((CONFIG_NAMES, _CONFIG_COPIES),)
_PERSONAL = ((PERSONAL_DATA, _PERSONAL_COPIES),)
_DOWNLOADED = ((DOWNLOADED_DATA, _DOWNLOADED_COPIES),)

def _hub_folder(repo):
    """The Hugging Face cache's folder name for *repo*."""
    return f"models--{repo.replace('/', '--')}"


# Each Hugging Face repository TuxTalk downloads from, as the cache names
# its folder. Only these: the cache is shared, and another app's models in
# it are not ours to delete. One folder holds a whole repository, so
# whisper.cpp's may hold another app's models too: hence the question's
# "other apps may use them". The Whisper models are every name a config may
# hold (an older one may say small.en), as faster-whisper downloads them from
# Systran; to it, "large" is large-v3.
HF_REPOS = tuple(sorted({_hub_folder(repo) for repo in (
    *(model_helper.MODEL_REPOS.get(m, f"Systran/faster-whisper-{m}")
      for m in config.VALID_MODELS - {"large"} if not parakeet_engine.is_parakeet(m)),
    parakeet_engine.V3_REPO, whisper_vulkan.MODEL_REPO,
    *whisper_vulkan.MODEL_REPOS.values(), ai_download.MODEL_REPO)}))

# The runtime folder's files that hold dictated text, as glob patterns:
# Recent Dictations, the temp file a save of it killed part-way leaves
# (history.py's mkstemp), and Fix a Word's hand-overs
# (voice_commands._hand_over, a name of its own each). Logging out empties
# the folder anyway; its lock files stay, since a development copy running
# beside this one uses the same names.
RUNTIME_FILES = ("talktype-history.json", ".talktype-history-" + "?" * 8,
                 "tuxtalk-fix-word-*.json")

ICON_SIZES = ("256x256", "128x128", "64x64", "48x48", "32x32")

# The packages, by how TuxTalk was installed.
PACKAGES = {"deb": "tuxtalk", "rpm": "tuxtalk", "aur": "tuxtalk-appimage"}

FEEDBACK_URL = "https://github.com/ronb1964/TuxTalk/discussions"


@dataclass
class Plan:
    """What there is to remove on this computer."""
    home: Path
    install_type: str
    downloads: list = field(default_factory=list)     # always removed
    shared: list = field(default_factory=list)        # in the Hugging Face cache: only when asked
    leftovers: list = field(default_factory=list)     # launchers, autostart, program
    personal: list = field(default_factory=list)      # removed only when asked
    folders: list = field(default_factory=list)       # (folder, _owner): emptied when asked
    kept: list = field(default_factory=list)          # left on purpose (a restore to retry)
    runtime: list = field(default_factory=list)
    hub: Path = None                 # Hugging Face's model cache
    helper: bool = False             # the hotkey helper is installed
    typing_rule: bool = False        # a udev rule lets TuxTalk type (either version)
    input_group: bool = False        # TuxTalk's old setup put the user in it
    ydotoold_unit: Path = None       # the systemd user unit TuxTalk used until 0.18
    extension: bool = False

    @property
    def package(self):
        return PACKAGES.get(self.install_type)

    @property
    def needs_root(self):
        return self.helper or self.typing_rule or self.input_group or self.package is not None


@dataclass
class Result:
    """How the uninstall went, for the closing message."""
    left_group: bool = False         # gpasswd really took the user out
    package_failed: bool = False     # the package manager refused
    not_removed: list = field(default_factory=list)   # what's still there, in words

    @property
    def complete(self):
        return not self.not_removed and not self.package_failed


def hf_hub_cache(home, environ=os.environ):
    """Where Hugging Face keeps its models, by its own rules (huggingface_hub's
    constants.py; HUGGINGFACE_HUB_CACHE is the older name, still honoured).
    Worked out here rather than read from there, so the tests can point it
    at a made-up home."""
    def path(value):
        return Path(os.path.expanduser(os.path.expandvars(value)))
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if environ.get(name):
            return path(environ[name])
    if environ.get("HF_HOME"):
        return path(environ["HF_HOME"]) / "hub"
    cache = Path(environ["XDG_CACHE_HOME"]) if environ.get("XDG_CACHE_HOME") else home / ".cache"
    return cache / "huggingface" / "hub"


def _in_input_group():
    """In the group now, or added and not logged in again since."""
    try:
        group = grp.getgrnam("input")
    except KeyError:
        return False
    return group.gr_gid in os.getgroups() or pwd.getpwuid(os.getuid()).pw_name in group.gr_mem


def _known(entry, kinds):
    """Whether *entry* is TuxTalk's by one of *kinds*, (names, copies) pairs:
    one of the names, made as TuxTalk makes it (a folder for FOLDER_NAMES,
    else a file), or a file matching copies. A link by one of the names is
    TuxTalk's either way; only the link is removed, never what it leads to."""
    name = entry.name
    link = entry.is_symlink()
    folder = entry.is_dir() and not link
    for names, copies in kinds:
        if name in names and (link or folder == (name in FOLDER_NAMES)):
            return True
        if not folder and copies.fullmatch(name):
            return True
    return False


def _owner(*kinds):
    """_known for *kinds*, as a test of one entry."""
    return lambda entry: _known(entry, kinds)


def _contents(folder, owned):
    """What in *folder* is TuxTalk's, by *owned*. Only that: a file of the
    user's own may sit beside TuxTalk's, in its own folder as much as in a
    synced folder or on another disk that the folder is a link to."""
    if not folder.is_dir():
        return []
    return [entry for entry in sorted(folder.iterdir()) if owned(entry)]


def _program_files(home, install_type, appimage):
    """Where the program, its launchers and icons may be, there or not."""
    apps = home / "AppImages"
    # The versioned files too: started at login, TuxTalk runs from the copy
    # setup made, and the one the user downloaded here would stay behind.
    candidates = [apps / "TuxTalk.AppImage", apps / "TalkType.AppImage",
                  *sorted(apps.glob("TuxTalk-*.AppImage")),
                  *sorted(apps.glob("TalkType-*.AppImage")),
                  apps / ".tuxtalk_just_updated", apps / ".talktype_just_updated",
                  *sorted(apps.glob(".TuxTalk.AppImage.*.part"))]
    if appimage and install_type == "appimage":
        candidates.insert(0, Path(appimage))
    for name in ("tuxtalk.desktop", "talktype.desktop"):
        candidates += [home / ".config" / "autostart" / name,
                       home / ".local" / "share" / "applications" / name]
    icons = home / ".local" / "share" / "icons" / "hicolor"
    for size in ICON_SIZES:
        apps = icons / size / "apps"
        candidates += [apps / "tuxtalk.png", apps / "talktype.png"]
    candidates.append(icons / "scalable" / "apps" / f"{config.APP_ID}.svg")
    return candidates


def _folders_to_empty(home, config_dir, data_dir):
    """The settings and data folders, by both names, each with the test of
    what's TuxTalk's in it.

    The names from before the 0.15.0 rename are links to the folders here,
    or the real folders behind them when the rename couldn't move them (a
    mount point): then the new name is the link, and the folder is emptied
    once, through it. When the rename found both there, the old one is a
    folder of its own, emptied by the same names.
    """
    folders = []
    for new, old, owned in ((config_dir, home / ".config" / "talktype", _owner(*_CONFIG)),
                            (data_dir, home / ".local" / "share" / "TalkType",
                             _owner(*_PERSONAL, *_DOWNLOADED))):
        behind_link = os.path.realpath(new) if new.is_symlink() else None
        for folder in (new, old):
            if not os.path.lexists(folder) or (folder == old and not old.is_symlink()
                                               and os.path.realpath(old) == behind_link):
                continue
            folders.append((folder, owned))
    return folders


def find(home, install_type, environ=os.environ, appimage=None):
    """The Plan for *home*: only what exists. *appimage* is the running
    AppImage's path, if any."""
    plan = Plan(home=home, install_type=install_type)
    config_dir = home / ".config" / config.CONFIG_DIR
    data_dir = home / ".local" / "share" / config.DATA_DIR

    def exists(path):
        return os.path.lexists(path)

    for entry in _contents(data_dir, _owner(*_PERSONAL, *_DOWNLOADED)):
        (plan.personal if _known(entry, _PERSONAL) else plan.downloads).append(entry)
    plan.hub = hf_hub_cache(home, environ)
    # A .locks that is a link leads somewhere else: deleting through it
    # would reach another folder's files.
    locks = plan.hub / ".locks"
    folders = [plan.hub] + ([] if locks.is_symlink() else [locks])
    plan.shared = [folder / name for name in HF_REPOS for folder in folders
                   if exists(folder / name)]

    for path in _program_files(home, install_type, appimage):
        if exists(path) and path not in plan.leftovers:
            plan.leftovers.append(path)
    plan.folders = _folders_to_empty(home, config_dir, data_dir)

    runtime = Path(single_instance.session_runtime_dir(environ))
    plan.runtime = sorted(path for pattern in RUNTIME_FILES for path in runtime.glob(pattern))

    unit = ydotoold_leftovers.our_ydotoold_unit(home)
    if unit is not None:
        plan.ydotoold_unit = Path(unit)
    plan.extension = exists(Path(gnome_extensions.extension_folder(
        extension_helper.EXTENSION_UUID)))
    plan.helper = any(os.path.exists(p) for p in (secure_input.UNIT_PATH, secure_input.HELPER_PATH))
    plan.typing_rule = any(os.path.exists(p) for p in (secure_input.UINPUT_RULE_PATH,
                                                       uinput_helper.UDEV_RULE_PATH))
    # Only the old setup's own rule says TuxTalk put the user in the group.
    plan.input_group = os.path.exists(uinput_helper.UDEV_RULE_PATH) and _in_input_group()
    return plan


def disk_size(paths):
    """Bytes *paths* take on disk (each file counted once). A link counts as
    nothing: only the link is removed, never what it leads to."""
    seen = {}

    def count(path):
        try:
            st = os.lstat(path)
        except OSError:
            return
        if stat.S_ISREG(st.st_mode):
            seen[(st.st_dev, st.st_ino)] = st.st_blocks * 512

    for top in paths:
        if os.path.islink(top) or not os.path.isdir(top):
            count(top)
            continue
        for root, _dirs, files in os.walk(top):
            for name in files:
                count(os.path.join(root, name))
    return sum(seen.values())


def _package_lines(plan):
    """Shell lines removing TuxTalk's package; on failure they print
    PACKAGE_NOT_REMOVED and the package manager's last lines."""
    name = plan.package
    if plan.install_type == "deb":
        command = f"DEBIAN_FRONTEND=noninteractive apt-get remove -y {name}"
    elif plan.install_type == "rpm":
        command = (f"if command -v dnf >/dev/null; then dnf remove -y {name}; "
                   "elif command -v zypper >/dev/null; then "
                   f"zypper --non-interactive remove {name}; "
                   f"else rpm -e {name}; fi")
    else:
        command = f"pacman -R --noconfirm {name}"
    return (f'if ! out=$( {{ {command}; }} </dev/null 2>&1 ); then\n'
            f'    echo {secure_input.PACKAGE_NOT_REMOVED}\n'
            f'    printf "%s\\n" "$out" | tail -n 3\n'
            f'fi\n')


def root_script(plan, leave_group):
    """What pkexec runs as root: the hotkey helper and typing rules, the
    input group when the user said so, then the package. Every step runs
    even if one before it failed."""
    lines = "set -uo pipefail\n"
    if leave_group:
        lines += secure_input.leave_group_lines()
    lines += secure_input.uninstall_lines()
    if plan.package:
        lines += _package_lines(plan)
    return lines + "exit 0\n"


def _remove(path):
    """Remove a file, a link (never what it points at), or a folder. False
    when it couldn't."""
    try:
        if path.is_symlink() or not path.is_dir():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path)
    except OSError as e:
        logger.warning(f"Could not remove {path}: {e}")
        return False
    return True


def _rmdir(folder):
    """Remove *folder* if it's empty: what's left in it isn't TuxTalk's."""
    try:
        folder.rmdir()
    except OSError:
        pass        # gone already, or something else is in it


def _is_backup_file(entry):
    """Whether *entry*, in a backups folder, is a file settings_backup wrote."""
    return settings_backup.is_ours(entry.name) and (entry.is_symlink() or not entry.is_dir())


def _empty(folder, owned, kept=()):
    """Remove what's TuxTalk's by *owned* in *folder* but what's *kept* (real
    paths), then the folder once empty, or the link and the folder it led
    to. Never the folder with what's left in it. What it couldn't remove."""
    target = folder.resolve() if folder.is_symlink() else None
    try:
        entries = _contents(folder, owned)
    except OSError as e:
        logger.warning(f"Could not look in {folder}: {e}")
        return [folder]
    failed = []
    held = [entry for entry in entries if os.path.realpath(entry) in kept]
    for entry in entries:
        if entry in held:
            continue
        if entry.name == "backups" and entry.is_dir():
            # Through a link too: someone may keep files of their own in a
            # folder by that name, or in the one the link leads to.
            failed += _empty(entry, _is_backup_file, kept)
        elif not _remove(entry):
            failed.append(entry)
    if target is None:
        _rmdir(folder)
        return failed
    if not failed and not held and not _remove(folder):
        failed.append(folder)
    _rmdir(target)
    return failed


def remove_files(plan, delete_personal, delete_shared):
    """Delete the files the plan lists, and return those it couldn't. Only
    files: the desktop's own settings are undone by undo_desktop_changes()
    first."""
    paths = plan.downloads + plan.leftovers + plan.runtime
    if delete_shared:
        paths += plan.shared
    if delete_personal:
        paths += plan.personal
    if plan.ydotoold_unit is not None:
        paths.append(plan.ydotoold_unit)
    kept = {os.path.realpath(path) for path in plan.kept}
    failed = [path for path in paths if os.path.realpath(path) not in kept and not _remove(path)]
    if delete_personal:
        for folder, owned in plan.folders:
            failed += _empty(folder, owned, kept)
    # Folders TuxTalk made and nothing else uses: gone once empty.
    folders = [plan.home / "AppImages", plan.hub / ".locks", plan.hub]
    if plan.hub.parent.name == "huggingface":
        folders.append(plan.hub.parent)
    for folder in folders:
        _rmdir(folder)
    # A download that wouldn't go may have gone with its folder after all,
    # and one that stays may be listed again through the old TalkType link.
    left = {}
    for path in failed:
        if os.path.lexists(path):
            left.setdefault(os.path.realpath(path), path)
    return list(left.values())


def _tuxtalk_pids(engines):
    """This user's other TuxTalk processes: its Python modules (service,
    Preferences, Transcribe a File, an old TalkType) and the engines it
    downloaded into the *engines* folders."""
    me = os.getpid()
    return [int(entry) for entry in os.listdir("/proc")
            if entry.isdigit() and int(entry) != me and _is_tuxtalk(int(entry), engines)]


def _is_tuxtalk(pid, engines):
    """Whether *pid* is one of _tuxtalk_pids' processes (asked again once it's
    held, as its PID could be another program's by then)."""
    try:
        if os.stat(f"/proc/{pid}").st_uid != os.getuid():
            return False
        argv = service_launcher.read_argv(pid) or []
        exe = os.path.realpath(f"/proc/{pid}/exe")
    except OSError:
        return False
    target = service_launcher.interpreter_target(argv)
    module = bool(target) and target[0] == "module" and target[1].startswith(
        ("tuxtalk.", "talktype."))
    return (module or exe.startswith(engines)) and not _is_dev(pid)


def _is_dev(pid):
    """A development copy (DEV_MODE=1) has its own folders: not ours to stop."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            return b"DEV_MODE=1" in f.read().split(b"\0")
    except OSError:
        return False


class TrayParts(NamedTuple):
    """The running tray's part in an uninstall, when it runs inside one."""
    stop: Callable          # refuse every action and start nothing (before the service goes)
    resume: Callable        # the uninstall didn't happen: as before
    shut_down: Callable     # its D-Bus service and KWin script, once the service has gone
    preferences_open: Callable  # its Preferences still open (none told it to close)


def _our_services():
    """The dictation services running now, a development copy's left out."""
    return [pid for pid in service_launcher.find_service_pids() if not _is_dev(pid)]


def _end_service():
    """End the dictation service (it types what it holds first), but never
    a development copy's; True when none of ours is left. Off GTK's thread."""
    pids = _our_services()
    return service_launcher.end_processes(
        pids, service_launcher.QUIT_WAIT_S, still_ours=service_launcher.is_service_pid,
        after_term=service_launcher.release_desktop_hotkeys if pids else None)


def stop_tuxtalk(plan, shut_down=None):
    """Stop everything TuxTalk runs, so nothing writes a file back after
    it's deleted. *shut_down* is the tray's own (TrayParts.shut_down) when
    this runs inside it. The waits run off GTK's thread (_wait_for), so the
    working window keeps drawing. Raises when a dictation service won't stop:
    its files mustn't go from under it."""
    if not _wait_for(_end_service):
        raise RuntimeError("TuxTalk's dictation service wouldn't stop")
    if shut_down is not None:
        shut_down()
    _wait_for(lambda: _stop_processes(plan))


def _stop_processes(plan):
    """stop_tuxtalk's waiting half: every other TuxTalk process and the old
    ydotoold service."""
    ydotoold_leftovers.stop_ydotoold_we_started()
    # Each engine folder, not the whole data folder: that may be a link into
    # a folder where other programs run from.
    data_dir = plan.home / ".local" / "share" / config.DATA_DIR
    engines = tuple(os.path.realpath(data_dir / name) + os.sep for name in ENGINE_FOLDERS)
    service_launcher.end_processes(_tuxtalk_pids(engines), 3,
                                   still_ours=lambda pid: _is_tuxtalk(pid, engines))
    if plan.ydotoold_unit is not None:
        for args in (["disable", "--now", "ydotoold.service"], ["daemon-reload"]):
            subprocess.run(["systemctl", "--user", *args], capture_output=True, timeout=15)


def undo_desktop_changes(plan):
    """Put the desktop back as it was: Alt+F7/Alt+F8 on GNOME if the hotkey
    test was cut short, the GNOME extension, KDE's and COSMIC's shortcut
    entries. Runs after stop_tuxtalk() and before the files go (the
    keybindings' backup is one of them). Returns what's still there, in words."""
    left = []
    if not gnome_keybindings.restore_left_over():
        # Its backup stays (remove_files leaves plan.kept), so a reinstall
        # can still put them back.
        plan.kept.append(plan.home / ".local" / "share" / config.DATA_DIR
                         / gnome_keybindings.BACKUP_NAME)
        left.append("GNOME's Alt+F7 and Alt+F8 shortcuts, which a hotkey test had "
                    "cleared (TuxTalk couldn't put them back)")
    if plan.extension or desktop_detect.is_gnome():
        extension_helper.uninstall_extension()
        # Its result can't tell "removed nothing" from "wasn't there".
        folder = Path(gnome_extensions.extension_folder(extension_helper.EXTENSION_UUID))
        if os.path.lexists(folder):
            left.append(f"the GNOME extension ({_shown(folder, plan.home)})")
    if not kwin_hotkeys.forget_our_components():
        left.append("TuxTalk's entries in KDE's Shortcuts settings")
    if not cosmic_hotkeys.release():
        left.append("TuxTalk's entries in COSMIC's custom shortcuts")
    return left


def _refresh_menus():
    """Let the app menus and the icon cache forget the launcher and its icon;
    quiet when a tool is missing."""
    menu_cache.refresh_icon_caches(str(Path.home() / ".local" / "share" / "icons" / "hicolor"))
    menu_cache.refresh_menu_cache()


# ---------------------------------------------------------------------------
# The dialogs


def _size_text(size):
    """Decimal units, as every download size in TuxTalk is shown."""
    if size >= 1000 ** 3:
        return f"{size / 1000 ** 3:.1f} GB"
    return f"{max(1, round(size / 1000 ** 2))} MB"


def confirm_text(plan, size):
    """The bullet lines of the question: what will go, as found here."""
    program = {
        "appimage": "TuxTalk itself (its AppImage)",
        "deb": "TuxTalk itself (the tuxtalk package)",
        "rpm": "TuxTalk itself (the tuxtalk package)",
        "aur": "TuxTalk itself (the tuxtalk-appimage package)",
    }.get(plan.install_type)
    lines = [program] if program else []
    if plan.helper:
        lines.append("its hotkey helper and the permission to type into other apps")
    elif plan.typing_rule:
        lines.append("the permission it set up to type into other apps")
    if size >= 1000 ** 2:       # not just the small files it keeps there
        lines.append("the engines, libraries and models it downloaded to its own folder "
                     f"({_size_text(size)})")
    found = {path.parent.name for path in plan.leftovers}
    desktop = [what for what, there in (("starting at login", "autostart" in found),
                                        ("its menu entry", "applications" in found),
                                        ("the GNOME extension", plan.extension)) if there]
    if desktop:
        lines.append(_and(desktop))
    return "\n".join(f"•  {line}" for line in lines)


def _and(items):
    """"a", "a and b", "a, b and c"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _shown(path, home):
    """*path* with the home folder as ~, as people know it."""
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def _shared_what(plan):
    """What the shared cache holds of TuxTalk's, in words, and whether that's
    more than one thing."""
    ai = _hub_folder(ai_download.MODEL_REPO)
    names = {path.name for path in plan.shared}
    if names == {ai}:
        return "the AI model", False
    return ("speech models and the AI model" if ai in names else "speech models"), True


def shared_label(plan):
    """The shared-models box."""
    what, several = _shared_what(plan)
    return (f"Also delete {what} in the shared download folder "
            f"(other apps may use {'them' if several else 'it'})")


def shared_text(plan, size):
    """The line under the shared-models box: how big, and where."""
    several = _shared_what(plan)[1]
    return (f"{_size_text(size)} in {_shown(plan.hub, plan.home)}. Otherwise "
            f"{'they stay' if several else 'it stays'} there for other apps, or for TuxTalk "
            "if you install it again.")


def _wait_for(work):
    """Run *work* on a thread while the window keeps drawing; its result,
    or the error it raised. Ctrl+C in `tuxtalk --uninstall` lets the step
    under way finish first: Python joins a pool's threads at exit anyway,
    and a step stopped halfway would leave half an uninstall."""
    loop = GLib.MainLoop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        done = pool.submit(work)
        # Run at once, here, when the work is already finished: then the
        # quit is queued before the loop starts, which still ends it.
        done.add_done_callback(lambda _future: GLib.idle_add(loop.quit))
        loop.run()
    return done.result()


def _note(text):
    """A dim, wrapped line under a tickbox in the question."""
    label = Gtk.Label(xalign=0, label=text)
    label.set_line_wrap(True)
    label.set_max_width_chars(60)
    label.get_style_context().add_class("dim-label")
    return label


def _ask(plan):
    """(delete_personal, delete_shared, leave_group), or None for Cancel."""
    dialog = message_dialog(message_type=Gtk.MessageType.QUESTION,
                            buttons=Gtk.ButtonsType.NONE, text="Uninstall TuxTalk?")
    text = ("This removes TuxTalk and everything it set up on this computer:\n\n"
            + confirm_text(plan, disk_size(plan.downloads)))
    if plan.needs_root:
        text += "\n\nYou'll be asked for your password once."
    dialog.format_secondary_text(text)
    area = dialog.get_message_area()

    personal = Gtk.CheckButton(label="Also delete my settings, Your Words, snippets and stats")
    area.pack_start(personal, False, False, 0)
    area.pack_start(_note("Otherwise they stay, so installing TuxTalk again picks up where "
                          "you left off. Backups you saved stay either way."), False, False, 0)

    shared = None
    if plan.shared:
        shared = Gtk.CheckButton(label=shared_label(plan))
        area.pack_start(shared, False, False, 0)
        area.pack_start(_note(shared_text(plan, disk_size(plan.shared))), False, False, 0)

    group = None
    if plan.input_group:
        group = Gtk.CheckButton(label="Take me out of the 'input' group")
        group.set_active(True)
        area.pack_start(group, False, False, 0)
        area.pack_start(_note("TuxTalk added you to it, which lets programs read the keyboard. "
                              "Untick it if another program you use needs it, a key remapper "
                              "for instance."), False, False, 0)

    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    button = dialog.add_button("Uninstall", Gtk.ResponseType.OK)
    button.get_style_context().add_class("destructive-action")
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)
    area.show_all()
    answer = dialog.run()
    choice = (personal.get_active(), bool(shared and shared.get_active()),
              bool(group and group.get_active()))
    dialog.destroy()
    return choice if answer == Gtk.ResponseType.OK else None


def _working_window():
    """A small "Uninstalling TuxTalk..." window while the work runs."""
    window = Gtk.Window(title="TuxTalk")
    window.set_position(Gtk.WindowPosition.CENTER)
    window.set_resizable(False)
    window.set_deletable(False)
    window.set_keep_above(True)
    box = Gtk.Box(spacing=12)
    for side in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{side}")(20)
    spinner = Gtk.Spinner()
    spinner.start()
    box.pack_start(spinner, False, False, 0)
    box.pack_start(Gtk.Label(label="Uninstalling TuxTalk…"), False, False, 0)
    window.add(box)
    window.show_all()
    return window


def _tell(kind, heading, body, markup=False):
    show_message(None, kind, heading, body, keep_above=True, markup=markup)


# What the root script's NOT_REMOVED lines name, in words.
ROOT_LEFT = {"helper": f"the hotkey helper ({secure_input.SERVICE})",
             "rule": "the typing permission in /etc/udev/rules.d"}


def root_result(report, leave_group, package=False):
    """The Result of the root script, from its report lines (_pkexec).
    *package* is whether it removed a package too."""
    lines = report.splitlines()
    result = Result(package_failed=secure_input.PACKAGE_NOT_REMOVED in lines)
    if secure_input.RAN_TOO_LONG in lines:
        # It ran to the end, but what it said was lost.
        unsure = ["the hotkey helper"]
        if package:
            unsure.append("the program's package")
        if leave_group:
            unsure.append("your place in the 'input' group")
        result.not_removed.append(f"maybe {_and(unsure)} (removing "
                                  f"{'them' if len(unsure) > 1 else 'it'} took too long to "
                                  "confirm)")
        return result
    result.not_removed = [ROOT_LEFT[line.split()[1]] for line in lines
                          if line.startswith(secure_input.NOT_REMOVED + " ")]
    still_in_group = secure_input.STILL_IN_GROUP in lines
    result.left_group = leave_group and not still_in_group
    if leave_group and still_in_group:
        result.not_removed.append("your place in the 'input' group")
    return result


def done_heading(result):
    return "TuxTalk is uninstalled" if result.complete else "TuxTalk couldn't remove everything"


def done_text(plan, delete_personal, delete_shared, result):
    """The closing message's body (Pango markup)."""
    parts = []
    if result.not_removed:
        shown = [GLib.markup_escape_text(item) for item in result.not_removed[:6]]
        if len(result.not_removed) > 6:
            shown.append(f"and {len(result.not_removed) - 6} more")
        parts.append("These are still there:\n" + "\n".join(f"•  {item}" for item in shown))
    if result.package_failed:
        command = {"deb": "sudo apt remove tuxtalk", "rpm": "sudo dnf remove tuxtalk",
                   "aur": "sudo pacman -R tuxtalk-appimage"}[plan.install_type]
        parts.append("The package manager wouldn't remove the program itself (it may be "
                     f"busy with an update). Remove it later with:\n<tt>{command}</tt>")
    elif plan.install_type == "package":
        parts.append("Remove the tuxtalk package with your package manager to finish.")
    logout = []
    if plan.extension:
        logout.append("GNOME shows the extension's menu until then")
    if result.left_group:
        logout.append("leaving the 'input' group takes effect then")
    if logout:
        parts.append("Log out and back in to finish: " + ", and ".join(logout) + ".")
    if not delete_personal and (plan.home / ".config" / config.CONFIG_DIR).exists():
        parts.append("Your settings, Your Words and snippets are kept in "
                     f"<tt>~/.config/{config.CONFIG_DIR}</tt>.")
    if not delete_shared and plan.shared:
        what, several = _shared_what(plan)
        where = GLib.markup_escape_text(_shown(plan.hub, plan.home))
        parts.append(f"{what[0].upper() + what[1:]} in the shared download folder "
                     f"{'are' if several else 'is'} kept in <tt>{where}</tt>.")
    if result.not_removed:
        parts.append(f'Ask in <a href="{FEEDBACK_URL}">Discussions</a> and we\'ll help you '
                     "remove the rest.")
    else:
        parts.append(f'If TuxTalk didn\'t work out for you, <a href="{FEEDBACK_URL}">tell us '
                     "why</a>. It helps.")
    return "\n\n".join(parts)


def _remove_as_root(plan, leave_group):
    """The root script's Result, or None when it didn't run (cancelled,
    failed or timed out; the user has been told)."""
    ok, message = _wait_for(lambda: secure_input._run_as_root(
        root_script(plan, leave_group), "removed (uninstalling TuxTalk)"))
    if ok:
        return root_result(message, leave_group, plan.package is not None)
    if message == secure_input.TIMED_OUT:
        _tell(Gtk.MessageType.ERROR, "TuxTalk couldn't uninstall",
              "Nothing was removed: the password prompt was open too long. Try Uninstall "
              "TuxTalk… again.")
    elif message != "cancelled":
        _tell(Gtk.MessageType.ERROR, "TuxTalk couldn't uninstall",
              f"Nothing was removed.\n\n{message}")
    return None


def _remove_the_rest(plan, delete_personal, delete_shared, tray, result):
    """Stop TuxTalk, undo its desktop changes and delete its files, adding
    what's left to *result*."""
    home = plan.home
    stop_tuxtalk(plan, tray.shut_down if tray else None)
    result.not_removed += undo_desktop_changes(plan)
    # Looked for again: stopping may have written a file (the stats).
    again = find(home, plan.install_type, appimage=installation.get_running_appimage_path())
    again.kept = plan.kept
    if delete_personal:
        logger.info("Uninstalled TuxTalk; deleting the settings and this log")
        stop_file_logging()         # its folder is about to go
    failed = _wait_for(lambda: remove_files(again, delete_personal, delete_shared))
    result.not_removed += [_shown(path, home) for path in failed]
    _wait_for(_refresh_menus)


def run(tray=None):
    """Ask, then uninstall. True when TuxTalk was uninstalled, if not
    always all of it, and the caller should quit; False when nothing
    changed. *tray* (TrayParts) when the running tray asks."""
    install_type = installation.get_install_type()
    if install_type in ("dev", "flatpak"):
        _tell(Gtk.MessageType.INFO, "Not for this copy of TuxTalk",
              "The development version runs from its source folder, and a Flatpak "
              "is removed with: flatpak uninstall --delete-data io.github.ronb1964.TuxTalk")
        return False
    plan = find(Path.home(), install_type, appimage=installation.get_running_appimage_path())
    choice = _ask(plan)
    if choice is None:
        return False
    delete_personal, delete_shared, leave_group = choice
    logger.info(f"Uninstalling TuxTalk ({install_type}); settings "
                + ("deleted" if delete_personal else "kept") + ", shared models "
                + ("deleted" if delete_shared else "kept"))
    window = _working_window()
    try:
        # With no tray taking part, its lock is held meanwhile, as the tray
        # would: Preferences' restart takes it too, so neither starts or
        # removes a service under the other.
        _lock = None if tray else _wait_for(_hold_tray_lock)  # held until run() returns
        if not _clear_to_go(_lock, tray):
            return False
        if tray:
            tray.stop()
        try:
            result = _remove_root_part(plan, leave_group, tray)
        except Exception:
            _resume(tray, False)        # never leave the tray refusing everything
            raise
        if result is None:
            return False
        try:
            _remove_the_rest(plan, delete_personal, delete_shared, tray, result)
        except Exception as e:
            # The program, the helper or the tray may be gone already: going
            # back to a half-stopped TuxTalk would be worse than saying so.
            logger.exception("The uninstall stopped partway")
            result.not_removed.append(f"some of the rest: the uninstall stopped partway ({e})")
    finally:
        window.destroy()
    left = result.not_removed + (["the package"] if result.package_failed else [])
    logger.info("Uninstalled TuxTalk" if result.complete
                else f"Uninstalled TuxTalk, except: {', '.join(left)}")
    _tell(Gtk.MessageType.INFO if result.complete else Gtk.MessageType.WARNING,
          done_heading(result), done_text(plan, delete_personal, delete_shared, result),
          markup=True)
    return True


def _clear_to_go(lock, tray):
    """Whether the uninstall may go ahead: the tray's lock had (*lock* not
    False), and Preferences closed, its changes asked about. Says why not."""
    if lock is False:
        _tell(Gtk.MessageType.ERROR, "TuxTalk couldn't uninstall",
              "Nothing was removed: TuxTalk is busy starting or restarting. Quit it "
              "from its menu, then try again.")
        return False
    if not _wait_for(_close_preferences) or (tray and tray.preferences_open()):
        _tell(Gtk.MessageType.INFO, "Uninstall stopped",
              "Nothing was removed: Preferences is still open. Close it, then try "
              "Uninstall TuxTalk… again.")
        return False
    return True


def _hold_tray_lock():
    """The tray's lock, for an uninstall no tray takes part in; None when no
    lock can be had at all (said in the log), False when it's held past
    QUIT_WAIT_S (a running tray that can't uninstall, or a restart from
    Preferences that won't end): nothing is removed then. Off GTK's thread:
    it waits."""
    from . import single_instance, tray_instance
    try:
        lock = single_instance.open_lock(tray_instance.TRAY_LOCK)
    except OSError as e:
        logger.warning(f"Uninstalling without the tray's lock: {e}")
        return None
    if single_instance.claim(lock, wait_s=service_launcher.QUIT_WAIT_S):
        return lock
    lock.close()
    return False


def _close_preferences():
    """Close an open Preferences window as its title bar's close does (it
    asks about changes not applied) before anything goes; False when one is
    still open: Keep Editing, or one the close request couldn't reach (an
    older version, a pidfile that couldn't be written), which ending later
    would lose its changes. A development copy's is never counted. Off GTK's
    thread: it waits while the user answers."""
    from . import prefs_ipc
    if not prefs_ipc.ask_to_close(PREFS_CLOSE_WAIT_S):
        return False
    return not [pid for pid in prefs_ipc.open_windows() if not _is_dev(pid)]


# How long the uninstall waits for Preferences to close while the user
# answers its question about changes not applied.
PREFS_CLOSE_WAIT_S = 120


def _remove_root_part(plan, leave_group, tray):
    """End the dictation service, then run the root step (the package, the
    hotkey helper) if there is one: the service runs on those, and types
    what it holds first. Its Result, or None when nothing was removed (the
    service wouldn't stop, or the root step didn't run), with TuxTalk as it
    was before."""
    was_running = bool(_our_services())
    if not _wait_for(_end_service):
        _tell(Gtk.MessageType.ERROR, "TuxTalk couldn't uninstall",
              "Nothing was removed: TuxTalk's dictation service wouldn't stop. "
              "Restart the computer, then try Uninstall TuxTalk… again.")
        _resume(tray, False)
        return None
    if not plan.needs_root:
        return Result()
    result = _remove_as_root(plan, leave_group)
    if result is None:
        _resume(tray, was_running)
    return result


def _resume(tray, start_dictation):
    """Back as before an uninstall that didn't happen: the tray takes
    actions again, and dictation runs again if it ran."""
    if tray:
        tray.resume()
    if start_dictation:
        service_launcher.launch_dictation_service()
