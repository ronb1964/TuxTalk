"""The Help window, shared by the tray and Preferences: one tab along the
top for each situation, as help_text.py writes it for this computer, in
Preferences' look (ui_style.use_tuxtalk_style), so the two windows match.

Tabs can't wrap to a second row in GTK 3, so each section added makes the
row wider; on a screen too narrow for it the row scrolls with arrows.
"""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Pango

from . import commands_cards, help_text, ui_style
from .help_text import COMMANDS_TITLE, FEEDBACK_URL, PROBLEMS_TITLE, REPORT_LINK

# Help's text is broken by hand at about 85 characters, so the page is this
# many of the font's characters wide, and the window that plus _FRAME_PX of
# margins and scroll bar, or the row of tabs if that is wider.
_LINE_CHARS = 87
_FRAME_PX = 60
HEIGHT = 600
# Room left beside Help on a screen too narrow for it.
_SCREEN_MARGIN_PX = 40

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


def text_page(markup_text):
    """A page of text: *markup_text* in a wrapping label that scrolls (Help's
    sections but Commands). The label takes the keyboard focus,
    so the arrow keys, Page Up and Down, Home and End scroll it, and a screen
    reader reads it."""
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    label = Gtk.Label()
    label.set_markup(markup_text)
    label.connect("activate-link", _on_link)
    label.set_line_wrap(True)
    label.set_xalign(0)
    label.set_valign(Gtk.Align.START)
    ui_style.selectable_text(label)
    for side in ("top", "bottom", "start", "end"):
        getattr(label, f"set_margin_{side}")(15 if side in ("top", "bottom") else 8)
    label.connect("key-press-event", ui_style.scroll_with_keys, scrolled)
    scrolled.add(label)
    return scrolled


def _problems_page(markup):
    """The Problems section, with a Report a Problem button above its text:
    the first thing seen there, not a link near the bottom of the page."""
    report = Gtk.Button(label="Report a Problem\u2026")
    report.get_style_context().add_class("suggested-action")
    report.connect("clicked", _open_report)
    note = Gtk.Label(label="Gathers what helps find a problem: your system, your settings and "
                           "the end of TuxTalk's log. You see all of it first, then copy it, "
                           "save it, or open a GitHub issue with it.", xalign=0)
    note.set_line_wrap(True)
    note.get_style_context().add_class("dim-label")
    row = Gtk.Box(spacing=14)
    for side in ("top", "start", "end"):
        getattr(row, f"set_margin_{side}")(15 if side == "top" else 8)
    row.pack_start(report, False, False, 0)
    row.pack_start(note, True, True, 0)
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.pack_start(row, False, False, 0)
    page.pack_start(text_page(markup), True, True, 0)
    return page


def _add_sections(notebook, sections):
    """A page in *notebook* for each of *sections*, help_text.tabs()' list."""
    for title, markup in sections:
        if title == PROBLEMS_TITLE:
            page = _problems_page(markup)
        elif title == COMMANDS_TITLE:
            page = commands_cards.all_groups_page(markup, help_text.COMMANDS_EXAMPLES)
        else:
            page = text_page(markup)
        notebook.append_page(page, Gtk.Label(label=title))


def _sections(sections):
    """The notebook of sections, one tab each, in Help's order."""
    notebook = Gtk.Notebook()
    _add_sections(notebook, sections)
    notebook.get_accessible().set_name("Help sections")
    return notebook


def _rewrite(notebook, sections):
    """Write Help's pages again, on the section shown: a setting they name
    (the hold key) changed while Help stayed open."""
    shown = notebook.get_current_page()
    while notebook.get_n_pages():
        notebook.remove_page(-1)
    _add_sections(notebook, sections)
    notebook.show_all()     # GTK won't show a hidden page
    notebook.set_current_page(shown)
    notebook.grab_focus()


def _fit(window, notebook):
    """Size Help: a page _LINE_CHARS wide, or the row of tabs if wider. On a
    screen narrower than that (large text, a small or portrait screen) Help
    takes the screen less a margin, its lines wrap and the tabs scroll."""
    context = window.get_pango_context()
    char = context.get_metrics(context.get_font_description(), None).get_approximate_char_width()
    tabs = notebook.get_preferred_width()[0] + _FRAME_PX
    wanted = max(int(_LINE_CHARS * char / Pango.SCALE + _FRAME_PX), tabs)
    screen = ui_style.workarea_width()
    if screen and wanted > screen - _SCREEN_MARGIN_PX:
        notebook.set_scrollable(True)
        wanted = screen - _SCREEN_MARGIN_PX
    window.set_default_size(wanted, HEIGHT)


def open_section(notebook, word):
    """Show the section whose title contains *word* ("writing"), if any."""
    for number in range(notebook.get_n_pages()):
        if word.lower() in notebook.get_tab_label_text(notebook.get_nth_page(number)).lower():
            notebook.set_current_page(number)
            return


_open_help = None     # (window, notebook, its sections) while Help is open


def _bring_forward(tab):
    """Bring the open Help forward, on *tab* when one is named, its text up
    to date with the settings. Returns the window."""
    global _open_help
    window, notebook, built = _open_help
    sections = help_text.tabs()
    if sections != built:
        _rewrite(notebook, sections)
        _open_help = (window, notebook, sections)
    if tab:
        open_section(notebook, tab)
    from .raise_window import raise_window
    raise_window(window)
    return window


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
    ui_style.close_on_escape(window)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    for side in ("top", "bottom", "start", "end"):
        getattr(content, f"set_margin_{side}")(10)
    window.add(content)

    ui_style.use_tuxtalk_style(window)
    title = Gtk.Label()
    title.set_markup("<big><b>TuxTalk Help</b></big>")
    content.pack_start(title, False, False, 6)
    sections = help_text.tabs()
    notebook = _sections(sections)
    content.pack_start(notebook, True, True, 0)

    # A passive feedback link under the sections, so it's there whichever
    # one is open. Help never asks for feedback on its own; this is only for
    # people who go looking.
    feedback_link = Gtk.LinkButton.new_with_label(
        FEEDBACK_URL, "Questions or feedback? Leave a note on TuxTalk Discussions")
    feedback_link.set_halign(Gtk.Align.START)
    feedback_link.set_margin_top(6)
    content.pack_start(feedback_link, False, False, 0)

    close_button = Gtk.Button(label="Close")
    close_button.connect("clicked", lambda w: window.destroy())
    buttons = Gtk.Box()
    buttons.set_margin_top(6)
    buttons.pack_end(close_button, False, False, 0)
    content.pack_start(buttons, False, False, 0)

    content.show_all()      # a hidden widget asks for no width
    _fit(window, notebook)
    if tab:
        open_section(notebook, tab)
    # The keyboard and a screen reader start on the tabs, at the section
    # shown: the arrows change section and Tab goes into its text.
    notebook.grab_focus()
    _open_help = (window, notebook, sections)
    window.connect("destroy", _forget_help)
    window.show_all()
    window.present()  # Bring to front and focus
    return window


def _forget_help(_window):
    """Help was closed: the next open builds it again."""
    global _open_help
    _open_help = None
