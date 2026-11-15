"""The page Help's search shows instead of its tabs: a line saying how many
cards matched, then each one in Help's card look with its tab named in a
tag, so the results never pass for one of Help's own pages. A result is a
button: a click, Enter or Space hands its entry to the caller, which opens
the tab at that card (help_dialog).
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import cards, commands_cards, help_pages, help_search  # noqa: E402
from .help_text import COMMANDS_TITLE  # noqa: E402

# How much of a card's text a result shows: enough to tell two "How it
# works" cards apart, and the place the word is, if it's further in.
_SNIPPET_CHARS = 220
_LEAD_CHARS = 60


def count_line(found, query):
    """"3 results for 'labs'", or that there are none."""
    if not found:
        return f"No results for '{query}'"
    return f"{len(found)} result{'' if len(found) == 1 else 's'} for '{query}'"


def snippet(text, query):
    """A line of *text* to show under a result: its start, or from just
    before the first place *query* appears when that's further in."""
    text = " ".join(text.split())
    at = max(text.lower().find(query.strip().lower()), 0)
    start = 0 if at < _SNIPPET_CHARS - _LEAD_CHARS else text.rfind(" ", 0, at - _LEAD_CHARS) + 1
    end = text.find(" ", start + _SNIPPET_CHARS)
    shown = text[start:] if end < 0 else text[start:end]
    return ("…" if start else "") + shown + ("…" if end >= 0 else "")


def _look(entry):
    """(look, icon) of *entry*'s tab, or of its group on the Commands tab."""
    if entry.tab == COMMANDS_TITLE:
        return commands_cards.GROUP_LOOK[entry.part]
    return help_pages.TAB_LOOK[entry.tab]


def _heading(entry):
    """A result's heading: the feature's name before a card's own, as two
    features both have a "How it works"."""
    if entry.part and entry.part != entry.title and entry.tab != COMMANDS_TITLE:
        return f"{entry.part}: {entry.title}"
    return entry.title


def _result(entry, query, on_pick):
    """*entry*'s result: its card, clickable, named for a screen reader by
    its heading and tab."""
    look, icon = _look(entry)
    labs = help_pages.LABS_TOOLTIP if entry.labs else ""
    head, _labels = cards.card_head(_heading(entry), look, icon, entry.note, labs)
    head.pack_end(cards.tag(entry.tab, f"On Help's {entry.tab} tab"), False, False, 0)
    card = cards.card_box(head)
    text = cards.label(help_search.highlight(snippet(entry.text, query), query), "tt-card-text")
    text.get_style_context().add_class("tt-card-row")
    card.pack_start(text, False, False, 0)
    button = Gtk.Button()
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.get_style_context().add_class("tt-result")
    button.add(card)
    button.get_accessible().set_name(f"{_heading(entry)}, on the {entry.tab} tab")
    button.connect("clicked", lambda _b: on_pick(entry))
    return button


def page(found, query, on_pick):
    """The results page for *query*: the count, then a result for each of
    *found* (help_search.search's entries); *on_pick* gets the one picked."""
    children = [cards.heading(cards.escape(count_line(found, query)), "tt-page-title")]
    if not found:
        children.append(cards.paragraph("Try a shorter word, or another name for it."))
    children.extend(_result(entry, query, on_pick) for entry in found)
    return cards.page(children)
