"""The welcome screen's Flatpak-only parts.

Inside the sandbox the dictation keys come from the desktop's GlobalShortcuts
portal, so the welcome screen asks the user to set them there instead of
running the typing setup. A desktop without that portal can never have a
dictation key in the Flatpak, and gets a stop screen pointing to the other
packages instead.
"""

import os

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from .logger import setup_logger

logger = setup_logger(__name__)

# Desktops whose portal backend (xdg-desktop-portal-xapp) does NOT implement
# GlobalShortcuts, so the Flatpak dictation hotkey can never register there.
_NO_GLOBALSHORTCUTS = {"XFCE", "CINNAMON", "X-CINNAMON", "MATE"}
_DESKTOP_NAMES = {
    "KDE": "KDE Plasma", "GNOME": "GNOME", "HYPRLAND": "Hyprland",
    "XFCE": "XFCE", "CINNAMON": "Cinnamon", "X-CINNAMON": "Cinnamon",
    "MATE": "MATE",
}
_DOWNLOADS_PAGE = "https://github.com/ronb1964/TuxTalk/releases"

# How to set the keys, by desktop: precise steps for KDE and GNOME, generic
# wording for other portal desktops (e.g. Hyprland).
_HOW_TO_SET = {
    "kde": ("To set your keys on KDE Plasma:",
            "When TuxTalk starts, KDE pops up a <b>“Global Shortcuts "
            "Requested”</b> window for <b>Hold to talk</b> and "
            "<b>Toggle dictation</b>. Click the button next to each, press "
            "the key you want (for example F8, then F9), and confirm.\n"
            "<i>You can change these any time later in "
            "System Settings → Keyboard → Shortcuts (under Applications).</i>"),
    "gnome": ("To set your keys on GNOME:",
              "When TuxTalk starts, GNOME pops up an <b>Add Keyboard "
              "Shortcuts</b> window listing <b>Hold to talk</b> and "
              "<b>Toggle dictation</b>. For each one you want to use:\n"
              "1. Click the <b>pencil</b> icon on that row.\n"
              "2. Click <b>Allow</b> on the “inhibit shortcuts” prompt "
              "(that’s just GNOME letting it capture your key).\n"
              "3. Press the key you want (for example F8, then F9).\n"
              "4. Click <b>Add</b> to save.\n"
              "<i>You can change these any time later in "
              "Settings → Keyboard → Keyboard Shortcuts.</i>"),
    "other": ("To set your keys:",
              "When TuxTalk starts (after you finish these screens), your "
              "desktop’s global-shortcuts prompt lets you bind <b>Hold to "
              "talk</b> and <b>Toggle dictation</b> — press the key you want "
              "for each when it appears.\n"
              "<i>If no prompt shows, you can assign them later in your "
              "desktop’s keyboard-shortcut settings, under <b>TuxTalk</b>.</i>"),
}

# The "type into other apps" consent (the RemoteDesktop portal) differs by
# desktop, and each has a gotcha worth calling out:
#  - GNOME defaults its "Allow Remote Interaction" switch to OFF; click
#    Share without it and dictation types nothing while beeps still play.
#  - KDE opens a fresh remote-control session per dictation, so it shows a
#    "Started Remote Desktop" popup EVERY time. "Allow restoring" only
#    stops the APPROVAL dialog re-asking — the popup is a separate KDE
#    notification, turned off in System Settings.
_PERMISSION_NOTE = {
    "kde": ("💡 The first time you dictate, KDE's <b>Remote Control</b> window "
            "asks to allow controlling input — check <b>Allow restoring on "
            "future sessions</b>, then <b>Approve</b> (a one-time OK). KDE also "
            "shows a small notification after each dictation; the <b>last setup "
            "screen</b> shows how to turn it off."),
    "gnome": ("💡 The first time you dictate, GNOME asks to allow typing into "
              "other apps. Turn <b>on</b> the <b>Allow Remote Interaction</b> "
              "switch, keep <b>Remember This Selection</b> checked, and click "
              "<b>Share</b> — just once."),
    "other": ("💡 The first time you dictate, your desktop will ask permission "
              "to type into other apps — approve it once."),
}


