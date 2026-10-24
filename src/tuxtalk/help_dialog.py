"""The Help window, shared by the tray and Preferences: one tab for each
situation, with the text help_text.py writes for this computer.
"""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from . import help_text, ui_style
from .help_text import FEEDBACK_URL


def show_help_dialog(tab=None):
    """Show help dialog with TuxTalk features and instructions. *tab* opens
    it on the tab whose title contains that word ("writing" from Preferences'
    TuxTalk Labs section). Returns the dialog."""
    from gi.repository import Gdk

    dialog = Gtk.Dialog(title="TuxTalk Help")
    dialog.set_default_size(760, 600)

    # Set minimum size but allow resizing larger
    geo = Gdk.Geometry()
    geo.min_width = 500
    geo.min_height = 400
    dialog.set_geometry_hints(None, geo, Gdk.WindowHints.MIN_SIZE)

    dialog.set_resizable(True)  # Allow resizing
    dialog.set_modal(False)  # Non-modal so it works without parent window
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)  # Ensure it appears on top

    content = dialog.get_content_area()
    content.set_margin_top(10)
    content.set_margin_bottom(10)
    content.set_margin_start(10)
    content.set_margin_end(10)

    # Create notebook (tabbed interface)
    notebook = Gtk.Notebook()
    notebook.set_tab_pos(Gtk.PositionType.TOP)
    content.pack_start(notebook, True, True, 0)

    def create_tab(title, markup_text):
        """Add a tab: *markup_text* in a wrapping label that scrolls."""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_margin_top(15)
        scrolled.set_margin_bottom(15)
        scrolled.set_margin_start(20)
        scrolled.set_margin_end(20)

        label = Gtk.Label()
        label.set_markup(markup_text)
        label.set_line_wrap(True)
        label.set_xalign(0)
        label.set_valign(Gtk.Align.START)

        scrolled.add(label)
        tab_label = Gtk.Label(label=title)
        tab_label.set_halign(Gtk.Align.CENTER)  # Center text within the tab
        notebook.append_page(scrolled, tab_label)

    for title, markup in help_text.tabs():
        create_tab(title, markup)

    # A passive feedback link under the tabs, so it's there whichever tab is
    # open. Help never asks for feedback on its own; this is only for people
    # who go looking. Not an action-area button: those emit a response, and
    # the response handler below closes Help.
    feedback_link = Gtk.LinkButton.new_with_label(
        FEEDBACK_URL, "Questions or feedback? Leave a note on TuxTalk Discussions")
    feedback_link.set_halign(Gtk.Align.START)
    feedback_link.set_margin_top(6)
    content.pack_start(feedback_link, False, False, 0)

    # Close button
    close_button = Gtk.Button(label="Close")
    close_button.connect("clicked", lambda w: dialog.destroy())
    dialog.add_action_widget(close_button, Gtk.ResponseType.CLOSE)

    # Connect response to destroy (for non-blocking operation)
    dialog.connect("response", lambda d, r: d.destroy())

    # Every tab in view, without GTK's scroll arrows: Mint's wider font needed
    # more than 760 px for the tab row. As wide as the tabs need, unless the
    # screen is narrower, where the arrows are the lesser evil.
    notebook.set_scrollable(False)
    content.show_all()      # a hidden widget asks for no width
    needed = dialog.get_preferred_width()[0]
    screen = ui_style.workarea_width()
    if screen and needed > screen:
        notebook.set_scrollable(True)
    else:
        dialog.set_default_size(max(760, needed), 600)

    dialog.show_all()
    if tab:
        for i in range(notebook.get_n_pages()):
            if tab.lower() in (notebook.get_tab_label_text(notebook.get_nth_page(i)) or "").lower():
                notebook.set_current_page(i)
                break
    dialog.present()  # Bring to front and focus
    return dialog
