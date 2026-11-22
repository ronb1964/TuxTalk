"""Which new dictation keys GNOME or COSMIC already uses for a shortcut of its
own, for Preferences to say after a change.

KDE says so itself when TuxTalk asks it to hold a key back
(kwin_hotkeys.refused_since), and ctrl_space_clashes knows who else takes
Ctrl+Space. On GNOME a key the desktop also binds does both jobs; on COSMIC
it isn't held back from the app either (cosmic_hotkeys.claim skips it). Only
the desktop's own settings can be read: apps' shortcuts can't
(dictation_keys.warning says so).
"""
import ast
import re
from dataclasses import dataclass

from . import cosmic_hotkeys, desktop_detect
from .ctrl_space_clashes import GNOME_SCHEMAS, GSETTINGS_TIMEOUT_S, gsettings
from .dictation_keys import CTRL_SPACE
from .hotkey_claims import qt_key_sequence
from .logger import setup_logger

logger = setup_logger(__name__)

# The shortcuts made in GNOME Settings: a list of paths, each one a
# relocatable schema that list-recursively on the parent doesn't show.
_CUSTOM_LIST = "org.gnome.settings-daemon.plugins.media-keys"
_CUSTOM = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
# How many of them are read, so the lookup's time has a bound.
MAX_CUSTOM = 10
# The longest clashes() can take: a gsettings call per GNOME schema, one for
# the list of custom shortcuts and one per custom shortcut read.
LOOKUP_BUDGET_S = GSETTINGS_TIMEOUT_S * (len(GNOME_SCHEMAS) + 1 + MAX_CUSTOM)

# One quoted accelerator in a gsettings value: '<Primary><Alt>t'.
_ACCELERATOR = re.compile(r"'((?:<[^<>']+>)*)([^<>']+)'")
# GTK's modifier names, as cosmic_hotkeys.binding names them.
_GTK_MODIFIERS = {"primary": "Ctrl", "control": "Ctrl", "ctrl": "Ctrl", "alt": "Alt",
                  "mod1": "Alt", "super": "Super", "shift": "Shift"}


@dataclass(frozen=True)
class ShortcutClash:
    """*desktop* binds dictation *key* to its shortcut *setting* ("" when
    unnamed), which is changed *where*."""
    desktop: str
    key: str
    setting: str
    where: str

    def title(self) -> str:
        """The title of the question Preferences asks."""
        return f"{self.desktop} already uses {self.key}"

    def paragraph(self) -> str:
        """What Preferences says about it."""
        which = f" ({self.setting})" if self.setting else ""
        return (f"{self.desktop} already uses {self.key} for one of its shortcuts{which}, so "
                "pressing it may do that as well as dictating. Pick another key, or change "
                f"that shortcut in {self.where}.")


def gnome_bindings(listing: str) -> dict:
    """{(frozenset of modifiers, key in lower case): setting} for the
    accelerators in a `gsettings list-recursively` *listing*."""
    found = {}
    for line in listing.splitlines():
        _schema, _, rest = line.partition(" ")
        setting, _, value = rest.partition(" ")
        for mods, key in _ACCELERATOR.findall(value):
            names = frozenset(_GTK_MODIFIERS.get(mod.lower(), mod)
                              for mod in re.findall(r"<([^<>]+)>", mods))
            found.setdefault((names, key.lower()), setting)
    return found


def gnome_custom_bindings() -> dict:
    """gnome_bindings for the shortcuts made in GNOME Settings, each named
    by the name given it there."""
    try:
        paths = ast.literal_eval(gsettings("get", _CUSTOM_LIST, "custom-keybindings")
                                 .removeprefix("@as").strip() or "[]")
    except (ValueError, SyntaxError):
        return {}
    if len(paths) > MAX_CUSTOM:
        logger.info(f"Only the first {MAX_CUSTOM} of {len(paths)} GNOME custom shortcuts "
                    "were checked for the new keys")
    found = {}
    for path in paths[:MAX_CUSTOM]:
        listing = gsettings("list-recursively", f"{_CUSTOM}:{path}")
        name = re.search(r" name '([^']*)'", listing)
        bound = "\n".join(line for line in listing.splitlines() if " binding " in line)
        for binding in gnome_bindings(bound):
            found.setdefault(binding, name.group(1) if name else "")
    return found


def _binding(name: str):
    """(frozenset of modifiers, key in lower case) for dictation key *name*,
    as both desktops spell it, or None for one no desktop can bind."""
    sequence = qt_key_sequence(name)
    if not sequence:
        return None
    mods, key = cosmic_hotkeys.binding(sequence)
    return frozenset(mods), key.lower()


def clashes(names) -> list:
    """A ShortcutClash for each dictation key in *names* this desktop binds."""
    # Left Ctrl+Space is ctrl_space_clashes' to look up, GNOME's use included.
    bindings = {binding: name for binding, name in
                ((_binding(name), name) for name in names if name and name != CTRL_SPACE)
                if binding}
    if not bindings:
        return []
    desktop = desktop_detect.get_desktop_environment()
    if desktop == "gnome":
        taken = {**gnome_custom_bindings(),
                 **gnome_bindings("\n".join(gsettings("list-recursively", schema)
                                            for schema in GNOME_SCHEMAS))}
        return [ShortcutClash("GNOME", name, taken[binding],
                              "Settings, Keyboard, View and Customize Shortcuts")
                for binding, name in bindings.items() if binding in taken]
    if desktop == "cosmic":
        taken = cosmic_hotkeys.others_bindings()
        return [ShortcutClash("COSMIC", name, "", "COSMIC Settings, Keyboard, Shortcuts")
                for binding, name in bindings.items() if binding in taken]
    return []
