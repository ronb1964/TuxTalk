"""The Help window, shared by the tray and Preferences: one tab along the
top for each situation, as help_text.py writes it for this computer, its
topics as cards (help_pages; Commands: commands_cards), in Preferences'
look (ui_style.use_tuxtalk_style), so the two windows match.

A search box over the tabs finds a card by the words a person knows
(help_search), as a feature known by name has no tab of its own; while it
holds a word, the matches (help_results) stand in for the tabs.

Tabs can't wrap to a second row in GTK 3, so each section added makes the
row wider; on a screen too narrow for it the row scrolls with arrows.
"""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GLib, Gtk, Pango

from . import commands_cards, help_outline, help_pages, help_results, help_search, help_text
from . import ui_style
from .help_text import COMMANDS_TITLE, FEEDBACK_URL, PROBLEMS_TITLE, REPORT_LINK

# Help's text flows to the cards' width; a line about this many of the
# font's characters long reads easily, so the window is that plus
# _CARD_FRAME_PX of margins, the cards' borders and padding and the scroll
# bar, or the row of tabs plus _TABS_FRAME_PX if that is wider.
_LINE_CHARS = 87
_CARD_FRAME_PX = 100
_TABS_FRAME_PX = 60
HEIGHT = 600
# How long the card a search went to stays outlined, and how far below the
# top of the page it's put (room for the feature's title over it).
_FOUND_MS = 2500
_ABOVE_FOUND_PX = 40


def _open_report(_widget):
    """Open Report a Problem (a window of its own; see problem_report_window)."""
    from .problem_report_window import ReportWindow
    ReportWindow()


def _on_link(label, uri):
    """Help's Report a problem link opens its window; any other link is a
    web page, which GTK opens."""
    if uri != REPORT_LINK:
        return False
    _open_report(label)
    return True


def _problems_page(markup, found):
    """The Problems section, with a Report a Problem button above its cards:
    the first thing seen there, not a link near the bottom of the page."""
    report = Gtk.Button(label="Report a Problem…")
    report.get_style_context().add_class("suggested-action")
    report.connect("clicked", _open_report)
    note = Gtk.Label(label="Gathers what helps find a problem: your system, your settings and "
                           "the end of TuxTalk's log. You see all of it first, then copy it, "
                           "save it, or open a GitHub issue with it.", xalign=0)
    note.set_line_wrap(True)
    note.get_style_context().add_class("dim-label")
    row = Gtk.Box(spacing=14)
    row.set_margin_bottom(6)
    row.pack_start(report, False, False, 0)
    row.pack_start(note, True, True, 0)
    return help_pages.page(markup, PROBLEMS_TITLE, _on_link, above=[row], found=found)


def _add_sections(notebook, sections, found):
    """A page in *notebook* for each of *sections*, help_text.tabs()' list;
    *found* gets each card's (focus, card) widgets by (tab, feature, heading),
    as help_search's entries name them."""
    for title, markup in sections:
        cards_here = {}
        if title == PROBLEMS_TITLE:
            page = _problems_page(markup, cards_here)
        elif title == COMMANDS_TITLE:
            # Its cards are the commands: its text is only a title and an
            # intro (a test keeps it so).
            commands = help_outline.parse(markup)
            page = commands_cards.all_groups_page(
                commands.title, commands.parts[0].intro, help_text.COMMANDS_EXAMPLES,
                found=cards_here)
        else:
            page = help_pages.page(markup, title, _on_link, found=cards_here)
        found.update({(title, *key): widgets for key, widgets in cards_here.items()})
        notebook.append_page(page, Gtk.Label(label=title))


def _fit(window, notebook):
    """Size Help: a page _LINE_CHARS wide, or the row of tabs if wider. On a
    screen narrower than that (large text, a small or portrait screen) Help
    takes the screen less a margin, its lines wrap and the tabs scroll."""
    context = window.get_pango_context()
    char = context.get_metrics(context.get_font_description(), None).get_approximate_char_width()
    tabs = notebook.get_preferred_width()[0] + _TABS_FRAME_PX
    wanted = max(int(_LINE_CHARS * char / Pango.SCALE + _CARD_FRAME_PX), tabs)
    width = ui_style.width_on_screen(wanted)
    notebook.set_scrollable(width < wanted)
    window.set_default_size(width, HEIGHT)