def globalshortcuts_available():
    """True/False whether the GlobalShortcuts portal is implemented on this
    system, or None if the check itself was inconclusive. The Flatpak hotkey
    needs this portal (KDE/GNOME/Hyprland have it; the xapp backend used by
    XFCE/Cinnamon/MATE does not)."""
    try:
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = bus.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.DBus.Introspectable", "Introspect",
            None, GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE, 1500, None)
        return "org.freedesktop.portal.GlobalShortcuts" in res.unpack()[0]
    except Exception as e:
        logger.info(f"GlobalShortcuts portal check inconclusive: {e}")
        return None


def shortcut_support():
    """Return (supported: bool, friendly_desktop_name: str) for the hotkey."""
    raw = os.environ.get("XDG_CURRENT_DESKTOP", "")
    base = raw.split(":")[0].upper() if raw else ""
    name = _DESKTOP_NAMES.get(base, (raw.split(":")[0] or "your desktop"))
    # Known-unsupported by name (also lets us preview the message via env).
    if base in _NO_GLOBALSHORTCUTS:
        return (False, name)
    # Otherwise trust a real portal check when it is conclusive.
    if globalshortcuts_available() is False:
        return (False, name)
    return (True, name)


def _text(markup, width=58, start=0, top=0):
    """A wrapped, left-aligned label for these sections."""
    label = Gtk.Label()
    label.set_markup(markup)
    label.set_halign(Gtk.Align.START)
    label.set_line_wrap(True)
    label.set_max_width_chars(width)
    label.set_margin_start(start)
    label.set_margin_top(top)
    return label


def _framed(vbox, box):
    """Pack *box* into *vbox* inside a frame. The frame makes a step read as
    distinct and mandatory, not as one of the optional sections below it."""
    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
    frame.set_margin_top(6)
    frame.set_margin_bottom(6)
    frame.add(box)
    vbox.pack_start(frame, False, False, 0)


def _section_box():
    """The padded box a section's lines go in."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_margin_start(12)
    box.set_margin_end(12)
    box.set_margin_top(10)
    box.set_margin_bottom(10)
    return box


def build_unsupported_desktop_section(vbox, desktop_name, dialog):
    """Shown when the desktop lacks the GlobalShortcuts portal: the Flatpak
    hotkey cannot work, so point the user to the AppImage (Backend A/evdev).
    Returns the warning box that pulses red."""
    esc = GLib.markup_escape_text
    box = _section_box()

    # This is a hard stop: the app cannot function here, so the warning
    # pulses red so it can't be skipped.
    pulse = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    pulse.get_style_context().add_class('tt-required')
    pulse.pack_start(_text(
        f'<span size="large" foreground="#ffffff"><b>🛑 The dictation hotkey '
        f'won’t work on {esc(desktop_name)}</b></span>', width=54), False, False, 0)
    pulse.pack_start(_text(
        '<span size="medium" foreground="#ffffff">TuxTalk’s hotkey relies on '
        f'your desktop’s <b>global-shortcut</b> system, and <b>{esc(desktop_name)}'
        '</b> doesn’t provide it. The Flatpak will open, but the Hold-to-talk and '
        'Toggle keys can’t be registered — so there is no key to press.</span>',
        width=54, top=6), False, False, 0)
    box.pack_start(pulse, False, False, 0)

    box.pack_start(_text(
        '<span size="medium" foreground="#ffffff"><b>Good news — another '
        'TuxTalk package works on your desktop.</b></span> '
        '<span size="medium">Pick the one for your system; they all use a '
        'method that works everywhere:</span>', width=56, start=10, top=12), False, False, 0)
    box.pack_start(_text(
        '<span size="medium">'
        '• <b>AppImage</b> — runs on any Linux, nothing to install\n'
        '• <b>.deb</b> — Debian, Ubuntu, Linux Mint\n'
        '• <b>.rpm</b> — Fedora, openSUSE\n'
        '• <b>AUR</b> — Arch, CachyOS, EndeavourOS'
        '</span>', width=56, start=20, top=6), False, False, 0)

    btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    btn_box.set_margin_start(10)
    btn_box.set_margin_top(14)
    download_btn = Gtk.Button(label="⬇  Open the Downloads Page")
    download_btn.get_style_context().add_class("suggested-action")
    download_btn.connect("clicked", lambda _button: _open_downloads_page(dialog))
    btn_box.pack_start(download_btn, False, False, 0)
    box.pack_start(btn_box, False, False, 0)

    _framed(vbox, box)
    return pulse


def _open_downloads_page(dialog):
    """Open the TuxTalk releases page. Opening a URL from a Flatpak goes
    through the OpenURI portal — no special sandbox permission needed."""
    try:
        Gtk.show_uri_on_window(dialog, _DOWNLOADS_PAGE, Gdk.CURRENT_TIME)
    except Exception as e:
        logger.warning(f"Could not open releases page ({e}); URL: {_DOWNLOADS_PAGE}")


def _desktop_kind(has_gnome):
    """"kde", "gnome" or "other": which wording the shortcut steps use."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    if "KDE" in desktop:
        return "kde"
    if has_gnome or "GNOME" in desktop:
        return "gnome"
    return "other"


