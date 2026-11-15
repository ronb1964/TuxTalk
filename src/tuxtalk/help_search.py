"""Searching Help by the words a person knows.

Help is sorted by situation (Chatting, Writing, Terminals), so a feature
known only by its name, a TuxTalk Labs one above all, has no tab to look
under. The index holds every card as Help shows it to this person (their
own keys filled in), and every voice command card of the Commands tab;
help_dialog shows the matches and jumps to the one picked.
"""
import re
from typing import NamedTuple

from . import cards, commands_cards, help_outline
from . import commands_reference as ref
from .help_text import COMMANDS_TITLE


class Entry(NamedTuple):
    """One card Help can jump to: its tab, the feature it's under ("" for
    none), its heading and dim note, whether it's a Labs one, and its words
    as plain text."""
    tab: str
    part: str
    title: str
    note: str
    labs: bool
    text: str


# Everyday words for what Help calls by another name: CPU is always "the
# processor" there, GPU "the graphics card".
SYNONYMS = {
    "cpu": ("processor",),
    "gpu": ("graphics card",),
    "mic": ("microphone",),
    "hotkey": ("key",),
    "shortcut": ("key",),
    "autostart": ("log in",),
    "startup": ("log in",),
    "remove": ("uninstall",),
    "delete": ("uninstall",),
}
_LABS_WORDS = ("lab", "labs", "tuxtalk labs")
# Help's links (<a href>) are a GtkLabel's, which Pango's own parser doesn't
# know; their words stay.
_LINK_TAG = re.compile(r"</?a\b[^>]*>")


def _words(text):
    """*text* lower-cased, its runs of spaces and line breaks one space."""
    return " ".join(text.lower().split())


def _plain(markup):
    """*markup* as plain text, links included."""
    return cards.plain(_LINK_TAG.sub("", markup))


def _help_entries(tab, markup):
    """The entries of the Help tab *tab*: a feature's opening lines count as
    a card of their own, under the feature's name."""
    for part in help_outline.parse(markup).parts:
        if part.intro and part.title:
            yield Entry(tab, part.title, part.title, part.note, part.labs, _plain(part.intro))
        for section in part.sections:
            yield Entry(tab, part.title, section.title, section.note,
                        section.labs or part.labs, _plain(section.body))


def _command_entries():
    """One entry per card of the Commands tab, under its group's name."""
    for section in ref.SECTIONS:
        yield Entry(COMMANDS_TITLE, section.group, section.title, "", bool(section.labs),
                    commands_cards.spoken(section))


def index(sections):
    """Every card of *sections* (help_text.tabs()' list), in Help's order."""
    entries = []
    for tab, markup in sections:
        entries.extend(_command_entries() if tab == COMMANDS_TITLE else _help_entries(tab, markup))
    return tuple(entries)


def _wanted(query):
    """The words to look for: *query* itself and its other names."""
    query = _words(query)
    return (query, *SYNONYMS.get(query, ())) if query else ()


def _rank(entry, wanted, labs):
    """0 when *entry*'s heading or feature names a *wanted* word, 1 when
    its note, text or tab does or it's a Labs card and *labs* was asked
    for, else None."""
    if any(word in _words(f"{entry.part} {entry.title}") for word in wanted):
        return 0
    if (labs and entry.labs) or any(
            word in _words(f"{entry.note} {entry.text} {entry.tab}") for word in wanted):
        return 1
    return None


def search(entries, query):
    """The *entries* matching *query*, the ones naming it in their heading
    first, each kind in Help's order. Case and spacing don't count."""
    wanted = _wanted(query)
    if not wanted:
        return []
    labs = wanted[0] in _LABS_WORDS
    ranked = [(rank, n) for n, entry in enumerate(entries)
              if (rank := _rank(entry, wanted, labs)) is not None]
    return [entries[n] for _rank_, n in sorted(ranked)]


def highlight(text, query):
    """*text* as Pango markup with the first place *query* (or one of its
    other names) appears in bold."""
    lowered = text.lower()
    spots = [(at, len(word)) for word in _wanted(query) if (at := lowered.find(word)) >= 0]
    if not spots:
        return cards.escape(text)
    at, length = min(spots)
    return (cards.escape(text[:at]) + "<b>" + cards.escape(text[at:at + length]) + "</b>"
            + cards.escape(text[at + length:]))