def _page_of(notebook, title):
    """The number of *notebook*'s tab called *title*, or -1."""
    return next((number for number in range(notebook.get_n_pages())
                 if notebook.get_tab_label_text(notebook.get_nth_page(number)) == title), -1)


def open_section(notebook, word):
    """Show the section whose title contains *word* ("writing"), if any."""
    for number in range(notebook.get_n_pages()):
        if word.lower() in notebook.get_tab_label_text(notebook.get_nth_page(number)).lower():
            notebook.set_current_page(number)
            return


class _Help:
    """An open Help window's tabs, its search box and what the box searches:
    Help's text as it was last written for this computer."""

    def __init__(self, window, sections):
        self.window = window
        self.notebook = Gtk.Notebook()
        self.notebook.get_accessible().set_name("Help sections")
        self.box = Gtk.SearchEntry(placeholder_text="Search Help: a feature, a command, any word")
        self.box.get_accessible().set_name("Search Help")
        self.box.connect("changed", self._on_search)
        self.box.connect("activate", self._pick_first)
        self.stack = Gtk.Stack()
        self.stack.add_named(self.notebook, "tabs")
        self.results, self.matches = None, []
        self.write(sections)
        window.connect("key-press-event", self._on_key)

    def write(self, sections):
        """Write the tabs, and what the search box searches, from *sections*
        (help_text.tabs()), on the tab shown; a search shown is run again."""
        self.sections = sections
        self.entries = help_search.index(sections)
        self.found = {}
        shown = self.notebook.get_current_page()
        while self.notebook.get_n_pages():
            self.notebook.remove_page(-1)
        _add_sections(self.notebook, sections, self.found)
        self.notebook.show_all()     # GTK won't show a hidden page
        if shown >= 0:      # -1 when first written; GTK reads -1 as the last tab
            self.notebook.set_current_page(shown)
        self._on_search(self.box)

    def _on_search(self, _box):
        """Show the matches for the box's words, or the tabs when it's empty."""
        if self.results is not None:
            self.stack.remove(self.results)
            self.results = None
        query = self.box.get_text().strip()
        if not query:
            self.stack.set_visible_child_name("tabs")
            return
        self.matches = help_search.search(self.entries, query)
        self.results = help_results.page(self.matches, query, self.jump)
        self.results.show_all()
        self.stack.add_named(self.results, "results")
        self.stack.set_visible_child_name("results")

    def _pick_first(self, _box):
        """Enter in the box goes to the first match."""
        if self.matches and self.box.get_text().strip():
            self.jump(self.matches[0])

    def jump(self, entry):
        """Open *entry*'s tab at its card, outlined for a moment, with the
        keyboard (and a screen reader) on it; the box empties."""
        focus, card = self.found[(entry.tab, entry.part, entry.title)]
        self.box.set_text("")
        number = _page_of(self.notebook, entry.tab)
        self.notebook.set_current_page(number)
        style = card.get_style_context()
        style.add_class("tt-found")
        GLib.timeout_add(_FOUND_MS, lambda: style.remove_class("tt-found"))
        _show_found(self.notebook.get_nth_page(number), focus, card)

    def _on_key(self, _window, event):
        """Esc empties a search first and closes Help after; Ctrl+F, or a
        letter typed outside the box, goes to the box; Down from the box goes
        to the first match."""
        ctrl = event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK)
        in_box = self.window.get_focus() is self.box
        if event.keyval == Gdk.KEY_Escape:
            if self.box.get_text():
                self.box.set_text("")
            else:
                self.window.destroy()
            return True
        if ctrl and event.keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.box.grab_focus()
            return True
        if in_box and event.keyval == Gdk.KEY_Down and self.results is not None:
            return self.results.child_focus(Gtk.DirectionType.TAB_FORWARD)
        typed = chr(Gdk.keyval_to_unicode(event.keyval))
        if not ctrl and not in_box and typed.isprintable() and not typed.isspace():
            self.box.grab_focus_without_selecting()     # the key then goes into it
        return False


