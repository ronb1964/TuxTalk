"""The Voice Commands window: every voice command, a tab per group, in the
same cards as Help's Commands tab (commands_cards), so each group's heading
stands out and nothing has to be scrolled past to find it.

Opened from the tray menu → Voice Commands..., its key, or by saying "what
can I say".
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

from . import cards, commands_cards, commands_reference, ui_style
from .logger import setup_logger
from .ui_style import close_on_escape

logger = setup_logger(__name__)

# One window at a time: a second click brings the open one forward.
_active_dialog = None

WIDTH, HEIGHT = 900, 640

INTRO = ("Hold your dictation key, say the command, let go. The commands are English "
         "words, whatever language you dictate in.")
SNIPPETS = ("<b>Snippets</b>: say a short phrase, get the full text. Add your own in "
            "<b>Preferences → Words</b>; wrap the text in <b>\"quotes\"</b> to type it "
            "exactly as written.")


def show_voice_commands_dialog():
    """Show the cheat sheet of every voice command, or bring the open one
    forward. Returns the window (None when one was already open)."""
    global _active_dialog
    if _active_dialog is not None:
        from .raise_window import raise_window
        raise_window(_active_dialog)
        return None

    dialog = Gtk.Dialog(title="Voice Commands")
    dialog.set_default_size(ui_style.width_on_screen(WIDTH), HEIGHT)
    ui_style.use_tuxtalk_style(dialog)
    dialog.set_resizable(True)
    dialog.set_modal(False)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)
    close_on_escape(dialog)

    content = dialog.get_content_area()
    content.set_margin_top(8)
    content.set_margin_bottom(8)
    content.set_margin_start(12)
    content.set_margin_end(12)
    content.pack_start(cards.paragraph(INTRO), False, False, 0)
    tabs = _group_tabs()
    content.pack_start(tabs, True, True, 0)

    hint = Gtk.Label()
    hint.set_markup(f'<span size="small" color="#888888">{_hint_markup()}</span>')
    hint.set_xalign(0.5)
    hint.set_margin_top(6)
    hint.set_margin_bottom(4)
    content.pack_start(hint, False, False, 0)

    dialog.add_action_widget(Gtk.Button(label="Close"), Gtk.ResponseType.CLOSE)
    dialog.connect("response", lambda d, r: d.destroy())

    def _on_destroy(widget):
        global _active_dialog
        _active_dialog = None
    dialog.connect("destroy", _on_destroy)

    _active_dialog = dialog
    dialog.show_all()
    # On the tabs, as in Help: the intro, which can be selected, would take
    # the focus first, and Left and Right would move a cursor in it.
    tabs.grab_focus()
    dialog.present()
    return dialog


def _hint_markup():
    """The tip under the cheat sheet: the key that opens it, or where to set one."""
    try:
        from .config import load_config
        hotkey = getattr(load_config(), "voice_commands_hotkey", "")
    except Exception as e:
        logger.warning(f"Could not read the Voice Commands key from the settings: {e}")
        hotkey = ""
    if hotkey:
        return f"Tip: Press <b>{GLib.markup_escape_text(hotkey)}</b> anytime to open this reference"
    return "Tip: Set a hotkey in Preferences → General to open this with a keypress"


def _group_tabs():
    """A tab per group of commands, as Help's tabs look; the Dictation tab
    ends with how snippets work."""
    tabs = Gtk.Notebook()
    tabs.set_scrollable(True)
    tabs.get_accessible().set_name("Voice command groups")
    for group in commands_reference.GROUPS:
        extra = SNIPPETS if group == commands_reference.DICTATION else None
        tabs.append_page(commands_cards.group_page(group, extra), Gtk.Label(label=group))
    return tabs
