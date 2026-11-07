"""Help's tabs as cards, the look of the Commands tab (cards.py).

help_text writes each tab as text with marks where its parts begin:
"# " the page's title (its first line), "## " a feature with cards of its
own (Voice Editing, Type at Pause), "### " a card. A heading that starts
with "🧪 " is a TuxTalk Labs one and shows the Labs tag; a "(...)" at its end
("Performance presets (TuxTalk menu → Performance)") shows dim beside it.
Text after a "## " and before its first card is that feature's
introduction. Lines broken by hand flow on (_text). parse() reads the marks,
page() builds the widgets.
"""
import re
from typing import NamedTuple

from . import cards, ui_style

LABS_MARK = "🧪 "
LABS_TOOLTIP = "A TuxTalk Labs feature: Preferences → Advanced → TuxTalk Labs"

# Each tab's colour (a tt-<look> class in prefs_style.css, the Commands
# groups' colours) and the icon on its cards. Mistakes shares Fixing's.
TAB_LOOK = {
    "Start Here": ("dictation", "audio-input-microphone-symbolic"),
    "Chatting": ("keys", "user-available-symbolic"),
    "Writing": ("editing", "document-edit-symbolic"),
    "Terminals": ("apps", "utilities-terminal-symbolic"),
    "Hands-free": ("sound", "media-playback-start-symbolic"),
    "Mistakes": ("fixing", "edit-undo-symbolic"),
    "Tips": ("dictation", "dialog-information-symbolic"),
    "Problems": ("sound", "dialog-warning-symbolic"),
}


class Section(NamedTuple):
    """A card: its heading, the dim "(...)" beside it, whether it's Labs,
    and its text (Pango markup)."""
    title: str
    note: str
    labs: bool
    body: str


class Part(NamedTuple):
    """A run of cards under a title of its own ("" for the cards before the
    first one), with the text that introduces them."""
    title: str
    labs: bool
    intro: str
    sections: list


class Page(NamedTuple):
    """A tab: its title and its parts."""
    title: str
    parts: list


_NOTE = re.compile(r"(.*?)\s+(\([^()]*\))$")


def _heading(text):
    """(title, note, labs) of a heading line's *text*."""
    labs = text.startswith(LABS_MARK)
    text = text[len(LABS_MARK):] if labs else text
    match = _NOTE.fullmatch(text)
    return (match[1], match[2], labs) if match else (text, "", labs)


def parse(text):
    """The Page *text* marks out (see the module's docstring)."""
    lines = text.split("\n")
    title = lines[0].removeprefix("# ")
    parts = [Part("", False, [], [])]
    body = parts[0].intro
    for line in lines[1:]:
        if line.startswith("## "):
            name, _note, labs = _heading(line[3:])
            parts.append(Part(name, labs, [], []))
            body = parts[-1].intro
        elif line.startswith("### "):
            name, note, labs = _heading(line[4:])
            parts[-1].sections.append(Section(name, note, labs, []))
            body = parts[-1].sections[-1].body
        else:
            body.append(line)
    return Page(title, [Part(p.title, p.labs, _text(p.intro),
                             [s._replace(body=_text(s.body)) for s in p.sections])
                        for p in parts if p.title or p.intro or p.sections])


# A line that starts an item of a list: a bullet, a dash or a numbered step.
_ITEM = re.compile(r"\s*(?:[•–]|\d+[.)])\s")


def _text(lines):
    """*lines* as one text for a card that is as wide as the window: a line
    broken by hand in the source carries on the one before it, while a blank
    line, a list's item and a numbered step start their own."""
    out = []
    for line in lines:
        if out and out[-1].strip() and line.strip() and not _ITEM.match(line):
            out[-1] = f"{out[-1].rstrip()} {line.strip()}"
        else:
            out.append(line.rstrip())
    return "\n".join(out).strip("\n")


def _card(section, look, icon, on_link):
    """*section*'s card. Its text takes the keyboard focus (it can be read,
    selected and its links followed), named by the heading for a screen
    reader, and the card is outlined while it has it."""
    labs = LABS_TOOLTIP if section.labs else ""
    card = cards.card_box(cards.card_head(section.title, look, icon, section.note, labs))
    text = cards.label(section.body, "tt-card-text")
    text.get_style_context().add_class("tt-card-row")
    ui_style.selectable_text(text)
    text.connect("activate-link", on_link)
    text.get_accessible().set_name(section.title)
    style = card.get_style_context()
    text.connect("focus-in-event", lambda *_: style.add_class("tt-has-focus"))
    text.connect("focus-out-event", lambda *_: style.remove_class("tt-has-focus"))
    card.pack_start(text, False, False, 0)
    return card


def page(text, tab, on_link, above=()):
    """The page of Help's tab *tab*, from its *text*: its title, then its
    cards in the tab's colour; *above* are widgets to put first (Problems'
    Report a Problem button); *on_link* handles a link's activate-link."""
    look, icon = TAB_LOOK[tab]
    content = parse(text)
    children = [*above, cards.page_title(content.title)]
    for part in content.parts:
        if part.title:
            children.append(cards.group_title(part.title, look,
                                              LABS_TOOLTIP if part.labs else ""))
        if part.intro:
            intro = cards.paragraph(part.intro)
            intro.connect("activate-link", on_link)
            children.append(intro)
        children.extend(_card(s, look, icon, on_link) for s in part.sections)
    return cards.page(children)