def _show_found(page, focus, card):
    """Put *card* near the top of *page* (a scrolled page of Help), with
    the keyboard on *focus*: focusing alone scrolls only as far as it must,
    leaving the card at the bottom. A tab just opened isn't laid out yet,
    and its scrolling then stops short: it's put there once it is."""
    focus.grab_focus()
    scroll = page.get_vadjustment()

    content = page.get_child().get_child()      # the column, inside the viewport

    def put(*_args):
        place = card.translate_coordinates(content, 0, 0)
        if place:
            scroll.set_value(max(place[1] - _ABOVE_FOUND_PX, 0))
    put()
    handler = scroll.connect("changed", put)
    # Only the first layout: later the person scrolls where they like.
    GLib.timeout_add(_FOUND_MS, lambda: scroll.disconnect(handler))


_open_help = None     # the _Help while Help is open


def open_help():
    """The open Help window's _Help, or None."""
    return _open_help


def _bring_forward(tab):
    """Bring the open Help forward, on *tab* when one is named, its text up
    to date with the settings. Returns the window."""
    sections = help_text.tabs()
    if sections != _open_help.sections:
        _open_help.write(sections)
        _open_help.notebook.grab_focus()
    if tab:
        open_section(_open_help.notebook, tab)
    from .raise_window import raise_window
    raise_window(_open_help.window)
    return _open_help.window


def _buttons(window):
    """The feedback link and the Close button under the sections. The link
    is passive, there whichever section is open: Help never asks for
    feedback on its own; this is only for people who go looking."""
    feedback_link = Gtk.LinkButton.new_with_label(
        FEEDBACK_URL, "Questions or feedback? Leave a note on TuxTalk Discussions")
    feedback_link.set_halign(Gtk.Align.START)
    feedback_link.set_margin_top(6)
    close_button = Gtk.Button(label="Close")
    close_button.connect("clicked", lambda w: window.destroy())
    buttons = Gtk.Box()
    buttons.set_margin_top(6)
    buttons.pack_end(close_button, False, False, 0)
    return feedback_link, buttons


def show_help_dialog(tab=None):
    """Show Help, on its first section (Start Here), or on the section whose
    title contains *tab* ("writing" from Preferences' TuxTalk Labs section).
    An open Help is brought forward instead (_bring_forward). Returns the
    window.

    A plain window, not a Gtk.Dialog: KWin keeps a dialog with no parent
    above every other window of its app, so the Report a Problem window it
    opens came up behind it."""
    global _open_help
    if _open_help is not None:
        return _bring_forward(tab)

    window = Gtk.Window(title="TuxTalk Help")
    window.set_position(Gtk.WindowPosition.CENTER)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    for side in ("top", "bottom", "start", "end"):
        getattr(content, f"set_margin_{side}")(10)
    window.add(content)

    ui_style.use_tuxtalk_style(window)
    title = Gtk.Label()
    title.set_markup("<big><b>TuxTalk Help</b></big>")
    content.pack_start(title, False, False, 6)
    help_window = _Help(window, help_text.tabs())
    help_window.box.set_margin_bottom(6)
    content.pack_start(help_window.box, False, False, 0)
    content.pack_start(help_window.stack, True, True, 0)
    for widget in _buttons(window):
        content.pack_start(widget, False, False, 0)

    content.show_all()      # a hidden widget asks for no width
    _fit(window, help_window.notebook)
    if tab:
        open_section(help_window.notebook, tab)
    _open_help = help_window
    window.connect("destroy", _forget_help)
    window.show_all()
    window.present()  # Bring to front and focus
    # The keyboard and a screen reader start on the tabs, at the section
    # shown: the arrows change section and Tab goes into its text; a letter
    # typed there starts a search. Once shown: a grab before can fail, and
    # GTK then starts on the search box, the first thing that takes focus.
    help_window.notebook.grab_focus()
    return window


def _forget_help(_window):
    """Help was closed: the next open builds it again."""
    global _open_help
    _open_help = None
