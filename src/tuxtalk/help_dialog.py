"""The Help window, shared by the tray and Preferences: a list of sections
down the left, one for each situation, and the chosen one's text on the
right, as help_text.py writes it for this computer.

A list rather than tabs along the top: a row of tabs makes the window wider
with every section added, and GTK 3's tabs can't wrap to a second row.
"""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Pango

from . import help_text
from .help_text import FEEDBACK_URL

# Help's text is broken by hand at about 85 characters, so the page is this
# many of the font's characters wide, and the window that plus the section
# list and _FRAME_PX of margins and scroll bar: 730 to 850 px in the fonts
# measured (KDE's, GNOME's, Mint's, DejaVu), each narrower than the row of
# tabs made it before.
_LINE_CHARS = 87
_FRAME_PX = 60
HEIGHT = 600


def _page(markup_text):
    """A section's text: *markup_text* in a wrapping label that scrolls. The
    label takes the keyboard focus (Tab from the list), so Page Up and Page
    Down scroll it and a screen reader reads it."""
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
    scrolled.add(label)
    return scrolled


def _sections():
    """The section list and the stack of pages it switches between."""
    stack = Gtk.Stack()
    stack.set_transition_type(Gtk.StackTransitionType.NONE)
    for number, (title, markup) in enumerate(help_text.tabs()):
        stack.add_titled(_page(markup), f"section-{number}", title)
    sidebar = Gtk.StackSidebar()
    sidebar.set_stack(stack)
    sidebar.get_accessible().set_name("Help sections")
    return sidebar, stack


def _width(dialog, sidebar):
    """How wide Help opens: the section list beside a page _LINE_CHARS wide."""
    context = dialog.get_pango_context()
    char = context.get_metrics(context.get_font_description(), None).get_approximate_char_width()
    return int(sidebar.get_preferred_width()[1] + _LINE_CHARS * char / Pango.SCALE + _FRAME_PX)


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
    dialog.show_all()
    if tab:
        open_section(stack, tab)
    dialog.present()  # Bring to front and focus
    return dialog
