"""
TuxTalk Voice Commands Quick Reference — a compact cheat sheet popup.

Triggered via tray menu → "Voice Commands..." or a configurable hotkey.
Designed to be glanceable: organized by category with clear formatting.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

from . import commands_reference
from .logger import setup_logger

logger = setup_logger(__name__)

# Singleton guard — prevents multiple stacked dialogs
_active_dialog = None


def show_voice_commands_dialog():
    """Show a compact, quick-reference popup of all voice commands."""
    global _active_dialog
    if _active_dialog is not None:
        from .raise_window import raise_window
        raise_window(_active_dialog)
        return

    dialog = Gtk.Dialog(title="Voice Commands — Quick Reference")
    dialog.set_default_size(520, 580)
    dialog.set_resizable(True)
    dialog.set_modal(False)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)

    # Close on Escape key
    dialog.connect("key-press-event", _on_key_press)

    content = dialog.get_content_area()
    content.set_margin_top(8)
    content.set_margin_bottom(8)
    content.set_margin_start(12)
    content.set_margin_end(12)

    # Scrollable content area
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

    label = Gtk.Label()
    label.set_markup(_build_markup())
    label.set_line_wrap(True)
    label.set_xalign(0)
    label.set_valign(Gtk.Align.START)
    label.set_margin_top(8)
    label.set_margin_bottom(8)
    label.set_margin_start(12)
    label.set_margin_end(12)

    scrolled.add(label)
    content.pack_start(scrolled, True, True, 0)

    # Hotkey hint at the bottom
    hint = Gtk.Label()
    hint.set_markup(f'<span size="small" color="#888888">{_hint_markup()}</span>')
    hint.set_xalign(0.5)
    hint.set_margin_top(6)
    hint.set_margin_bottom(4)
    content.pack_start(hint, False, False, 0)

    # Close button
    close_btn = Gtk.Button(label="Close")
    close_btn.connect("clicked", lambda w: dialog.destroy())
    dialog.add_action_widget(close_btn, Gtk.ResponseType.CLOSE)
    dialog.connect("response", lambda d, r: d.destroy())

    # Clear singleton reference when dialog is closed
    def _on_destroy(widget):
        global _active_dialog
        _active_dialog = None
    dialog.connect("destroy", _on_destroy)

    _active_dialog = dialog
    dialog.show_all()
    dialog.present()


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


def _on_key_press(dialog, event):
    """Close dialog on Escape key."""
    if event.keyval == Gdk.KEY_Escape:
        dialog.destroy()
        return True
    return False


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
