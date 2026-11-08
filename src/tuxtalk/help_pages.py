"""Help's tabs as cards, the look of the Commands tab (cards.py), built from
the parts help_outline reads in help_text's marks.

Each card's text is one selectable label: Tab stops on it once, its words
can be selected and copied, its links followed, and a screen reader hears
the feature's title, the card's heading and its note before it, through
labelled-by relations (several cards are called "Good to know").
"""
import gi
gi.require_version("Atk", "1.0")
from gi.repository import Atk

from . import cards, help_outline, ui_style

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


def _labelled_by(widget, labels):
    """Tell a screen reader that *labels*, in order, name *widget*."""
    accessible = widget.get_accessible()
    for label in labels:
        accessible.add_relationship(Atk.RelationType.LABELLED_BY, label.get_accessible())
        label.get_accessible().add_relationship(Atk.RelationType.LABEL_FOR, accessible)


def _card(section, look, icon, on_link, part_labels):
    """*section*'s card, its text named by *part_labels* (the feature's
    title and note, if any) and its heading; outlined while the text has
    the focus."""
    labs = LABS_TOOLTIP if section.labs else ""
    head, head_labels = cards.card_head(section.title, look, icon, section.note, labs)
    card = cards.card_box(head)
    text = cards.label(section.body, "tt-card-text")
    text.get_style_context().add_class("tt-card-row")
    ui_style.selectable_text(text)
    text.connect("activate-link", on_link)
    _labelled_by(text, [*part_labels, *head_labels])
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
    content = help_outline.parse(text)
    children = [*above, cards.page_title(content.title)]
    for part in content.parts:
        part_labels = []
        if part.title:
            title, part_labels = cards.group_title(
                part.title, look, part.note, LABS_TOOLTIP if part.labs else "")
            children.append(title)
        if part.intro:
            intro = cards.paragraph(part.intro)
            intro.connect("activate-link", on_link)
            # It takes the focus: a screen reader names the feature before it.
            _labelled_by(intro, part_labels)
            children.append(intro)
        children.extend(_card(s, look, icon, on_link, part_labels) for s in part.sections)
    return cards.page(children)
