"""Open apps by voice (TuxTalk Labs): which installed app a spoken name means.

`find` is pure: it takes the menu's apps as AppEntry values and the user's
default apps, so tests drive it with lists. `installed_apps` and `default_apps`
are the thin readers of the desktop (GLib's app list, KDE's terminal setting).

A weak match is never reported as "one": a wrong app opening is worse than a
message saying nothing fit.
"""
import configparser
import difflib
import logging
import os
import shlex
from dataclasses import dataclass

from .custom_words import MIN_SOUND_LETTERS, sound_key
from .voice_navigation import normalize

log = logging.getLogger(__name__)

# Chosen with tests/test_app_finder.py's respellings ("console" for Konsole and
# "fierfox" for Firefox both score 0.86) against a wrong one that must not open
# anything ("fierfox zz" scores 0.75).
CLOSE_ENOUGH = 0.8
# Below this a fuzzy match is chance ("cat" for "kate"); prefix matching
# needs it too, so "a" or "of" never start an app.
_MIN_SHORT_WORD = 3
_MIN_FUZZY_LETTERS = 4
_NOISE_WORDS = ("the", "app")

# What people say for "the app I use for this", mapped to the kind
# `default_apps` reports.
_DEFAULT_WORDS = {
    "browser": "browser", "web browser": "browser", "web": "browser",
    "email": "email", "mail": "email",
    "files": "files", "file manager": "files",
    "text editor": "text editor",
    "terminal": "terminal",
}
_KDEGLOBALS = os.path.join("~", ".config", "kdeglobals")
# Beyond Your Words' sound folds: the silent letters a speech engine writes
# differently in an app's name ("K right" for KWrite).
_APP_SOUND_FOLDS = (("wr", "r"), ("igh", "i"), ("kay", "k"))


@dataclass(frozen=True)
class AppEntry:
    app_id: str          # the .desktop file's id: "org.kde.konsole.desktop"
    name: str            # as the menu shows it (localised)
    english_name: str    # the untranslated Name, so "Files" works on a German desktop
    generic: str         # "Web Browser", "Calculator"
    program: str         # the command it runs: "firefox"
    install: str         # "", "flatpak", "snap" or "appimage"
    wm_class: str = ""   # StartupWMClass: its windows' class, when the entry says


@dataclass(frozen=True)
class Found:
    kind: str            # "one", "tie" or "none"
    entries: tuple       # the app, or each app that fits equally well


_NONE = Found("none", ())


def _clean(text):
    """Normalised words without the filler people add ("the", "app")."""
    return [w for w in normalize(text).split() if w not in _NOISE_WORDS]


class _Indexed:
    """One entry with the spellings it can be matched by, worked out once."""

    def __init__(self, entry):
        self.entry = entry
        names = {" ".join(_clean(n)) for n in (entry.name, entry.english_name)}
        self.names = sorted(n for n in names if n)
        self.program = " ".join(_clean(entry.program))
        # GenericName, not Keywords: Konsole's "script" made "start the script"
        # open a terminal.
        self.labels = {" ".join(_clean(entry.generic))} - {""}
        self.generic_words = set(_clean(entry.generic))
        # Names and program with the spaces taken out ("fire fox" -> "firefox").
        self.letters = {s.replace(" ", "") for s in (*self.names, self.program) if s}
        self.sounds = {_sounds(s) for s in self.letters}

    def has_name_words(self, words):
        """Whether *words* are whole, consecutive words of a name ("chrome"
        of "google chrome"), none of them a word of what the app is ("web"
        of "brave web browser", its generic name "web browser")."""
        if self.generic_words.intersection(words):
            return False
        n = len(words)
        return any(parts[k:k + n] == words for parts in (name.split() for name in self.names)
                   for k in range(len(parts) - n + 1))

    def starts_name(self, words):
        """Whether *words* start the words of a name, in order ("libre writer")."""
        return any(_words_start(words, name.split()) for name in self.names)


def _sounds(letters):
    """*letters* folded so names that sound alike compare equal: Your Words'
    folds, the app ones above, and no silent final e ("kwrite" and "kright"
    both give "krit")."""
    letters = "".join(c for c in letters if c.isalnum())
    for a, b in _APP_SOUND_FOLDS:
        letters = letters.replace(a, b)
    return sound_key(letters).removesuffix("e")


