"""The Help window, shared by the tray and Preferences: a list of sections
down the left, one for each situation, and the chosen one's text on the
right, as help_text.py writes it for this computer.

A list rather than tabs along the top: a row of tabs makes the window wider
with every section added, and GTK 3's tabs can't wrap to a second row.
"""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, Gtk, Pango

from . import help_text, ui_style
from .help_text import FEEDBACK_URL

# Help's text is broken by hand at about 85 characters, so the page is this
# many of the font's characters wide, and the window that plus the section
# list and _FRAME_PX of margins and scroll bar: 730 to 850 px in the fonts
# measured (KDE's, GNOME's, Mint's, DejaVu), each narrower than the row of
# tabs made it before.
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


def _page(markup_text):
    """A section's text: *markup_text* in a wrapping label that scrolls. The
    label takes the keyboard focus (Tab from the list), so the arrow keys,
    Page Up and Down, Home and End scroll it, and a screen reader reads it."""
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    label = Gtk.Label()
    label.set_markup(markup_text)
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


def _text_of(page):
    """The label holding *page*'s text (inside the viewport its scrolled
    window wraps it in)."""
    return page.get_child().get_child()


def _list_of(sidebar):
    """The list box inside *sidebar*: the widget that takes the focus."""
    child = sidebar
    while not isinstance(child, Gtk.ListBox):
        child = child.get_child()
    return child


def _into_page(listbox, event, stack):
    """Tab in the section list goes into the open page's text. GTK 3's list
    takes Tab for the next row, and its selection follows the focus, so Tab
    alone would switch sections and never reach the text."""
    tab = event.keyval in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab)
    modified = event.state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK)
    if not tab or modified:
        return False
    _text_of(stack.get_visible_child()).grab_focus()
    return True


def _sections():
    """The section list and the stack of pages it switches between."""
    stack = Gtk.Stack()
    stack.set_transition_type(Gtk.StackTransitionType.NONE)
    for number, (title, markup) in enumerate(help_text.tabs()):
        stack.add_titled(_page(markup), f"section-{number}", title)
    sidebar = Gtk.StackSidebar()
    sidebar.set_stack(stack)
    listbox = _list_of(sidebar)
    listbox.get_accessible().set_name("Help sections")
    listbox.connect("key-press-event", _into_page, stack)
    return sidebar, stack


def _width(dialog, sidebar):
    """How wide Help opens: the section list beside a page _LINE_CHARS wide,
    or the screen's width less a margin if that is narrower (large text, a
    small or portrait screen), where the page's lines wrap."""
    context = dialog.get_pango_context()
    char = context.get_metrics(context.get_font_description(), None).get_approximate_char_width()
    wanted = int(sidebar.get_preferred_width()[1] + _LINE_CHARS * char / Pango.SCALE + _FRAME_PX)
    screen = ui_style.workarea_width()
    return min(wanted, screen - _SCREEN_MARGIN_PX) if screen else wanted


def open_section(stack, word):
    """Show the section whose title contains *word* ("writing"), if any."""
    for page in stack.get_children():
        if word.lower() in stack.child_get_property(page, "title").lower():
            stack.set_visible_child(page)
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

    sidebar, stack = _sections()
    body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    body.pack_start(sidebar, False, False, 0)
    body.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
    body.pack_start(stack, True, True, 0)
    content.pack_start(body, True, True, 0)

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
    dialog.set_default_size(_width(dialog, sidebar), HEIGHT)
    # Before the window shows: it puts the focus on the list's chosen row
    # then, so the keyboard and a screen reader start on the section shown,
    # not on Start Here.
    if tab:
        open_section(stack, tab)
    dialog.show_all()
    dialog.present()  # Bring to front and focus
    return dialog
