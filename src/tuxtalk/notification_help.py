"""A small, always-on-top window that walks a KDE user through turning off
the per-dictation "Remote control session started" popup.

The popup only appears after setup, when the user first dictates, so the steps
have to stay on screen while they work through System Settings. This window
floats above it and can be reopened any time from Preferences.
"""
import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

logger = logging.getLogger(__name__)

# Single shared instance — reopening just re-presents the existing window rather
# than stacking duplicates.
_window = None
# Guard so the (screen-wide) opaque-background CSS is installed only once.
_css_installed = False

# Bare Gtk.Windows render TRANSLUCENT under KDE's Breeze-GTK theme, which makes
# the text unreadable over whatever is behind it. Force the theme's own (opaque)
# window background so it's solid in both light and dark.
_CSS = b"""
.tt-notif-help { background-color: @theme_bg_color; }
"""

_STEPS = [
    "Dictate once — the <b>“Remote control session started”</b> popup appears.",
    "Click the <b>sliders / settings icon</b> on that popup (top-right).",
    "In the settings window, find <b>“Started Remote Desktop”</b>.",
    "Uncheck <b>“Show a message in a pop-up”</b>, then click <b>Apply</b>.",
]


def _install_css_once():
    """Install the opaque-background CSS for the whole screen, once."""
    global _css_installed
    if _css_installed:
        return
    try:
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _css_installed = True
    except Exception as e:
        logger.info(f"The notification help window's style didn't load: {e}")


def show_notification_help_window(parent=None):
    """Show (or re-present) the floating 'turn off the KDE notification' guide.

    Non-modal and kept above other windows so it stays visible over System
    Settings while the user follows the steps. Never raises — a UI failure here
    must not take down the caller (onboarding or Preferences)."""
    global _window
    try:
        if _window is not None:
            _window.present()
            return _window

        _install_css_once()

        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_title("Turn Off the Dictation Notification")
        win.set_default_size(420, -1)
        win.set_resizable(True)
        win.set_keep_above(True)  # float over System Settings
        win.set_position(Gtk.WindowPosition.CENTER)
        win.get_style_context().add_class("tt-notif-help")
        if parent is not None:
            win.set_transient_for(parent)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(14)
        outer.set_margin_bottom(14)
        outer.set_margin_start(16)
        outer.set_margin_end(16)

        header = Gtk.Label()
        header.set_markup('<b>🔔 Turn off the dictation notification</b>')
        header.set_halign(Gtk.Align.START)
        header.set_line_wrap(True)
        outer.pack_start(header, False, False, 0)

        intro = Gtk.Label()
        intro.set_markup(
            'KDE pops a <b>“Remote control session started”</b> notice after every '
            'dictation. To switch it off (keep this window open — it stays on top):'
        )
        intro.set_halign(Gtk.Align.START)
        intro.set_line_wrap(True)
        intro.set_max_width_chars(46)
        intro.set_xalign(0)
        outer.pack_start(intro, False, False, 0)

        steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for i, step in enumerate(_STEPS, start=1):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            num = Gtk.Label()
            num.set_markup(f'<b>{i}.</b>')
            num.set_valign(Gtk.Align.START)
            row.pack_start(num, False, False, 0)
            text = Gtk.Label()
            text.set_markup(step)
            text.set_halign(Gtk.Align.START)
            text.set_line_wrap(True)
            text.set_max_width_chars(44)
            text.set_xalign(0)
            row.pack_start(text, True, True, 0)
            steps_box.pack_start(row, False, False, 0)
        outer.pack_start(steps_box, False, False, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        note = Gtk.Label()
        note.set_markup(
            '<span size="small">Optional — reopen any time from Preferences.</span>')
        note.set_halign(Gtk.Align.START)
        note.set_opacity(0.8)
        footer.pack_start(note, True, True, 0)
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _b: win.destroy())
        footer.pack_start(close_btn, False, False, 0)
        outer.pack_start(footer, False, False, 0)

        win.add(outer)

        def _on_destroy(*_a):
            global _window
            _window = None
        win.connect("destroy", _on_destroy)

        win.show_all()
        _window = win
        return win
    except Exception as e:
        logger.warning(f"Could not show notification help window: {e}")
        return None
