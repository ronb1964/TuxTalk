"""Who else uses Ctrl+Space, so that pressing Left Ctrl+Space as a dictation
key may do their job instead of dictating.

An input method (IBus, Fcitx 5) listens for it as its on/off or next-method
key, and GNOME can bind it to switching the input source; each is looked for
only while it's running (or, for GNOME, on GNOME), since a setting left
behind by a program that isn't running is no clash. A keyboard option that
makes Left Ctrl Compose or a layout switch is one too. KDE's own shortcuts
aren't here: kwin_hotkeys' verdict on the key covers them.
"""
import logging
import os
import re
import subprocess
from dataclasses import dataclass

from . import desktop_detect, keyboard_layout

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Clash:
    """A program or setting that takes Ctrl+Space: *who* does *what*, and
    *where* its shortcut is changed."""
    who: str
    what: str
    where: str


IBUS = Clash("IBus", "to switch input methods", "IBus Preferences, Next input method")
FCITX5 = Clash("Fcitx 5", "to turn the input method on and off",
               "Fcitx 5 Configuration, Global Options, Trigger Input Method")
GNOME = Clash("GNOME", "to switch the input source",
              "Settings, Keyboard, View and Customize Shortcuts")
KEYBOARD_OPTIONS = Clash("Your keyboard settings",
                         "to make Left Ctrl a Compose or layout-switch key",
                         "Settings, Keyboard, Keyboard options")

# The XKB options that give Left Ctrl a job of its own.
_LEFT_CTRL_OPTIONS = frozenset({
    "compose:lctrl", "compose:lctrl-altgr", "grp:lctrl_toggle", "grp:ctrl_select",
    "grp:lctrl_lshift_toggle", "grp:lctrl_lalt_toggle", "grp:lctrl_lwin_toggle",
})

# GNOME's shortcut schemas that could hold Ctrl+Space.
_GNOME_SCHEMAS = (
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.shell.keybindings",
    "org.gnome.settings-daemon.plugins.media-keys",
    "org.gnome.mutter.keybindings",
)

# Ctrl+Space as GTK accelerators write it, and no other key with it.
_CTRL_SPACE_ACCEL = re.compile(r"'<(?:Control|Primary|Ctrl)>space'", re.IGNORECASE)


def ibus_triggers_ctrl_space(gsettings_value: str) -> bool:
    """Whether IBus's trigger list (gsettings, "['<Control>space']") has Ctrl+Space."""
    return bool(_CTRL_SPACE_ACCEL.search(gsettings_value))


def fcitx5_triggers_ctrl_space(config_text: str | None) -> bool:
    """Whether Fcitx 5's config turns the input method on with Ctrl+Space. It
    does when it says nothing (no file, or no trigger section): that's its default."""
    if config_text is None:
        return True
    keys = []
    section_seen = in_section = False
    for line in config_text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_section = line == "[Hotkey/TriggerKeys]"
            section_seen = section_seen or in_section
        elif in_section and "=" in line:
            keys.append(line.partition("=")[2].strip().lower())
    return not section_seen or "control+space" in keys


def gnome_bindings_ctrl_space(listing: str) -> bool:
    """Whether a `gsettings list-recursively` listing binds Ctrl+Space."""
    return bool(_CTRL_SPACE_ACCEL.search(listing))


def xkb_options_use_left_ctrl(options: str | None) -> bool:
    """Whether a comma-separated XKB options string gives Left Ctrl another job."""
    return bool(options) and not _LEFT_CTRL_OPTIONS.isdisjoint(options.split(","))


def _running(name: str, proc: str = "/proc") -> bool:
    """Whether this user has a process called *name* (its /proc/<pid>/comm)."""
    uid = os.getuid()
    for pid in os.listdir(proc):
        if not pid.isdigit():
            continue
        try:
            if os.stat(os.path.join(proc, pid)).st_uid != uid:
                continue
            with open(os.path.join(proc, pid, "comm"), encoding="utf-8") as f:
                if f.read().strip() == name:
                    return True
        except OSError:     # the process ended while we looked
            continue
    return False


def _gsettings(*args: str) -> str:
    """What `gsettings` prints for *args*; "" when it can't say (no gsettings,
    an unknown schema, too slow)."""
    try:
        return subprocess.run(["gsettings", *args], capture_output=True, text=True,
                              errors="replace", timeout=2, check=True).stdout
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(f"gsettings {' '.join(args)} gave no answer: {e}")
        return ""


def fcitx5_config_path() -> str:
    """Fcitx 5's global config file."""
    return os.path.join(desktop_detect.config_home(), "fcitx5", "config")


def _fcitx5_config() -> str | None:
    """Fcitx 5's global config, or None when there is none to read."""
    try:
        with open(fcitx5_config_path(), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _xkb_options() -> str | None:
    """The active keyboard's XKB options, None when they can't be told."""
    layout = keyboard_layout.active_layout()
    return layout[2] if layout else None


def clashes() -> list[Clash]:
    """Everything running or set that also takes Ctrl+Space."""
    found = []
    if _running("ibus-daemon") and ibus_triggers_ctrl_space(
            _gsettings("get", "org.freedesktop.ibus.general.hotkey", "triggers")):
        found.append(IBUS)
    if _running("fcitx5") and fcitx5_triggers_ctrl_space(_fcitx5_config()):
        found.append(FCITX5)
    if desktop_detect.get_desktop_environment() == "gnome" and gnome_bindings_ctrl_space(
            "\n".join(_gsettings("list-recursively", schema) for schema in _GNOME_SCHEMAS)):
        found.append(GNOME)
    if xkb_options_use_left_ctrl(_xkb_options()):
        found.append(KEYBOARD_OPTIONS)
    return found


def log_clashes() -> None:
    """Say in the log who else uses Ctrl+Space, for each."""
    for clash in clashes():
        logger.info(f"{clash.who} also uses Ctrl+Space {clash.what}; "
                    "Left Ctrl+Space may do that instead of dictating")