def _words_start(said, name_words):
    """Each said word starts a later word of the name than the one before."""
    position = 0
    for word in said:
        if len(word) < _MIN_SHORT_WORD:
            return False
        while position < len(name_words) and not name_words[position].startswith(word):
            position += 1
        if position == len(name_words):
            return False
        position += 1
    return True


def _fuzzy(letters, indexed):
    """The entries whose spelling is closest to *letters*, if close enough."""
    if len(letters) < _MIN_FUZZY_LETTERS:
        return []
    scored = [(max((difflib.SequenceMatcher(None, letters, s).ratio()
                    for s in item.letters), default=0.0), item) for item in indexed]
    best = max((score for score, _ in scored), default=0.0)
    if best < CLOSE_ENOUGH:
        return []
    return [item for score, item in scored if score == best]


def _candidates(words, indexed, strict=False):
    """The entries the first matching rule finds, rules in the spec's order.
    *strict*: only the rules that take whole words as said, never the start
    of a word, a sound or a close spelling, for a window command, where
    "Close the fire." must not close Firefox: the whole name, its letters,
    the generic name, or whole words of the name ("close Chrome")."""
    said = " ".join(words)
    letters = said.replace(" ", "")
    sounds = _sounds(letters) if len(letters) >= MIN_SOUND_LETTERS else ""
    # Sounding alike comes after every rule that matches what was said, so
    # "open contact" stays Contacts and never becomes Kontact.
    rules = (
        lambda i: said in i.names,
        lambda i: letters in i.letters,
        lambda i: said in i.labels,
        lambda i: i.starts_name(words),
        lambda i: bool(sounds) and sounds in i.sounds,
    )
    strict_rules = (*rules[:3], lambda i: i.has_name_words(words))
    for rule in strict_rules if strict else rules:
        hits = [i for i in indexed if rule(i)]
        if hits:
            return hits
    return [] if strict else _fuzzy(letters, indexed)


def _name_key(entry):
    return " ".join(_clean(entry.english_name or entry.name)) or entry.app_id


def _packaged_as(entry, program):
    """Whether *entry* is a Flatpak or Snap of *program*: its id ends with it
    ("org.mozilla.firefox" for firefox)."""
    stem = entry.app_id.removesuffix(".desktop").casefold()
    return entry.install in ("flatpak", "snap") and bool(program) and stem.endswith(
        "." + program)


def _same_program(a, b):
    pa, pb = a.program.casefold(), b.program.casefold()
    return (bool(pa) and pa == pb) or _packaged_as(a, pb) or _packaged_as(b, pa)


def copies(entry, entries):
    """Every copy of *entry*'s app among *entries*, *entry* first: the
    entries that run the same program, as the distro's Firefox, Flathub's
    and the Snap do, whatever each is named ("Firefox Web Browser" on Mint).

    A program behind two entries of one install starts different apps
    (LibreOffice's Writer and Calc, Steam's games): then a copy must have
    *entry*'s name too. Different programs with one name ("Calculator") are
    never copies.
    """
    same = [entry] + [e for e in entries if e != entry and _same_program(entry, e)]
    installs = [e.install for e in same]
    if len(set(installs)) < len(installs):
        name = _name_key(entry)
        same = [e for e in same if _name_key(e) == name]
    return same


def _pick(group, install):
    """The copy *install* names, else the distro's own, else the first."""
    by_install = {c.install: c for c in reversed(group)}
    return by_install.get(install) or by_install.get("") or group[0]


def _one_of_each_app(hits, entries, install):
    """One entry per app among *hits*: the same app installed twice is one
    app, not a tie, and the copy opened is _pick's from all its copies, not
    only the ones that matched."""
    chosen, seen = [], set()
    for item in hits:
        if item.entry.app_id in seen:
            continue
        group = copies(item.entry, entries)
        seen.update(e.app_id for e in group)
        chosen.append(_pick(group, install))
    return tuple(chosen)


