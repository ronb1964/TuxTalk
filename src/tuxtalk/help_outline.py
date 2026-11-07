"""How Help's text marks out its cards, read without any window code.

help_text writes each tab as Pango markup with marks where its parts begin:
"# " the page's title (its first line), "## " a feature with cards of its
own (Voice Editing, Type at Pause), "### " a card. A heading that starts
with "🧪 " is a TuxTalk Labs one; a "(...)" at its end ("Performance presets
(TuxTalk menu → Performance)") is a note shown dim beside it. Text after a
"## " and before its first card introduces that feature. help_pages shows
the result.
"""
import html
import re
from typing import NamedTuple

LABS_MARK = "🧪 "


class Section(NamedTuple):
    """A card: its heading and note (plain text), whether it's Labs, and
    its text (Pango markup)."""
    title: str
    note: str
    labs: bool
    body: str


class Part(NamedTuple):
    """A run of cards under a title of its own ("" for the cards before the
    first one), with the text that introduces them."""
    title: str
    note: str
    labs: bool
    intro: str
    sections: tuple


class Page(NamedTuple):
    """A tab: its title and its parts."""
    title: str
    parts: tuple


_NOTE = re.compile(r"(.*?)\s+(\([^()]*\))")
# A line that starts an item of a list: a bullet, a dash or a numbered step.
_ITEM = re.compile(r"\s*(?:[•–]|\d+[.)])\s")


def _heading(markup):
    """(title, note, labs) of a heading line, as plain text: headings hold
    no tags, but a key name in one may be escaped ("&amp;")."""
    text = html.unescape(markup)
    labs = text.startswith(LABS_MARK)
    text = text.removeprefix(LABS_MARK)
    match = _NOTE.fullmatch(text)
    return (match[1], match[2], labs) if match else (text, "", labs)


def parse(text):
    """The Page *text* marks out (see the module's docstring)."""
    lines = text.split("\n")
    parts = [{"heading": ("", "", False), "intro": [], "sections": []}]
    body = parts[0]["intro"]
    for line in lines[1:]:
        if line.startswith("## "):
            parts.append({"heading": _heading(line[3:]), "intro": [], "sections": []})
            body = parts[-1]["intro"]
        elif line.startswith("### "):
            body = []
            parts[-1]["sections"].append((_heading(line[4:]), body))
        else:
            body.append(line)
    return Page(html.unescape(lines[0].removeprefix("# ")), tuple(
        Part(*part["heading"], _text(part["intro"]),
             tuple(Section(*heading, _text(lines)) for heading, lines in part["sections"]))
        for part in parts if part["heading"][0] or part["intro"] or part["sections"]))


def _text(lines):
    """*lines* as one text for a card as wide as the window: a line broken
    by hand carries on the one before it, in a paragraph and in a list's
    item alike, so both fill the card. A blank line, an item and a numbered
    step start their own. (Lined up under its text, a wrapped item filled
    only part of the card, and on a narrow screen left words stranded.)"""
    out = []
    for line in lines:
        if out and out[-1].strip() and line.strip() and not _ITEM.match(line):
            out[-1] = f"{out[-1].rstrip()} {line.strip()}"
        else:
            out.append(line.rstrip())
    return "\n".join(out).strip("\n")
