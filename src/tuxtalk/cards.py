"""The card look Help and the Voice Commands window share: a page of cards,
each with a heading band in its group's colour, an icon from the desktop's
icon theme and a Labs tag where it needs TuxTalk Labs. A run of plain lines
let the eye slide past every heading; a band of colour stops it at each one.

commands_cards fills the cards with commands, help_pages with Help's text.
The look is in prefs_style.css (the tt-* classes); the windows using these
pages carry its class (ui_style.use_tuxtalk_style).
"""
import gi
gi.require_version("Atk", "1.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Atk, GLib, Gtk, Pango

from . import ui_style


def escape(text):
    """*text* safe inside Pango markup."""
    return GLib.markup_escape_text(text)


def plain(markup):
    """*markup* as the words a screen reader says."""
    return Pango.parse_markup(markup, -1, "\0")[2]


def label(markup, style, wrap=True):
    """A left-aligned label of *markup* carrying the class *style*."""
    widget = Gtk.Label(xalign=0)
    widget.set_markup(markup)
    widget.set_line_wrap(wrap)
    widget.get_style_context().add_class(style)
    return widget


def heading(markup, style):
    """A label a screen reader announces as a heading. It wraps, so a long
    one never holds a window wider than a narrow screen."""
    widget = label(markup, style)
    widget.get_accessible().set_role(Atk.Role.HEADING)
    return widget


def paragraph(markup):
    """A paragraph over or between the cards. It takes the keyboard focus, as
    Help's other pages do, so Tab and a screen reader reach it too."""
    widget = label(markup, "tt-intro")
    ui_style.selectable_text(widget)
    return widget


def page_title(title):
    """A page's title, over its cards."""
    return heading(f'<span size="xx-large"><b>{escape(title)}</b></span>', "tt-page-title")


def labs_tag(tooltip):
    """The small "Labs" tag of a heading; *tooltip* says where it's turned on."""
    tag = label("Labs", "tt-labs", wrap=False)
    tag.set_tooltip_text(tooltip)
    return tag


def card_head(title, look, icon, note="", labs=""):
    """A card's heading band: *icon*, *title* (plain text) and *note* (dim,
    beside it), in the colours of *look* (a tt-<look> class), and the Labs
    tag when *labs* (its tooltip) is given. Returns the band and its
    heading's labels (title, then note), which name the card's text."""
    head = Gtk.Box(spacing=10)
    for style in ("tt-card-head", f"tt-{look}"):
        head.get_style_context().add_class(style)
    head.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR),
                    False, False, 0)
    labels = [heading(f'<span size="large"><b>{escape(title)}</b></span>', "tt-card-title")]
    if note:
        labels.append(label(escape(note), "tt-card-note"))
    for widget in labels:
        head.pack_start(widget, False, False, 0)
    if labs:
        head.pack_end(labs_tag(labs), False, False, 0)
    return head, labels


def group_title(title, look, note="", labs=""):
    """The title over a run of cards (a group of commands, a Help feature),
    large, in the colour of *look*, with *note* dim beside it and the Labs
    tag when *labs* (its tooltip) is given. Returns the widget to put on the
    page and its labels (title, then note), as card_head does."""
    labels = [heading(f'<span size="x-large"><b>{escape(title)}</b></span>',
                      "tt-group-title")]
    labels[0].get_style_context().add_class(f"tt-{look}")
    if not (note or labs):
        return labels[0], labels
    row = Gtk.Box(spacing=10)
    row.get_style_context().add_class(f"tt-{look}")     # the note's and the tag's colour
    if note:
        labels.append(label(escape(note), "tt-card-note"))
    for widget in labels:
        row.pack_start(widget, False, False, 0)
    if labs:
        tag = labs_tag(labs)
        tag.set_valign(Gtk.Align.CENTER)
        row.pack_start(tag, False, False, 0)
    return row, labels


def card_box(head):
    """The card itself, *head* on top; its rows are packed under it."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.get_style_context().add_class("tt-card")
    box.pack_start(head, False, False, 0)
    return box


def page(children):
    """A scrolling page of *children*, top to bottom. Tab moves through its
    cards and paragraphs, scrolling to each, and the arrow keys, Page Up and
    Down, Home and End scroll it, as on Help's other pages: without
    scroll_with_keys an arrow would move a text's cursor or hop to the next
    card."""
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    for side in ("top", "bottom", "start", "end"):
        getattr(column, f"set_margin_{side}")(12)
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    for child in children:
        column.pack_start(child, False, False, 0)
    for widget in _focusable(column):
        widget.connect("key-press-event", ui_style.scroll_with_keys, scrolled)
    scrolled.add(column)
    return scrolled


def _focusable(widget):
    """*widget* and what's inside it that Tab can reach."""
    if widget.get_can_focus():
        yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from _focusable(child)