def _required_note():
    """The "set your key at the end" line, in the box the red pulse paints:
    a bare GtkLabel does not reliably render background-color in GTK3."""
    # The emoji triangle (⚠️, with the VS16 selector) renders in full colour like
    # the other icons; keep it OUTSIDE the white span or the colour override flattens it.
    must_do = _text(
        '<span size="x-large">⚠️</span>  '
        '<span size="medium" foreground="#ffffff"><b>You’ll set your key at '
        'the end: once you finish these setup screens, TuxTalk starts and '
        'your desktop pops up a window to bind it (about 30 seconds). It '
        'won’t dictate until you do.</b></span>', width=56)
    pulse = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    pulse.get_style_context().add_class('tt-required')
    pulse.pack_start(must_do, False, False, 0)
    pulse.set_margin_top(4)
    return pulse


def build_shortcut_setup_section(vbox, has_gnome):
    """Flatpak-only setup step: the desktop assigns the dictation shortcut.
    Returns the warning box that pulses red.

    Inside the sandbox typing works via the portal + libei path with no
    uinput/input-group setup, so the host "Typing Setup Required"
    step does not apply. The one thing the user must do is let their desktop
    bind a key to the TuxTalk dictate shortcuts. Desktops without the
    GlobalShortcuts portal never reach here: the welcome screen shows them
    the unsupported screen instead.
    """
    box = _section_box()
    header = Gtk.Label()
    header.set_markup('<span size="large"><b>🔴 Required: Set Your Dictation Key</b></span>')
    header.set_halign(Gtk.Align.START)
    box.pack_start(header, False, False, 0)
    pulse = _required_note()
    box.pack_start(pulse, False, False, 0)

    box.pack_start(_text(
        '<span size="medium">Typing into your apps then works automatically — no '
        'drivers, groups, or reboot needed. You just pick which key(s) start '
        'dictation. TuxTalk offers <b>two</b>, and you can set either or both:</span>',
        start=10, top=5), False, False, 0)
    box.pack_start(_text(
        '<span size="medium">'
        '🎤 <b>“Hold to talk”</b> — hold it while you speak (push-to-talk)\n'
        '🔁 <b>“Toggle dictation”</b> — tap once to start, tap again to stop'
        '</span>', start=20, top=8), False, False, 0)

    kind = _desktop_kind(has_gnome)
    how_title, steps = _HOW_TO_SET[kind]
    how_label = Gtk.Label()
    how_label.set_markup(f'<span size="medium"><b>{how_title}</b></span>')
    how_label.set_halign(Gtk.Align.START)
    how_label.set_margin_start(10)
    how_label.set_margin_top(12)
    box.pack_start(how_label, False, False, 0)
    box.pack_start(_text(f'<span size="medium">{steps}</span>', start=20, top=4),
                   False, False, 0)
    box.pack_start(_text(f'<span size="medium"><i>{_PERMISSION_NOTE[kind]}</i></span>',
                         start=10, top=10), False, False, 0)

    _framed(vbox, box)
    return pulse