def find(request, entries, defaults, strict=False):
    """The Found result for *request* among *entries*.

    *defaults* maps "browser", "email", "files", "text editor" and "terminal"
    to app ids; saying one of those words opens the default app. *strict*:
    the whole name only (_candidates).
    """
    words = _clean(request.name)
    if not words:
        return _NONE
    kind = _DEFAULT_WORDS.get(" ".join(words))
    default_id = defaults.get(kind) if kind else None
    if default_id:
        match = [e for e in entries if e.app_id == default_id]
        if match:
            # The user's own choice, unless the request names another copy.
            chosen = next((c for c in copies(match[0], entries)
                           if request.install and c.install == request.install), match[0])
            return Found("one", (chosen,))
    hits = _candidates(words, [_Indexed(e) for e in entries], strict)
    if not hits:
        return _NONE
    apps = _one_of_each_app(hits, entries, request.install)
    return Found("one" if len(apps) == 1 else "tie", apps)


def program_name(commandline):
    """The program a menu entry's Exec line starts: "firefox" for "/usr/bin/firefox %u".

    Skips "env" and its VAR=value words (Snap's entries), and reads Flatpak's
    --command= since the line itself starts "flatpak run".
    """
    try:
        words = shlex.split(commandline or "")
    except ValueError:
        return ""
    for word in words:
        if word.startswith("--command="):
            return word.split("=", 1)[1]
    for word in words:
        if word != "env" and "=" not in word:
            return os.path.basename(word)
    return ""


def install_kind(commandline):
    """"flatpak", "snap" or "appimage" for how a menu entry's app is installed, else ""."""
    low = (commandline or "").lower()
    if "flatpak run" in low:
        return "flatpak"
    if "/snap/bin/" in low or "snap run" in low:
        return "snap"
    if ".appimage" in low:
        return "appimage"
    return ""


def _desktop_apps():
    """The menu's own list: shown entries only (not hidden, other desktops', or
    with a missing TryExec), as Gio.DesktopAppInfo."""
    from gi.repository import Gio
    return [a for a in Gio.AppInfo.get_all()
            if isinstance(a, Gio.DesktopAppInfo) and a.should_show()]


def installed_apps():
    """Every app the desktop's menu shows, as AppEntry values."""
    entries = []
    for app in _desktop_apps():
        command = app.get_commandline() or ""
        entries.append(AppEntry(
            app_id=app.get_id() or "",
            name=app.get_name() or "",
            english_name=app.get_string("Name") or "",
            generic=app.get_generic_name() or "",
            program=program_name(command),
            install=install_kind(command),
            wm_class=app.get_startup_wm_class() or ""))
    return entries


def _terminal_id(apps, preferred):
    """The id of the user's terminal: KDE's setting, else the only emulator."""
    for app in apps:
        if preferred and program_name(app.get_commandline()) == preferred:
            return app.get_id()
    emulators = [a for a in apps if "TerminalEmulator" in (a.get_categories() or "")]
    return emulators[0].get_id() if len(emulators) == 1 else None


def _kde_terminal():
    """KDE's chosen terminal program from kdeglobals, or "" when it isn't set."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        # errors="replace": one bad byte in a file KDE owns must not stop
        # every open command.
        with open(os.path.expanduser(_KDEGLOBALS), encoding="utf-8",
                  errors="replace") as f:
            parser.read_file(f)
    except FileNotFoundError:
        return ""
    except (OSError, configparser.Error) as exc:
        log.info("Couldn't read kdeglobals for the terminal: %s", exc)
        return ""
    return parser.get("General", "TerminalApplication", fallback="").strip()


def default_apps():
    """App ids for "browser", "email", "files", "text editor" and "terminal"."""
    from gi.repository import Gio
    lookups = {
        "browser": Gio.AppInfo.get_default_for_uri_scheme("https"),
        "email": Gio.AppInfo.get_default_for_uri_scheme("mailto"),
        "files": Gio.AppInfo.get_default_for_type("inode/directory", False),
        "text editor": Gio.AppInfo.get_default_for_type("text/plain", False),
    }
    found = {kind: app.get_id() for kind, app in lookups.items() if app}
    terminal = _terminal_id(_desktop_apps(), _kde_terminal())
    if terminal:
        found["terminal"] = terminal
    return found
