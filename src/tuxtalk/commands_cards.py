"""The voice commands as cards, for Help's Commands tab and the Voice Commands
window: one card per section of commands_reference, with a coloured heading
band, an icon from the desktop's icon theme, a Labs tag where the commands
need TuxTalk Labs, and two columns, what to say and what it does. A run of
plain lines let the eye slide past every heading; a band of colour per
group, the same in both windows, stops it at each one.

Each card takes the keyboard focus, so Tab goes card by card, and a screen
reader hears its heading and its commands.

The look is in prefs_style.css (the tt-* classes); the windows using these
pages carry its class (ui_style.use_tuxtalk_style).
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Atk, GLib, Gtk, Pango

from . import commands_reference as ref
from . import ui_style

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
    boxes = {}
    for section in sections:
        if section.labs:
            boxes.setdefault(section.labs, []).append(section.title)
    if not boxes:
        return ""
    if len(boxes) == 1:
        need = f"<b>{_escape(next(iter(boxes)))}</b>"
    else:
        need = " and ".join(f"<b>{_escape(box)}</b> ({_escape(', '.join(titles))})"
                            for box, titles in boxes.items())
    return (f"<b>Labs</b>: the cards tagged Labs need {need} ticked in "
            "<b>Preferences → Advanced → TuxTalk Labs</b>.")


def _escape(text):
    return GLib.markup_escape_text(text)


def _plain(markup):
    """*markup* as the words a screen reader says."""
    return Pango.parse_markup(markup, -1, "\0")[2]


def _label(markup, style, wrap=True):
    """A left-aligned label of *markup* carrying the class *style*."""
    label = Gtk.Label(xalign=0)
    label.set_markup(markup)
    label.set_line_wrap(wrap)
    label.get_style_context().add_class(style)
    return label


def paragraph(markup):
    """A paragraph over or between the cards. It takes the keyboard focus, as
    Help's other pages do, so Tab and a screen reader reach it too."""
    label = _label(markup, "tt-intro")
    ui_style.selectable_text(label)
    return label


def _heading(markup, style):
    """A label a screen reader announces as a heading."""
    label = _label(markup, style, wrap=False)
    label.get_accessible().set_role(Atk.Role.HEADING)
    return label


def _row(command, say_width):
    """One command: the ways to say it, bold, beside what it does (and its
    note under that)."""
    said = ", ".join(f"<b>{_escape(p)}</b>" for p in command.said)
    say = _label(said, "tt-say")
    say.set_width_chars(_SAY_CHARS)
    say.set_max_width_chars(_SAY_CHARS)
    say.set_valign(Gtk.Align.START)
    say_width.add_widget(say)
    does = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    does.pack_start(_label(command.result, "tt-does"), False, False, 0)
    if command.note:
        does.pack_start(_label(command.note, "tt-note"), False, False, 0)
    row = Gtk.Box(spacing=16)
    row.get_style_context().add_class("tt-card-row")
    row.pack_start(say, False, False, 0)
    row.pack_start(does, True, True, 0)
    return row


def _spoken(section):
    """What a screen reader says on *section*'s card: each command, what it
    does, and the notes, the text the card shows."""
    lines = [f"{', '.join(c.said)}: {_plain(c.result)}"
             + (f". {_plain(c.note)}" if c.note else "") for c in section.commands]
    if section.note:
        lines.append(_plain(ref.section_note(section)))
    return ".\n".join(lines)


def section_card(section, say_width):
    """The card of *section*: its heading band, then a row per command, and
    its note; *say_width* lines the "what to say" column up across cards.
    The card takes the keyboard focus, named for a screen reader by its
    heading and read out as its commands: its rows are labels no key reaches."""
    look, icon = GROUP_LOOK[section.group]
    head = Gtk.Box(spacing=10)
    for style in ("tt-card-head", f"tt-{look}"):
        head.get_style_context().add_class(style)
    head.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR),
                    False, False, 0)
    head.pack_start(_heading(f'<span size="large"><b>{_escape(section.title)}</b></span>',
                             "tt-card-title"), False, False, 0)
    name = section.title
    if section.labs:
        tag = _label("Labs", "tt-labs", wrap=False)
        tag.set_tooltip_text(f"Needs {section.labs} ticked in "
                             "Preferences → Advanced → TuxTalk Labs")
        head.pack_end(tag, False, False, 0)
        name += f", needs Labs: {section.labs}"
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    rows.pack_start(head, False, False, 0)
    for command in section.commands:
        rows.pack_start(_row(command, say_width), False, False, 0)
    if section.note:
        note = _label(ref.section_note(section), "tt-note")
        note.get_style_context().add_class("tt-card-row")
        rows.pack_start(note, False, False, 0)
    card = Gtk.EventBox(can_focus=True)
    card.get_style_context().add_class("tt-card")
    card.add(rows)
    card.get_accessible().set_name(name)
    card.get_accessible().set_description(_spoken(section))
    return card


def _page(children):
    """A scrolling page of *children*, top to bottom. Tab moves through its
    cards and paragraphs, scrolling to each, and the arrow keys, Page Up and
    Down, Home and End scroll it, as on Help's other pages: without
    scroll_with_keys an arrow would hop to the next card."""
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    for side in ("top", "bottom", "start", "end"):
        getattr(column, f"set_margin_{side}")(12)
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    for child in children:
        column.pack_start(child, False, False, 0)
        if child.get_can_focus():
            child.connect("key-press-event", ui_style.scroll_with_keys, scrolled)
    scrolled.add(column)
    return scrolled


def _group_children(group, say_width):
    """*group*'s cards, under the line naming the Labs tickboxes they need."""
    sections = [s for s in ref.SECTIONS if s.group == group]
    children = [section_card(s, say_width) for s in sections]
    if line := labs_line(sections):
        children.insert(0, paragraph(line))
    return children


def group_page(group, extra=None):
    """The Voice Commands window's tab for *group*: its cards, then *extra*
    (markup)."""
    children = _group_children(group, Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL))
    if extra:
        children.append(paragraph(extra))
    return _page(children)


def all_groups_page(intro, outro):
    """Help's Commands tab: *intro* (markup), then each group under its own
    title in its colour, then *outro*."""
    say_width = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
    children = [paragraph(intro)]
    for group in ref.GROUPS:
        look, _icon = GROUP_LOOK[group]
        title = _heading(f'<span size="x-large"><b>{_escape(group)}</b></span>',
                         "tt-group-title")
        title.get_style_context().add_class(f"tt-{look}")
        children.append(title)
        children.extend(_group_children(group, say_width))
    children.append(paragraph(outro))
    return _page(children)
