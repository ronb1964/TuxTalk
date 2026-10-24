"""
TuxTalk Voice Commands Quick Reference — a compact cheat sheet popup.

Triggered via tray menu → "Voice Commands..." or a configurable hotkey.
Designed to be glanceable: organized by category with clear formatting.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

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
    try:
        from .config import load_config
        cfg = load_config()
        hotkey = getattr(cfg, 'voice_commands_hotkey', '')
        if hotkey:
            hint.set_markup(
                f'<span size="small" color="#888888">'
                f'Tip: Press <b>{hotkey}</b> anytime to open this reference'
                f'</span>'
            )
        else:
            hint.set_markup(
                '<span size="small" color="#888888">'
                'Tip: Set a hotkey in Preferences → General to open this with a keypress'
                '</span>'
            )
    except Exception:
        hint.set_markup(
            '<span size="small" color="#888888">'
            'Tip: Set a hotkey in Preferences → General to open this with a keypress'
            '</span>'
        )
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


def _on_key_press(dialog, event):
    """Close dialog on Escape key."""
    if event.keyval == Gdk.KEY_Escape:
        dialog.destroy()
        return True
    return False


def _build_markup():
    """Build the Pango markup for the cheat sheet content."""
    return '''<span size="large"><b>Voice Commands</b></span>
The commands are English words, whatever language you dictate in.

<b>Punctuation</b>
  <b>comma</b> → ,          <b>period</b> / <b>full stop</b> → .
  <b>question mark</b> → ?    <b>exclamation point</b> / <b>exclamation mark</b> → !
  <b>semicolon</b> → ;        <b>colon</b> → :
  <b>apostrophe</b> → '       <b>quote</b> → "
  <b>open quote</b> → \u201c         <b>close quote</b> → \u201d
  <b>hyphen</b> / <b>dash</b> → -    <b>em dash</b> → \u2014
  <b>ellipsis</b> / <b>dot dot dot</b> → \u2026

<b>Brackets</b>
  <b>open parenthesis</b> → (    <b>close parenthesis</b> → )
  <b>open bracket</b> → [       <b>close bracket</b> → ]
  <b>open brace</b> → {         <b>close brace</b> → }

<b>Formatting</b>
  <b>new line</b> / <b>newline</b> / <b>return</b> / <b>line break</b> → line break
  <b>new paragraph</b> / <b>paragraph break</b> → double line break
  <b>press tab</b> → tab character (just <b>tab</b> is the word)
  <b>press tab</b> at the end → presses Tab (the next box in a form; Help → Writing has the steps).
    In Claude Desktop, four spaces
  <b>soft break</b> → three spaces
  <b>press enter</b> / <b>hit enter</b> at the end → types it, then presses Enter (sends a message, runs a command)

<b>Undo</b>
  <b>delete last word</b> / <b>undo last word</b> / <b>remove last word</b> → delete last dictated word
  <b>delete last sentence</b> → delete back to previous sentence
  <b>delete last paragraph</b> → delete back to last line break
  <b>delete last 3 words</b> / <b>undo last two sentences</b> → counted undo (1–10 or digits)

<b>Clear the Whole Box</b>
  <b>delete everything</b> / <b>delete all</b> / <b>clear everything</b> / <b>clear all</b> /
  <b>undo everything</b> / <b>undo all</b> → empties the whole box, including text TuxTalk
  didn't type (Ctrl+A, Backspace); in a terminal, the line you're typing (Ctrl+E, Ctrl+U)

<b>Fixing a Word</b>
  <b>fix that word</b> / <b>fix that</b> → open Fix a Word on what you just said, to teach TuxTalk
  the right spelling for next time

<b>Editing by Voice</b> (TuxTalk Labs: turn on in Preferences → Advanced)
  Select text, hold <b>Shift</b> with your dictation key, and say the change:
  <b>make this more formal</b> / <b>fix the grammar</b> / <b>make this all caps</b> / <b>remove the commas</b>
  Right after dictating, no keys: <b>edit that, make it shorter</b>

<b>Select and Move</b> (TuxTalk Labs: turn on in Preferences → Advanced)
  <b>select that</b> / <b>select the last sentence</b> / <b>select the last 3 words</b> → select what you just said
  <b>select all</b> / <b>select the line</b> / <b>select the next word</b> / <b>select to the end of the line</b>
  <b>go to the end</b> / <b>go to the start of the line</b> / <b>go back 3 words</b> / <b>go up 2 lines</b>
  <b>unselect</b> → drop the selection

<b>Changing Your Mind</b>
Correct yourself mid-sentence with <b>no wait</b>, <b>I mean</b>, <b>make that</b>,
<b>scratch that</b> or <b>or rather</b>:
  "meet at 3, no wait, 4" → "meet at 4"
Needs <b>Fix self-corrections with AI</b> turned on in Preferences → Advanced
(a one-time download of about 1.1 GB).

<b>Literal Words</b>
Say <b>literal</b> or <b>the word</b> before a basic punctuation or line-break word
(period, comma, quote, dash, new line…) to type the word itself:
  <b>literal period</b> → "period"    <b>the word comma</b> → "comma"
  <b>literal new line</b> → "new line"

<b>Snippets</b>
Say a short phrase, get the full text. Add your own in Preferences → Words.
  Wrap the text in <b>"quotes"</b> to type it exactly as written
  (bypasses auto-capitalization and punctuation).'''
