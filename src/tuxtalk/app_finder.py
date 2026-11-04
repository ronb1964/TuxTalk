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


@dataclass(frozen=True)
class AppEntry:
    app_id: str          # the .desktop file's id: "org.kde.konsole.desktop"
    name: str            # as the menu shows it (localised)
    english_name: str    # the untranslated Name, so "Files" works on a German desktop
    generic: str         # "Web Browser", "Calculator"
    keywords: tuple
    program: str         # the command it runs: "firefox"
    install: str         # "", "flatpak", "snap" or "appimage"


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
        self.labels = {" ".join(_clean(t)) for t in (entry.generic, *entry.keywords)}
        self.labels.discard("")
        # Names and program with the spaces taken out ("fire fox" -> "firefox").
        self.letters = {s.replace(" ", "") for s in (*self.names, self.program) if s}

    def starts_name(self, words):
        """Whether *words* start the words of a name, in order ("libre writer")."""
        return any(_words_start(words, name.split()) for name in self.names)


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


def _candidates(words, indexed):
    """The entries the first matching rule finds, rules in the spec's order."""
    said = " ".join(words)
    letters = said.replace(" ", "")
    rules = (
        lambda i: said in i.names,
        lambda i: letters in i.letters,
        lambda i: said in i.labels,
        lambda i: i.starts_name(words),
    )
    for rule in rules:
        hits = [i for i in indexed if rule(i)]
        if hits:
            return hits
    return _fuzzy(letters, indexed)


def _one_of_each_app(items, install):
    """One entry per app: the same app installed twice is one app, not a tie.

    The copy the request names, else the distro's own, else the first listed.
    """
    groups = {}
    for item in items:
        e = item.entry
        key = " ".join(_clean(e.english_name or e.name)) or e.app_id
        groups.setdefault(key, []).append(e)
    chosen = []
    for copies in groups.values():
        by_install = {c.install: c for c in reversed(copies)}
        chosen.append(by_install.get(install) or by_install.get("") or copies[0])
    return tuple(chosen)


def find(request, entries, defaults):
    """The Found result for *request* among *entries*.

    *defaults* maps "browser", "email", "files", "text editor" and "terminal"
    to app ids; saying one of those words opens the default app.
    """
    words = _clean(request.name)
    if not words:
        return _NONE
    kind = _DEFAULT_WORDS.get(" ".join(words))
    default_id = defaults.get(kind) if kind else None
    if default_id:
        match = [e for e in entries if e.app_id == default_id]
        if match:
            return Found("one", (match[0],))
    hits = _candidates(words, [_Indexed(e) for e in entries])
    if not hits:
        return _NONE
    apps = _one_of_each_app(hits, request.install)
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
            keywords=tuple(app.get_keywords() or ()),
            program=program_name(command),
            install=install_kind(command)))
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
