"""The voice commands as cards (cards.py), for Help's Commands tab and the
Voice Commands window: one card per section of commands_reference, in its
group's colour, the same in both windows, with two columns, what to say and
what it does.

Each card takes the keyboard focus, so Tab goes card by card, and a screen
reader hears its heading and its commands.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from . import cards
from . import commands_reference as ref

# Each group's class in prefs_style.css (its colours) and its icon.
GROUP_LOOK = {
    ref.DICTATION: ("dictation", "format-text-bold-symbolic"),
    ref.FIXING: ("fixing", "edit-undo-symbolic"),
    ref.EDITING: ("editing", "edit-select-all-symbolic"),
    ref.APPS: ("apps", "window-new-symbolic"),
    ref.KEYS: ("keys", "input-keyboard-symbolic"),
    ref.SOUND: ("sound", "audio-volume-high-symbolic"),
}
# The width of the "what to say" column, in the font's characters.
_SAY_CHARS = 30


def labs_line(sections):
    """The line over a group's cards naming the TuxTalk Labs tickboxes its
    Labs cards need, or "" when none do."""
    boxes = dict.fromkeys(s.labs for s in sections if s.labs)
    if not boxes:
        return ""
    need = " and ".join(f"<b>{cards.escape(box)}</b>" for box in boxes)
    return (f"<b>Labs</b>: the cards tagged Labs need {need} ticked in "
            "<b>Preferences → Advanced → TuxTalk Labs</b>.")


def _row(command, say_width):
    """One command: the ways to say it, bold, beside what it does (and its
    note under that)."""
    said = ", ".join(f"<b>{cards.escape(p)}</b>" for p in command.said)
    say = cards.label(said, "tt-say")
    say.set_width_chars(_SAY_CHARS)
    say.set_max_width_chars(_SAY_CHARS)
    say.set_valign(Gtk.Align.START)
    say_width.add_widget(say)
    does = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    does.pack_start(cards.label(command.result, "tt-does"), False, False, 0)
    if command.note:
        does.pack_start(cards.label(command.note, "tt-note"), False, False, 0)
    row = Gtk.Box(spacing=16)
    row.get_style_context().add_class("tt-card-row")
    row.pack_start(say, False, False, 0)
    row.pack_start(does, True, True, 0)
    return row


def _spoken(section):
    """What a screen reader says on *section*'s card: each command, what it
    does, and the notes, the text the card shows."""
    lines = [f"{', '.join(c.said)}: {cards.plain(c.result)}"
             + (f". {cards.plain(c.note)}" if c.note else "") for c in section.commands]
    if section.note:
        lines.append(cards.plain(ref.section_note(section)))
    return ".\n".join(lines)


def section_card(section, say_width):
    """The card of *section*: its heading band, then a row per command, and
    its note; *say_width* lines the "what to say" column up across cards.
    The card takes the keyboard focus, named for a screen reader by its
    heading and read out as its commands: its rows are labels no key reaches."""
    look, icon = GROUP_LOOK[section.group]
    labs = (f"Needs {section.labs} ticked in Preferences → Advanced → TuxTalk Labs"
            if section.labs else "")
    head, _labels = cards.card_head(section.title, look, icon, labs=labs)
    rows = cards.card_box(head)
    for command in section.commands:
        rows.pack_start(_row(command, say_width), False, False, 0)
    if section.note:
        note = cards.label(ref.section_note(section), "tt-note")
        note.get_style_context().add_class("tt-card-row")
        rows.pack_start(note, False, False, 0)
    # Windowless: an EventBox with its own window drops the card's margin
    # and border, so the cards would touch. It only holds the focus.
    card = Gtk.EventBox(visible_window=False, can_focus=True)
    card.get_style_context().add_class("tt-card-focus")
    card.add(rows)
    name = section.title + (f", needs Labs: {section.labs}" if section.labs else "")
    card.get_accessible().set_name(name)
    card.get_accessible().set_description(_spoken(section))
    return card


def _group_children(group, say_width):
    """*group*'s cards, under the line naming the Labs tickboxes they need."""
    sections = [s for s in ref.SECTIONS if s.group == group]
    children = [section_card(s, say_width) for s in sections]
    if line := labs_line(sections):
        children.insert(0, cards.paragraph(line))
    return children


def group_page(group, extra=None):
    """The Voice Commands window's tab for *group*: its cards, then *extra*
    (markup)."""
    children = _group_children(group, Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL))
    if extra:
        children.append(cards.paragraph(extra))
    return cards.page(children)


def all_groups_page(title, intro, outro):
    """Help's Commands tab: its *title*, *intro* (markup), then each group
    under its own title in its colour, then *outro*."""
    say_width = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
    children = [cards.page_title(title), cards.paragraph(intro)]
    for group in ref.GROUPS:
        children.append(cards.group_title(group, GROUP_LOOK[group][0])[0])
        children.extend(_group_children(group, say_width))
    children.append(cards.paragraph(outro))
    return cards.page(children)
