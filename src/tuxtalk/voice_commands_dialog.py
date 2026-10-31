"""
TuxTalk Voice Commands Quick Reference — a compact cheat sheet popup.

Triggered via tray menu → "Voice Commands..." or a configurable hotkey.
Designed to be glanceable: organized by category with clear formatting.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

from . import commands_reference
from .help_dialog import text_page
from .logger import setup_logger
from .ui_style import close_on_escape

logger = setup_logger(__name__)

# One window at a time: a second click brings the open one forward.
_active_dialog = None


def show_voice_commands_dialog():
    """Show the cheat sheet of every voice command, or bring the open one
    forward. Returns the window (None when one was already open)."""
    global _active_dialog
    if _active_dialog is not None:
        from .raise_window import raise_window
        raise_window(_active_dialog)
        return None

    dialog = Gtk.Dialog(title="Voice Commands — Quick Reference")
    dialog.set_default_size(520, 580)
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
    # Help's page: the text takes the focus, so the keys scroll it and a
    # screen reader reads it; Close is the only other thing to focus.
    content.pack_start(text_page(_build_markup()), True, True, 0)

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


def _build_markup():
    """The cheat sheet: the shared command table (commands_reference) in its
    short layout, then snippets."""
    return f'''<span size="large"><b>Voice Commands</b></span>
The commands are English words, whatever language you dictate in.

{commands_reference.short_markup()}

<b>Snippets</b>
Say a short phrase, get the full text. Add your own in Preferences → Words.
  Wrap the text in <b>"quotes"</b> to type it exactly as written
  (bypasses auto-capitalization and punctuation).'''
