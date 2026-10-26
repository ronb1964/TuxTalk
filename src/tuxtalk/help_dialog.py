"""The Help window, shared by the tray and Preferences: one tab along the
top for each situation, as help_text.py writes it for this computer, in
Preferences' look (ui_style.use_tuxtalk_style), so the two windows match.

Tabs can't wrap to a second row in GTK 3, so each section added makes the
row wider; on a screen too narrow for it the row scrolls with arrows.
"""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, Gtk, Pango

from . import help_text, ui_style
from .help_text import FEEDBACK_URL, PROBLEMS_TITLE, REPORT_LINK

# Help's text is broken by hand at about 85 characters, so the page is this
# many of the font's characters wide, and the window that plus _FRAME_PX of
# margins and scroll bar, or the row of tabs if that is wider.
_LINE_CHARS = 87
_FRAME_PX = 60
HEIGHT = 600
# Room left beside Help on a screen too narrow for it.
_SCREEN_MARGIN_PX = 40

# The keys that scroll a page while its text has the focus. A selectable
# label would move its cursor on most of them, or move the focus away.
_SCROLL_KEYS = {
    Gdk.KEY_Page_Down: Gtk.ScrollType.PAGE_FORWARD,
    Gdk.KEY_Page_Up: Gtk.ScrollType.PAGE_BACKWARD,
    Gdk.KEY_Down: Gtk.ScrollType.STEP_FORWARD,
    Gdk.KEY_Up: Gtk.ScrollType.STEP_BACKWARD,
    Gdk.KEY_End: Gtk.ScrollType.END,
    Gdk.KEY_Home: Gtk.ScrollType.START,
}


def _scroll(label, event, scrolled):
    """A scrolling key pressed in a page's text: scroll the page."""
    scroll = _SCROLL_KEYS.get(event.keyval)
    if scroll is None or event.state & Gdk.ModifierType.CONTROL_MASK:
        return False
    scrolled.emit("scroll-child", scroll, False)
    return True


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


def _page(markup_text):
    """A section's text: *markup_text* in a wrapping label that scrolls. The
    label takes the keyboard focus (Tab from the list), so the arrow keys,
    Page Up and Down, Home and End scroll it, and a screen reader reads it."""
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    label = Gtk.Label()
    label.set_markup(markup_text)
    label.connect("activate-link", _on_link)
    label.set_line_wrap(True)
    label.set_xalign(0)
    label.set_valign(Gtk.Align.START)
    label.set_selectable(True)
    # A selectable label selects all of itself when focused; reading it
    # shouldn't leave the whole page highlighted.
    label.connect_after("grab-focus", lambda lbl: lbl.select_region(0, 0))
    for side in ("top", "bottom", "start", "end"):
        getattr(label, f"set_margin_{side}")(15 if side in ("top", "bottom") else 8)
    label.connect("key-press-event", _scroll, scrolled)
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
    page.pack_start(_page(markup), True, True, 0)
    return page


def _sections():
    """The notebook of sections, one tab each, in Help's order."""
    notebook = Gtk.Notebook()
    for title, markup in help_text.tabs():
        page = _problems_page(markup) if title == PROBLEMS_TITLE else _page(markup)
        notebook.append_page(page, Gtk.Label(label=title))
    notebook.get_accessible().set_name("Help sections")
    return notebook


def _fit(dialog, notebook):
    """Size Help: a page _LINE_CHARS wide, or the row of tabs if wider. On a
    screen narrower than that (large text, a small or portrait screen) Help
    takes the screen less a margin, its lines wrap and the tabs scroll."""
    context = dialog.get_pango_context()
    char = context.get_metrics(context.get_font_description(), None).get_approximate_char_width()
    tabs = notebook.get_preferred_width()[0] + _FRAME_PX
    wanted = max(int(_LINE_CHARS * char / Pango.SCALE + _FRAME_PX), tabs)
    screen = ui_style.workarea_width()
    if screen and wanted > screen - _SCREEN_MARGIN_PX:
        notebook.set_scrollable(True)
        wanted = screen - _SCREEN_MARGIN_PX
    dialog.set_default_size(wanted, HEIGHT)


def open_section(notebook, word):
    """Show the section whose title contains *word* ("writing"), if any."""
    for number in range(notebook.get_n_pages()):
        if word.lower() in notebook.get_tab_label_text(notebook.get_nth_page(number)).lower():
            notebook.set_current_page(number)
            return


def show_help_dialog(tab=None):
    """Show Help, on its first section (Start Here), or on the section whose
    title contains *tab* ("writing" from Preferences' TuxTalk Labs section).
    Returns the dialog."""
    dialog = Gtk.Dialog(title="TuxTalk Help")
    dialog.set_resizable(True)
    dialog.set_modal(False)  # Non-modal so it works without parent window
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)  # Ensure it appears on top

    content = dialog.get_content_area()
    for side in ("top", "bottom", "start", "end"):
        getattr(content, f"set_margin_{side}")(10)

    ui_style.use_tuxtalk_style(dialog)
    title = Gtk.Label()
    title.set_markup("<big><b>TuxTalk Help</b></big>")
    content.pack_start(title, False, False, 6)
    notebook = _sections()
    content.pack_start(notebook, True, True, 0)

    # A passive feedback link under the sections, so it's there whichever
    # one is open. Help never asks for feedback on its own; this is only for
    # people who go looking. Not an action-area button: those emit a
    # response, and the response handler below closes Help.
    feedback_link = Gtk.LinkButton.new_with_label(
        FEEDBACK_URL, "Questions or feedback? Leave a note on TuxTalk Discussions")
    feedback_link.set_halign(Gtk.Align.START)
    feedback_link.set_margin_top(6)
    content.pack_start(feedback_link, False, False, 0)

    close_button = Gtk.Button(label="Close")
    close_button.connect("clicked", lambda w: dialog.destroy())
    dialog.add_action_widget(close_button, Gtk.ResponseType.CLOSE)
    dialog.connect("response", lambda d, r: d.destroy())

    content.show_all()      # a hidden widget asks for no width
    _fit(dialog, notebook)
    if tab:
        open_section(notebook, tab)
    # The keyboard and a screen reader start on the tabs, at the section
    # shown: the arrows change section and Tab goes into its text.
    notebook.grab_focus()
    dialog.show_all()
    dialog.present()  # Bring to front and focus
    return dialog
