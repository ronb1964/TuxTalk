"""Which installed app a voice command names: "open Firefox", "close
Firefox", "switch to Firefox". Shared by open_app and window_actions, which
each say what happens when no app fits (the words are typed) or several fit
equally (a note names them)."""
from dataclasses import dataclass

from . import app_finder, notices
from .app_commands import OpenRequest
from .logger import setup_logger

logger = setup_logger(__name__)


@dataclass(frozen=True)
class Match:
    kind: str                # "one", "tie" or "none", as app_finder.Found's
    entries: tuple           # the app, or each app that fits equally well
    # Other copies of the app are installed (or one was named): only a window
    # naming its menu entry is its own (app_focus.window_names).
    exact: bool = False
    # Found by some words of each one's menu name only (app_finder.Found):
    # (entry, exact) for each, which a window command takes only if open.
    partial: tuple = ()

    @property
    def entry(self):
        return self.entries[0]


def find(request: OpenRequest, strict: bool = False) -> Match:
    """The app *request* names among the menu's apps. *strict*: by its name
    as said, its generic name or a default-app word, else by some whole
    words of its name, with *partial* set (app_finder.find)."""
    entries = app_finder.installed_apps()
    found = app_finder.find(request, entries, app_finder.default_apps(), strict)
    def exact(entry):
        return bool(request.install) or len(app_finder.copies(entry, entries)) > 1
    if found.partial:
        return Match(found.kind, found.entries, partial=tuple((e, exact(e))
                                                              for e in found.entries))
    if found.kind != "one":
        return Match(found.kind, tuple(found.entries))
    return Match("one", found.entries, exact(found.entries[0]))


def say_tie(match: Match, action: str, nothing_done: str, beeps_on: bool) -> None:
    """Several apps fit the voice command *action* equally: logged by their
    ids, never the words said, and a note names them, ending *nothing_done*."""
    logger.info(f"Voice command: {action}, but these fit equally: "
                + ", ".join(e.app_id for e in match.entries))
    label = tie_label(match.entries) + " Say the full name."
    notices.command_refused(label, f"{label} {nothing_done}", beeps_on)


def tie_label(entries) -> str:
    """"Two apps fit: Konsole, Kitty." Up to three names, so it stays short."""
    names = [e.name for e in entries]
    count = {2: "Two", 3: "Three"}.get(len(names), "Several")
    shown = ", ".join(names[:3]) + (" and more" if len(names) > 3 else "")
    return f"{count} apps fit: {shown}."
