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
# processor" there, GPU "the graphics card", starting with the desktop
# "Launch TuxTalk at login".
SYNONYMS = {
    "cpu": ("processor",),
    "gpu": ("graphics card",),
    "mic": ("microphone",),
    "hotkey": ("dictation key",),
    "hotkeys": ("dictation key",),
    "autostart": ("at login",),
    "startup": ("at login",),
    "login": ("at login",),
    "remove": ("uninstall",),
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
    """One entry per card of the Commands tab, under its group's name, with
    the German words when the cards show them."""
    german = commands_cards.german_commands()
    for section in ref.SECTIONS:
        yield Entry(COMMANDS_TITLE, section.group, section.title, "", bool(section.labs),
                    commands_cards.spoken(section, german))


def index(sections):
    """Every card of *sections* (help_text.tabs()' list), in Help's order."""
    entries = []
    for tab, markup in sections:
        entries.extend(_command_entries() if tab == COMMANDS_TITLE else _help_entries(tab, markup))
    return tuple(entries)


# Words a question is made of that say nothing about what's wanted: "how
# to uninstall" is a search for "uninstall", "mic not working" for "mic";
# every card is about TuxTalk.
_FILLER = frozenset({
    "a", "an", "and", "are", "can", "do", "does", "for", "how", "i", "in", "is", "it", "me",
    "my", "not", "of", "on", "the", "to", "what", "when", "where", "why", "with", "work",
    "working", "tuxtalk"})


class _Query(NamedTuple):
    """A search, ready to match: the whole of it as one phrase, then each
    word that counts, with whether that word asks for TuxTalk Labs."""
    phrase: re.Pattern
    words: tuple


def _pattern(*names):
    """Where any of *names* starts a word ("ai" isn't in "again"), any case,
    any spacing between a name's words."""
    return re.compile("|".join(r"(?<!\w)" + r"\s+".join(map(re.escape, name.split()))
                               for name in dict.fromkeys(names)), re.IGNORECASE)


def _names(word):
    """*word* and its other names; a plural also finds its singular."""
    stem = word[:-1] if len(word) > 3 and word.endswith("s") else word
    return (stem, *SYNONYMS.get(word, ()), *SYNONYMS.get(stem, ()))


def _query(query):
    """*query* ready to match, or None for one with no words."""
    words = _words(query).split()
    if not words:
        return None
    kept = [word for word in words if word not in _FILLER] or words
    return _Query(_pattern(" ".join(words), *SYNONYMS.get(" ".join(words), ())),
                  tuple((_pattern(*_names(word)), word in _LABS_WORDS) for word in kept))


def _rank(entry, query):
    """How well *entry* answers *query*, best first: the phrase in its
    heading (0), every word there (1), the phrase or every word anywhere
    (2), then fewer words found (3 and on); None when none is. A Labs card
    answers a word asking for Labs."""
    heading = f"{entry.part} {entry.title}"
    rest = f"{entry.note} {entry.text} {entry.tab}"
    if query.phrase.search(heading):
        return 0
    if all(word.search(heading) for word, _labs in query.words):
        return 1
    found = sum(bool(word.search(heading) or word.search(rest) or (labs and entry.labs))
                for word, labs in query.words)
    if query.phrase.search(rest) or found == len(query.words):
        return 2
    return 3 + len(query.words) - found if found else None


def search(entries, query):
    """The *entries* answering *query*, best first (_rank), each rank in
    Help's order. Case, spacing and filler words don't count."""
    query = _query(query)
    if query is None:
        return []
    ranked = [(rank, n) for n, entry in enumerate(entries)
              if (rank := _rank(entry, query)) is not None]
    return [entries[n] for _rank_of_entry, n in sorted(ranked)]


def first_match(text, query):
    """(start, end) of the first place in *text* that *query* matches, the
    whole phrase before any one word; None when it's nowhere."""
    query = _query(query)
    if query is None:
        return None
    found = query.phrase.search(text) or min(
        (hit for word, _labs in query.words if (hit := word.search(text))),
        key=lambda hit: hit.start(), default=None)
    return found.span() if found else None


def highlight(text, query):
    """*text* as Pango markup with the first place *query* matches in bold."""
    span = first_match(text, query)
    if span is None:
        return cards.escape(text)
    start, end = span
    return (cards.escape(text[:start]) + "<b>" + cards.escape(text[start:end]) + "</b>"
            + cards.escape(text[end:]))
