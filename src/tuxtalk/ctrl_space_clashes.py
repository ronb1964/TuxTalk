"""Who else uses Ctrl+Space, so that pressing Left Ctrl+Space as a dictation
key may do their job instead of dictating.

An input method (IBus, Fcitx 5) listens for it as its on/off or next-method
key, and GNOME can bind it to switching the input source; each is looked for
only while it's running (or, for GNOME, on GNOME), since a setting left
behind by a program that isn't running is no clash. A keyboard option that
makes Ctrl+Space switch the layout, or gives Left Ctrl another job, is one
too. KDE's own shortcuts aren't here: kwin_hotkeys' verdict on the key
covers them.
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
    *where* it is changed. *sentence* replaces the stock wording, with
    *headline* as the question's title, for the keyboard options, whose
    wording says what they do to the pair; *fix* is then the advice."""
    who: str
    what: str
    where: str
    headline: str = ""
    sentence: str = ""
    fix: str = ""

    def title(self) -> str:
        """The title of the question Preferences asks."""
        return self.headline or f"{self.who} already uses Ctrl+Space"

    def paragraph(self) -> str:
        """What Preferences tells someone who picks Left Ctrl+Space."""
        fix = self.fix or f"give {self.who} another shortcut"
        return (f"{self.sentence or f'{self.who} already uses Ctrl+Space {self.what}'}, "
                f"so pressing Left Ctrl+Space may do that instead of dictating. "
                f"Pick another key, or {fix} ({self.where}).")

    def log_line(self) -> str:
        """What the log says about it at the service's start."""
        return (f"{self.sentence or f'{self.who} also uses Ctrl+Space {self.what}'}; "
                "Left Ctrl+Space may do that instead of dictating")


IBUS = Clash("IBus", "to switch input methods", "IBus Preferences, Next input method")
FCITX5 = Clash("Fcitx 5", "to turn the input method on and off",
               "Fcitx 5 Configuration, Global Options, Trigger Input Method")
GNOME = Clash("GNOME", "to switch the input source",
              "Settings, Keyboard, View and Customize Shortcuts")


def _keyboard_options(does: str, headline: str = "Your keyboard options change Left Ctrl"
                      ) -> Clash:
    """A clash with the keyboard's XKB options, which *does* says."""
    return Clash("Your keyboard options", "", "Settings, Keyboard, Keyboard options",
                 headline=headline, sentence=f"Your keyboard options {does}",
                 fix="change those keyboard options")


_LEFT_CTRL_SWITCHES_LAYOUT = _keyboard_options("make Left Ctrl switch the layout")

# The XKB options after which Left Ctrl+Space isn't a plain Ctrl+Space, as
# xkeyboard-config's symbols define them. Those that need another key with
# Left Ctrl (grp:lctrl_lshift_toggle and the like) or AltGr
# (compose:lctrl-altgr) leave the pair alone, so they aren't here.
KEYBOARD_OPTIONS = {
    "grp:ctrl_space_toggle": _keyboard_options(
        "make Ctrl+Space switch the layout", headline="Your keyboard options use Ctrl+Space"),
    "grp:lctrl_toggle": _LEFT_CTRL_SWITCHES_LAYOUT,
    "grp:ctrl_select": _LEFT_CTRL_SWITCHES_LAYOUT,
    "ctrl:grouptoggle_capscontrol": _LEFT_CTRL_SWITCHES_LAYOUT,
    "compose:lctrl": _keyboard_options("make Left Ctrl start a Compose sequence"),
    "ctrl:swapcaps": _keyboard_options("make Left Ctrl act as Caps Lock"),
    "ctrl:swap_lalt_lctl": _keyboard_options("make Left Ctrl act as Alt"),
    "ctrl:swap_lwin_lctl": _keyboard_options("make Left Ctrl act as Super"),
    "ctrl:swap_lalt_lctl_lwin": _keyboard_options("make Left Ctrl act as Super"),
    "altwin:ctrl_alt_win": _keyboard_options("make Left Ctrl act as Super"),
    "ctrl:lctrl_meta": _keyboard_options("make Left Ctrl act as Meta"),
    "ctrl:hyper_capscontrol": _keyboard_options("make Left Ctrl act as Hyper"),
}

# GNOME's shortcut schemas that could hold Ctrl+Space.
_GNOME_SCHEMAS = (
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.shell.keybindings",
    "org.gnome.settings-daemon.plugins.media-keys",
    "org.gnome.mutter.keybindings",
)

GSETTINGS_TIMEOUT_S = 2
# The longest clashes() can take: IBus's one lookup and one per GNOME schema.
LOOKUP_BUDGET_S = GSETTINGS_TIMEOUT_S * (1 + len(_GNOME_SCHEMAS))

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


def keyboard_options_clash(options: str | None) -> Clash | None:
    """What a comma-separated XKB options string does to Left Ctrl+Space, or None."""
    return next((KEYBOARD_OPTIONS[o] for o in (options or "").split(",")
                 if o in KEYBOARD_OPTIONS), None)


def _running(name: str, proc: str = "/proc") -> bool:
    """Whether this user has a process called *name* (its /proc/<pid>/comm,
    which keeps 15 characters: names must stay short)."""
    uid = os.getuid()
    try:
        pids = os.listdir(proc)
    except OSError as e:
        logger.info(f"Could not list the running programs: {e}")
        return False
    for pid in pids:
        if not pid.isdigit():
            continue
        try:
            if os.stat(os.path.join(proc, pid)).st_uid != uid:
                continue
            # errors="replace": comm's 15 bytes can end inside a character.
            with open(os.path.join(proc, pid, "comm"), encoding="utf-8",
                      errors="replace") as f:
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
                              errors="replace", timeout=GSETTINGS_TIMEOUT_S, check=True).stdout
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
    options = keyboard_options_clash(_xkb_options())
    if options:
        found.append(options)
    return found


def log_clashes() -> None:
    """Say in the log who else uses Ctrl+Space, for each."""
    for clash in clashes():
        logger.info(clash.log_line())
