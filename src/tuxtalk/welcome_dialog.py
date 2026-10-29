#!/usr/bin/env python3
"""
The first-run welcome screen: what TuxTalk does, the setup recommended for
this computer, and the setup steps it still needs.

The welcome screen adapts to the computer: every one gets the
recommended-setup card; PortAudio and typing setup (or, in a Flatpak, the
shortcut step) appear only when needed, and the GNOME extension option only
on GNOME. A Flatpak on a desktop without the GlobalShortcuts portal gets a
stop screen instead.
"""

import math
import os
import threading
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib
from .ui_style import Fold, message_dialog, more_below_bar, show_message

from .flatpak_welcome import (build_shortcut_setup_section, build_unsupported_desktop_section,
                              shortcut_support)
from .logger import setup_logger
from .splash_screen import SplashScreen, fade_in
from .uinput_helper import (
    check_all_device_permissions,
    get_portaudio_status,
)

logger = setup_logger(__name__)

# Checks both halves: writing keystrokes (/dev/uinput) and reading hotkeys
# (/dev/input). Some systems grant uinput through a logind ACL but not 'input'
# group membership, so uinput alone says nothing about hotkeys.
# Returns (has_access, reason).
detect_uinput_access = check_all_device_permissions
# A dict with installed, needs_install, message (PortAudio records the audio).
detect_portaudio_status = get_portaudio_status


# The welcome screen's styles. They go on the whole screen (_install_css), so
# every rule uses a class only the welcome screen's widgets carry.
_CSS = b"""
    .welcome-checkbox {
        padding: 8px;
        border-radius: 8px;
        transition: all 300ms ease-in-out;
    }
    .welcome-checkbox:hover {
        background-color: rgba(66, 133, 244, 0.15);
        box-shadow: 0 0 8px rgba(66, 133, 244, 0.3);
    }
    .welcome-checkbox:checked {
        background-color: rgba(66, 133, 244, 0.25);
        box-shadow: 0 0 12px rgba(66, 133, 244, 0.5);
    }
    /* Pulse effect with multiple states for smooth continuous animation */
    .welcome-checkbox.pulse-0 {
        background-color: rgba(66, 133, 244, 0.06);
        box-shadow: 0 0 8px rgba(66, 133, 244, 0.25);
    }
    .welcome-checkbox.pulse-1 {
        background-color: rgba(66, 133, 244, 0.075);
        box-shadow: 0 0 9px rgba(66, 133, 244, 0.3);
    }
    .welcome-checkbox.pulse-2 {
        background-color: rgba(66, 133, 244, 0.09);
        box-shadow: 0 0 11px rgba(66, 133, 244, 0.375);
    }
    .welcome-checkbox.pulse-3 {
        background-color: rgba(66, 133, 244, 0.105);
        box-shadow: 0 0 12px rgba(66, 133, 244, 0.425);
    }
    .welcome-checkbox.pulse-4 {
        background-color: rgba(66, 133, 244, 0.12);
        box-shadow: 0 0 14px rgba(66, 133, 244, 0.5);
    }
    /* Required-step (Flatpak shortcut setup): the SAME pulse-N levels, but
       red, so the "will NOT work until you do this" line pulses hard. */
    .tt-required {
        padding: 10px;
        border-radius: 8px;
    }
    .tt-required.pulse-0 {
        background-color: rgba(255, 80, 80, 0.10);
        box-shadow: 0 0 8px rgba(255, 80, 80, 0.20);
    }
    .tt-required.pulse-1 {
        background-color: rgba(255, 80, 80, 0.14);
        box-shadow: 0 0 10px rgba(255, 80, 80, 0.28);
    }
    .tt-required.pulse-2 {
        background-color: rgba(255, 80, 80, 0.18);
        box-shadow: 0 0 12px rgba(255, 80, 80, 0.36);
    }
    .tt-required.pulse-3 {
        background-color: rgba(255, 80, 80, 0.22);
        box-shadow: 0 0 14px rgba(255, 80, 80, 0.44);
    }
    .tt-required.pulse-4 {
        background-color: rgba(255, 80, 80, 0.28);
        box-shadow: 0 0 16px rgba(255, 80, 80, 0.55);
    }
    /* Neutral secondary button (Quit on the stop screen): a real button,
       but understated so it complements the red warning and blue primary
       instead of adding a third loud colour. */
    .tt-quit {
        background-image: none;
        background-color: rgba(255, 255, 255, 0.10);
        border: 1px solid rgba(255, 255, 255, 0.14);
        border-radius: 6px;
        padding: 6px 14px;
    }
    .tt-quit:hover {
        background-color: rgba(255, 255, 255, 0.17);
    }
    .tt-quit:active {
        background-color: rgba(255, 255, 255, 0.22);
    }
    .tt-rec-card {
        border: 2px solid #4a90e2;
        border-radius: 10px;
        padding: 12px 14px;
        background-color: rgba(74, 144, 226, 0.10);
    }
    .tt-rec-tag {
        color: #7fb2f0;
        font-weight: bold;
        font-size: 9pt;
    }
"""
_css_installed = False


def _install_css(screen):
    """Add the welcome screen's stylesheet to *screen*, once per process.
    Screen-wide, because a window's own provider never reaches the widgets
    inside it."""
    global _css_installed
    if _css_installed:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_installed = True


def _status_markup(text, color, max_lines=4):
    """Pango markup for a status label: escaped, and at most *max_lines* lines.

    The helpers' messages carry raw package-manager output: a '<' or '&'
    would make the markup invalid and blank the label, and dozens of lines
    would stretch the dialog off the screen. The full text is in the log.
    """
    lines = [line for line in str(text).splitlines() if line.strip()]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["…"]
    return f'<span color="{color}">{GLib.markup_escape_text(chr(10).join(lines))}</span>'


def _fix_typing_steps():
    """The blocking part of Fix Typing; runs off the GTK thread.

    Returns a dict: results (lines to show), uinput_fixed and needs_restart.
    """
    from tuxtalk.secure_input import fix_permissions
    logger.info("Setting up typing and hotkey permissions...")
    fixed, message, needs_restart = fix_permissions()
    if fixed:
        logger.info("Typing and hotkey permissions set up")
        results = ["✅ Device permissions configured"]
    else:
        logger.warning(f"Permission setup result: {message}")
        results = (["⚠️ Permissions setup cancelled"] if "cancelled" in message.lower()
                   else [f"❌ Permissions: {message}"])
    return {"results": results, "uinput_fixed": fixed,
            "needs_restart": needs_restart if fixed else False}


def _extension_already_active():
    """Whether TuxTalk's GNOME extension is installed and enabled."""
    try:
        from .extension_helper import is_extension_enabled
        return bool(is_extension_enabled())
    except Exception:
        return False


def menu_location():
    """Where this desktop shows TuxTalk's menu, for the welcome screen's and
    Setup Complete's tips: plain GNOME has no tray, so "the tray icon" sent
    people looking for nothing."""
    try:
        if _extension_already_active():
            return "its icon in the top bar"
        from .desktop_detect import tray_host_available
        if tray_host_available():
            return "its tray icon"
    except Exception as e:
        logger.info(f"Could not tell where TuxTalk's menu shows: {e}")
        return "its tray icon"
    return "open TuxTalk again from your apps"


def saved_hold_key():
    """The push-to-talk key in the settings, F8 when there is none yet."""
    try:
        from tuxtalk.config import load_config
        return load_config().hotkey or "F8"
    except Exception as e:
        logger.warning(f"Could not read the hold key from the settings: {e}")
        return "F8"


class WelcomeDialog:
    """
    Adaptive welcome dialog that shows appropriate content based on system detection.

    Every computer gets the recommended-setup card (language, model, graphics
    card). Setup sections for typing, PortAudio and the GNOME extension appear
    only when this computer needs them.
    """

    def __init__(self, parent=None, force_gnome=None):
        """
        Initialize the welcome dialog.

        Args:
            parent: Parent window (optional)
            force_gnome: Override GNOME detection for testing (None/True/False)
        """
        self.parent = parent

        # Detect system capabilities (or use forced values for testing)
        from .desktop_detect import is_gnome
        self.has_gnome = is_gnome() if force_gnome is None else force_gnome
        # The extension is offered only when it isn't already running;
        # updating an installed one is Preferences' job.
        self.offer_extension = self.has_gnome and not _extension_already_active()
        # What to recommend (recommend.py): the graphics chip and the language.
        from . import recommend as _rec
        try:
            from .config import load_config
            _cfg = load_config()
        except Exception:
            _cfg = None
        self.hardware = _rec.detect_hardware(_cfg)
        self.initial_language = _rec.system_language()
        self._chosen_model = None      # set when the user picks a model in Other options
        
        # Detect uinput access (for keystroke injection)
        has_uinput, _ = detect_uinput_access()
        self.needs_uinput_fix = not has_uinput

        # Detect PortAudio status (required for audio recording)
        self.portaudio_status = detect_portaudio_status()
        self.needs_portaudio_install = self.portaudio_status.get('needs_install', False)

        logger.info(f"PortAudio status: installed={self.portaudio_status.get('installed')}, "
                   f"needs_install={self.needs_portaudio_install}")

        # Inside a Flatpak, typing uses the portal + libei path (Backend B): there
        # is no uinput/input-group setup and no reboot. Showing the host
        # "Typing Setup Required" step there is wrong (and its fixes can't run in
        # the sandbox). Instead we show a short "Shortcut Setup" step explaining
        # that the DESKTOP assigns the dictation key.
        self.is_flatpak = bool(os.environ.get("FLATPAK_ID"))

        # In a Flatpak on a desktop without the GlobalShortcuts portal, the hotkey
        # can never work — a hard stop. Decide this up front so the dialog can be a
        # lean "use another package" screen instead of the full welcome.
        if self.is_flatpak:
            _supported, self.unsupported_desktop_name = shortcut_support()
            self.hotkey_unsupported = not _supported
        else:
            self.hotkey_unsupported = False
            self.unsupported_desktop_name = ""

        # Calculate height based on what sections we'll show
        if self.hotkey_unsupported:
            # Lean stop screen: header, the red stop box and Quit, since the app
            # can't run here. Sized so Quit is visible without dead space below.
            self.height = 700
        else:
            base_height = 700
            additional_height = 0
            if self.offer_extension:
                additional_height += 160  # GNOME extension section
            additional_height += 225  # recommendation card + its options button
            if self.is_flatpak:
                additional_height += 190  # Shortcut setup section (Flatpak)
            elif self.needs_uinput_fix:
                additional_height += 250  # Typing setup section
            # PortAudio setup section
            if self.needs_portaudio_install:
                additional_height += 180  # PortAudio section
            self.height = base_height + additional_height

        logger.info(f"Welcome dialog: GNOME={self.has_gnome}, NVIDIA={self.hardware.has_nvidia} "
                   f"(graphics: {self.hardware.gpu_name or 'none found'}), "
                   f"needs_uinput_fix={self.needs_uinput_fix}, needs_portaudio={self.needs_portaudio_install}")

        # Build the dialog
        self.dialog = None
        self.extension_check = None
        self.gpu_check = None  # "Use my graphics card" under Other options
        self.required_pulse_box = None  # Flatpak: the red pulsing "Required" warning box
        self.fix_typing_button = None
        self.typing_status_label = None
        self.uinput_fixed = False  # Track if user fixed uinput during this dialog
        # Only the "input" group needs a restart; the hotkey helper doesn't.
        self.restart_needed = False
        self.install_portaudio_button = None
        self.portaudio_status_label = None
        self.portaudio_fixed = False  # Track if user installed PortAudio during this dialog
        self._syncing = False  # True while _refresh_recommendation sets the model radios
        self._busy = 0  # installs running in the background (_run_in_background)
        self._job_lock = threading.Lock()  # one background install at a time
        self._closed = False  # set once the dialog has closed
        self.continue_button = None  # "Set it up"
        self._build_dialog()

    def _build_dialog(self):
        """Build the GTK dialog with appropriate content."""
        self.dialog = Gtk.Dialog(title="Welcome to TuxTalk!")
        # Resizable, and capped to the screen when it's created (the realize
        # hook's ui_style.fit_window_to_screen), so the footer (Set it up
        # button) is always reachable. The content below is in a
        # ScrolledWindow, so overflow scrolls instead of being cut off.
        from .ui_style import fit_dialog_to_screen
        fit_dialog_to_screen(self.dialog, 580, self.height)
        self.dialog.set_modal(True)
        self.dialog.set_position(Gtk.WindowPosition.CENTER)
        self.dialog.set_keep_above(True)

        if self.parent:
            self.dialog.set_transient_for(self.parent)
        _install_css(self.dialog.get_screen() or Gdk.Screen.get_default())

        content = self.dialog.get_content_area()
        scrolled, vbox = self._build_scroller(content)
        self._build_header(vbox)

        if self.hotkey_unsupported:
            # Hard stop: skip the welcome blurb, features, quick-start and all the
            # setup sections — the app can't run here. Go straight to the red stop
            # box and "get a package that works on your desktop".
            self.required_pulse_box = build_unsupported_desktop_section(
                vbox, self.unsupported_desktop_name, self.dialog)
        else:
            self._build_base_content(vbox)
            # Always: it holds the recommended-setup card, which every computer
            # needs (language, model, graphics card). Its other parts check
            # their own conditions.
            self._build_optional_features(vbox)

        self._build_footer_area(content, scrolled)
        # Left to GTK, the focus went to the first button in the scrolling
        # part ("Change language, model or graphics card"), and the window
        # opened scrolled down to it with its title cut off.
        if self.continue_button is not None:
            self.dialog.set_focus(self.continue_button)

    @staticmethod
    def _build_scroller(content):
        """(scrolled window, box inside it) for the sections: a short screen
        scrolls them instead of cutting them off."""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content.pack_start(scrolled, True, True, 0)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(6)
        vbox.set_margin_start(30)
        vbox.set_margin_end(30)
        scrolled.add(vbox)
        return scrolled, vbox

    def _build_footer_area(self, content, scrolled):
        """The footer ("Next:" and Set it up / Quit) below the scrolling part,
        with a "More below" bar above it. Inside the scroll, a title bar the
        desktop draws (KDE) or a short screen could push the button out of
        sight."""
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        footer.set_margin_bottom(20)
        footer.set_margin_start(30)
        footer.set_margin_end(30)
        content.pack_end(footer, False, False, 0)
        self._build_footer(footer)
        # Packed after the footer, so it sits just above it: the optional
        # features under the fold don't get missed.
        content.pack_end(more_below_bar(
            scrolled, tooltip="There's more on this page, including optional features"),
            False, False, 0)

    def _build_header(self, vbox):
        """Build the header section."""
        # Header
        header = Gtk.Label()
        header.set_markup('<span size="x-large"><b>🎙️ Welcome to TuxTalk!</b></span>')
        vbox.pack_start(header, False, False, 0)

        # Subtitle
        subtitle = Gtk.Label()
        subtitle.set_markup('<span size="medium">Voice dictation for Linux</span>')
        subtitle.set_opacity(0.85)
        vbox.pack_start(subtitle, False, False, 0)

        # Separator
        sep1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        vbox.pack_start(sep1, False, False, 10)

    def _build_base_content(self, vbox):
        """Build the base content (shown in all scenarios)."""
        # Main description
        main_desc = Gtk.Label()
        main_desc.set_markup('<span size="medium">Private dictation that runs entirely on your computer.</span>')
        main_desc.set_line_wrap(False)
        main_desc.set_justify(Gtk.Justification.CENTER)
        vbox.pack_start(main_desc, False, False, 10)

        # Key Features section
        features_label = Gtk.Label()
        features_label.set_markup('<span size="large"><b>✨ Key Features</b></span>')
        features_label.set_halign(Gtk.Align.START)
        vbox.pack_start(features_label, False, False, 5)

        features_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        features_box.set_margin_start(20)
        features_box.set_margin_top(5)

        hold = GLib.markup_escape_text(saved_hold_key())      # a hand edit may hold "&"
        hotkey_hint = "your desktop assigns the key" if self.is_flatpak else f"default: {hold} key"
        features = [
            f"🎤 <b>Hold-to-talk dictation</b> ({hotkey_hint})",
            "🗣️ <b>Voice commands:</b> \"period\", \"comma\", \"new paragraph\"",
            "🤖 <b>Powered by OpenAI's Whisper or NVIDIA's Parakeet</b>",
            "🔒 <b>100% local</b> - your voice never leaves your computer",
            "⚙️ <b>Configurable</b> hotkeys and preferences"
        ]

        for feature in features:
            label = Gtk.Label()
            label.set_markup(feature)
            label.set_halign(Gtk.Align.START)
            label.set_line_wrap(True)
            label.set_max_width_chars(60)
            features_box.pack_start(label, False, False, 0)

        vbox.pack_start(features_box, False, False, 0)

        # Separator
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep2.set_margin_top(10)
        sep2.set_margin_bottom(10)
        vbox.pack_start(sep2, False, False, 0)

        # How it works: the whole of what a new user needs to know.
        quickstart_label = Gtk.Label()
        quickstart_label.set_markup('<span size="large"><b>💡 How It Works</b></span>')
        quickstart_label.set_halign(Gtk.Align.START)
        vbox.pack_start(quickstart_label, False, False, 5)

        quickstart_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        quickstart_box.set_margin_start(20)
        quickstart_box.set_margin_top(5)

        key = ("your Hold-to-talk key" if self.is_flatpak
               else f"<b>{hold}</b> (or a key you pick next)")
        tips = [
            f"1. Hold {key}",
            "2. Talk",
            "3. Let go: your words are typed where your cursor is",
            f"Settings and Help are in the TuxTalk menu ({menu_location()})",
        ]

        for tip in tips:
            label = Gtk.Label()
            label.set_markup(tip)
            label.set_halign(Gtk.Align.START)
            label.set_line_wrap(True)
            label.set_max_width_chars(60)
            quickstart_box.pack_start(label, False, False, 0)

        vbox.pack_start(quickstart_box, False, False, 0)

    def _build_optional_features(self, vbox):
        """The recommended-setup card, the setup steps this computer needs, and the
        optional GNOME extension."""
        # Separator before optional features
        sep3 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep3.set_margin_top(10)
        sep3.set_margin_bottom(10)
        vbox.pack_start(sep3, False, False, 0)

        # The setup this computer should get (recommend.py), with Other options.
        self._build_recommendation(vbox)

        # PortAudio setup section (shown FIRST if needed - required for audio recording)
        if self.needs_portaudio_install:
            self._build_portaudio_setup_section(vbox)

            # Separator after PortAudio section
            if self.needs_uinput_fix or self.offer_extension:
                sep_portaudio = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep_portaudio.set_margin_top(15)
                sep_portaudio.set_margin_bottom(10)
                vbox.pack_start(sep_portaudio, False, False, 0)

        # Flatpak: typing works out of the box (libei). The only setup is letting
        # the desktop assign the dictation shortcut. Host: the uinput step.
        if self.is_flatpak:
            self.required_pulse_box = build_shortcut_setup_section(vbox, self.has_gnome)

            if self.offer_extension:
                sep_sc = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep_sc.set_margin_top(15)
                sep_sc.set_margin_bottom(10)
                vbox.pack_start(sep_sc, False, False, 0)
        # Typing setup section (shown if needed - this is critical for typing to work)
        elif self.needs_uinput_fix:
            self._build_typing_setup_section(vbox)

            # Separator after typing setup section
            if self.offer_extension:
                sep_uinput = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep_uinput.set_margin_top(15)
                sep_uinput.set_margin_bottom(10)
                vbox.pack_start(sep_uinput, False, False, 0)

        # Optional Features: the GNOME extension is the only one
        if self.offer_extension:
            optional_label = Gtk.Label()
            optional_label.set_markup('<span size="large"><b>⚙️ Optional Features</b></span>')
            optional_label.set_halign(Gtk.Align.START)
            vbox.pack_start(optional_label, False, False, 5)

            self._build_gnome_extension_option(vbox)

            # Preferences note
            note = Gtk.Label()
            note.set_markup('<span>💡 <i>You can change these anytime in Preferences</i></span>')
            note.set_halign(Gtk.Align.START)
            note.set_margin_top(10)
            note.set_opacity(0.85)
            vbox.pack_start(note, False, False, 0)

    def _build_portaudio_setup_section(self, vbox):
        """Build the PortAudio setup section (required for audio recording)."""
        portaudio_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        portaudio_box.set_margin_start(10)
        portaudio_box.set_margin_top(5)

        # Header with warning icon
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header_label = Gtk.Label()
        header_label.set_markup('<span size="large"><b>⚠️ Audio Library Required</b></span>')
        header_label.set_halign(Gtk.Align.START)
        header_box.pack_start(header_label, False, False, 0)
        portaudio_box.pack_start(header_box, False, False, 0)

        # Explanation
        explain_label = Gtk.Label()
        explain_label.set_markup(
            '<span>TuxTalk needs PortAudio to record audio from your microphone.\n'
            'This library is not installed on your system.</span>'
        )
        explain_label.set_halign(Gtk.Align.START)
        explain_label.set_line_wrap(True)
        explain_label.set_max_width_chars(60)
        explain_label.set_margin_start(10)
        explain_label.set_margin_top(5)
        portaudio_box.pack_start(explain_label, False, False, 0)

        # Status display (initial "not found" message - will be updated after install)
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        status_box.set_margin_start(15)
        status_box.set_margin_top(8)

        self.portaudio_initial_status = Gtk.Label()
        self.portaudio_initial_status.set_markup('❌ <span color="#f44336">PortAudio library not found</span>')
        self.portaudio_initial_status.set_halign(Gtk.Align.START)
        status_box.pack_start(self.portaudio_initial_status, False, False, 0)

        portaudio_box.pack_start(status_box, False, False, 0)

        # What the install does
        what_label = Gtk.Label()
        what_label.set_markup('<b>What "Install PortAudio" does:</b>')
        what_label.set_halign(Gtk.Align.START)
        what_label.set_margin_start(10)
        what_label.set_margin_top(10)
        portaudio_box.pack_start(what_label, False, False, 0)

        details = [
            "📦 Installs the PortAudio library using your package manager",
            "🎤 Enables microphone recording for speech recognition"
        ]

        details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        details_box.set_margin_start(25)

        for detail in details:
            label = Gtk.Label()
            label.set_markup(detail)
            label.set_halign(Gtk.Align.START)
            label.set_opacity(0.9)
            details_box.pack_start(label, False, False, 0)

        portaudio_box.pack_start(details_box, False, False, 0)

        # Install button
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_margin_start(10)
        button_box.set_margin_top(12)

        self.install_portaudio_button = Gtk.Button(label="📦 Install PortAudio (Recommended)")
        self.install_portaudio_button.get_style_context().add_class("suggested-action")
        self.install_portaudio_button.connect("clicked", self._on_install_portaudio_clicked)
        self.install_portaudio_button.set_tooltip_text(
            "Install PortAudio library using your system's package manager.\n"
            "This will prompt for your admin password."
        )
        button_box.pack_start(self.install_portaudio_button, False, False, 0)

        # Status label (shown after install attempt)
        self.portaudio_status_label = Gtk.Label()
        self.portaudio_status_label.set_halign(Gtk.Align.START)
        self.portaudio_status_label.set_xalign(0)
        self.portaudio_status_label.set_line_wrap(True)
        self.portaudio_status_label.set_max_width_chars(40)
        button_box.pack_start(self.portaudio_status_label, False, False, 10)

        portaudio_box.pack_start(button_box, False, False, 0)

        vbox.pack_start(portaudio_box, False, False, 0)

    def _on_install_portaudio_clicked(self, button):
        """Handle the Install PortAudio button click."""
        from tuxtalk.uinput_helper import install_portaudio_with_pkexec

        # Disable button while processing
        button.set_sensitive(False)
        button.set_label("Installing...")
        self.portaudio_status_label.set_markup('<span>Installing PortAudio...</span>')
        self._run_in_background(lambda: install_portaudio_with_pkexec(),
                                lambda outcome: self._finish_install_portaudio(button, outcome))

    def _finish_install_portaudio(self, button, outcome):
        """Show the result of the PortAudio install (GTK thread)."""
        if isinstance(outcome, Exception):
            logger.error(f"Error installing PortAudio: {outcome}")
            button.set_sensitive(True)
            button.set_label("📦 Install PortAudio (Recommended)")
            self.portaudio_status_label.set_markup(
                _status_markup(f"Error: {outcome}", "#f44336"))
            return
        success, message = outcome
        if success:
            button.set_label("✅ Installed!")
            # Update the initial "not found" status to show success
            self.portaudio_initial_status.set_markup(
                '✅ <span color="#4CAF50">PortAudio library installed</span>'
            )
            self.portaudio_status_label.set_markup(
                '<span color="#4CAF50"><b>PortAudio installed!</b> Audio recording is now available.</span>'
            )

            # Update internal state
            self.portaudio_fixed = True
            self.needs_portaudio_install = False

            # Show success message
            show_message(
                self.dialog,
                Gtk.MessageType.INFO,
                "PortAudio Installed!",
                "PortAudio has been installed successfully.\n\n"
                "TuxTalk can now record audio from your microphone for speech recognition.")
        else:
            button.set_sensitive(True)
            button.set_label("📦 Install PortAudio (Recommended)")

            if "cancelled" in message.lower():
                self.portaudio_status_label.set_markup(
                    '<span color="#FF9800">Installation cancelled. You can try again.</span>'
                )
            else:
                self.portaudio_status_label.set_markup(_status_markup(message, "#f44336"))

    def _run_in_background(self, work, finish):
        """Run blocking *work* on a thread, then *finish(outcome)* on the GTK thread.

        Package installs and pkexec scripts can take minutes; on the GTK thread
        they would freeze the window. The outcome is work's return value, or the
        exception it raised. "Set it up" waits while anything runs, and nothing
        is updated once the window has closed. Jobs run one after another: two
        package installs at once would fail on the package manager's lock.
        """
        self._busy += 1
        self._update_continue_button()

        def runner():
            try:
                with self._job_lock:
                    outcome = work()
            except Exception as e:
                outcome = e
            GLib.idle_add(self._finish_background, finish, outcome)

        threading.Thread(target=runner, daemon=True).start()

    def _finish_background(self, finish, outcome):
        """GTK-thread half of _run_in_background: report one finished job."""
        self._busy -= 1
        if self._closed:
            return False
        self._update_continue_button()
        try:
            finish(outcome)
        except Exception:
            logger.error("Could not show the setup result", exc_info=True)
        return False

    def _update_continue_button(self):
        """The "Set it up" button works only while no background job runs."""
        if self.continue_button is not None:
            self.continue_button.set_sensitive(self._busy == 0)

    def _build_typing_setup_section(self, vbox):
        """Build the typing setup section: permission to type and hear hotkeys."""
        typing_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        typing_box.set_margin_start(10)
        typing_box.set_margin_top(5)

        # Header: this step is required, not optional.
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header_label = Gtk.Label()
        header_label.set_markup('<span size="large"><b>🔴 Typing Setup Required</b></span>')
        header_label.set_halign(Gtk.Align.START)
        header_box.pack_start(header_label, False, False, 0)
        typing_box.pack_start(header_box, False, False, 0)

        # What Fix Typing will do, worked out first so the line below only
        # asks for a restart when the fix really needs one (the hotkey
        # helper doesn't; the "input" group does).
        from tuxtalk.secure_input import fix_description
        lines, needs_restart = fix_description()
        details = [f"🔧 {GLib.markup_escape_text(line)}" for line in lines]
        if needs_restart:
            details.append("🔄 Restart your computer to apply (logging out is often not enough)")
        else:
            details.append("✓ No restart needed")

        explain_text = (
            '<span><b>This step is required for TuxTalk to work.</b>\n'
            'Without this setup, dictated text cannot be typed into your applications.\n'
            + ('Click "Fix Typing" below, then restart your computer.' if needs_restart
               else 'Click "Fix Typing" below.')
            + '</span>'
        )
        explain_label = Gtk.Label()
        explain_label.set_markup(explain_text)
        explain_label.set_halign(Gtk.Align.START)
        explain_label.set_line_wrap(True)
        explain_label.set_max_width_chars(60)
        explain_label.set_margin_start(10)
        explain_label.set_margin_top(5)
        typing_box.pack_start(explain_label, False, False, 0)

        # Status display - show what needs to be fixed
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        status_box.set_margin_start(15)
        status_box.set_margin_top(8)

        # Only built while typing needs fixing; Fix Typing updates it.
        uinput_label = Gtk.Label()
        uinput_label.set_markup("❌ Input device permissions needed")
        uinput_label.set_halign(Gtk.Align.START)
        status_box.pack_start(uinput_label, False, False, 0)
        self._uinput_status_label = uinput_label

        typing_box.pack_start(status_box, False, False, 0)

        # What the fix does section
        what_label = Gtk.Label()
        what_label.set_markup('<b>What "Fix Typing" does:</b>')
        what_label.set_halign(Gtk.Align.START)
        what_label.set_margin_start(10)
        what_label.set_margin_top(10)
        typing_box.pack_start(what_label, False, False, 0)

        # Details (worked out above) - show relevant items based on what's needed
        details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        details_box.set_margin_start(25)

        for detail in details:
            label = Gtk.Label()
            label.set_markup(detail)
            label.set_halign(Gtk.Align.START)
            label.set_opacity(0.9)
            details_box.pack_start(label, False, False, 0)

        # On the "input" group path, why and what it means (secure_input).
        from tuxtalk.secure_input import fallback_note
        note = fallback_note()
        if note:
            label = Gtk.Label(label=f"🔒 {note}", xalign=0)
            label.set_line_wrap(True)
            label.set_max_width_chars(70)
            label.set_opacity(0.9)
            label.set_margin_top(4)
            details_box.pack_start(label, False, False, 0)

        typing_box.pack_start(details_box, False, False, 0)

        # Buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_margin_start(10)
        button_box.set_margin_top(12)

        self.fix_typing_button = Gtk.Button(label="🔧 Fix Typing (Recommended)")
        self.fix_typing_button.get_style_context().add_class("suggested-action")
        self.fix_typing_button.connect("clicked", self._on_fix_typing_clicked)
        self.fix_typing_button.set_tooltip_text(
            "Lets TuxTalk type into your apps and hear its hotkeys.\n"
            "This will prompt for your admin password.")
        button_box.pack_start(self.fix_typing_button, False, False, 0)

        # Status label (shown after fix attempt)
        self.typing_status_label = Gtk.Label()
        self.typing_status_label.set_halign(Gtk.Align.START)
        self.typing_status_label.set_xalign(0)
        self.typing_status_label.set_line_wrap(True)
        self.typing_status_label.set_max_width_chars(40)
        button_box.pack_start(self.typing_status_label, False, False, 10)

        typing_box.pack_start(button_box, False, False, 0)

        # Alternative note
        # Not "use Clipboard Paste instead": paste presses Ctrl+V on the same
        # virtual keyboard, so it needs these permissions too.
        alt_note = Gtk.Label()
        alt_note.set_markup(
            '<span><i>💡 If you skip this, TuxTalk can\'t type into other apps, but every\n'
            '   dictation is kept in Recent Dictations in the TuxTalk menu to copy and paste.</i></span>'
        )
        alt_note.set_halign(Gtk.Align.START)
        alt_note.set_margin_start(10)
        alt_note.set_margin_top(8)
        alt_note.set_opacity(0.85)
        alt_note.set_line_wrap(True)
        alt_note.set_max_width_chars(65)
        typing_box.pack_start(alt_note, False, False, 0)

        vbox.pack_start(typing_box, False, False, 0)

    def _typing_fix_pending(self):
        """Whether the typing setup this computer needs hasn't been done."""
        if self.is_flatpak:
            return False
        return self.needs_uinput_fix and not self.uinput_fixed

    def _on_set_it_up(self, _button):
        """"Set it up" does the typing setup first when it's needed, so a user
        who never scrolls to Fix Typing doesn't end up with no hotkey."""
        if not self._typing_fix_pending():
            self.dialog.response(Gtk.ResponseType.OK)
            return
        button = self.fix_typing_button
        if button is not None and button.get_sensitive():
            self._continue_after_fix = True
            self._on_fix_typing_clicked(button)
        else:
            # Fix Typing is still running.
            self._continue_without_typing()

    def _continue_without_typing(self):
        """Warn that the hotkey and typing won't work; continue if the user
        says so, otherwise stay on this screen."""
        msg = message_dialog(
            transient_for=self.dialog,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Typing isn't set up yet",
        )
        msg.format_secondary_text(
            "Without it your dictation key and typing won't work. Go back and "
            "click Fix Typing, or continue and set it up later in Preferences.")
        msg.add_button("Go Back", Gtk.ResponseType.CANCEL)
        msg.add_button("Continue Anyway", Gtk.ResponseType.OK)
        answer = msg.run()
        msg.destroy()
        if answer == Gtk.ResponseType.OK:
            self.dialog.response(Gtk.ResponseType.OK)

    def _on_fix_typing_clicked(self, button):
        """Handle the Fix Typing button click: the typing and hotkey permissions."""
        # Disable button while processing
        button.set_sensitive(False)
        button.set_label("Setting up...")
        self.typing_status_label.set_markup('<span>Setting up typing...</span>')
        self._run_in_background(_fix_typing_steps,
                                lambda outcome: self._finish_fix_typing(button, outcome))

    def _finish_fix_typing(self, button, outcome):
        """Show the result of Fix Typing (GTK thread), then carry on with
        "Set it up" if that is what started it."""
        self._report_fix_typing(button, outcome)
        if getattr(self, "_continue_after_fix", False):
            self._continue_after_fix = False
            if self._typing_fix_pending():
                self._continue_without_typing()
            else:
                self.dialog.response(Gtk.ResponseType.OK)

    def _report_fix_typing(self, button, outcome):
        """Show the result of Fix Typing (GTK thread)."""
        if isinstance(outcome, Exception):
            logger.error(f"Error fixing typing setup: {outcome}")
            button.set_sensitive(True)
            button.set_label("🔧 Fix Typing (Recommended)")
            self.typing_status_label.set_markup(
                _status_markup(f"Error: {outcome}", "#f44336"))
            return

        if outcome["uinput_fixed"]:
            self.uinput_fixed = True
            self.restart_needed = self.restart_needed or outcome["needs_restart"]
        needs_restart = self.restart_needed

        if self.uinput_fixed:
            button.set_label("✅ Fixed!")
            button.set_sensitive(False)  # Disable - no more action needed
            # Or the status line above the button still says "needed".
            label = getattr(self, "_uinput_status_label", None)
            if label is not None:
                label.set_markup("✅ Input device permissions OK")

            restart_note = " Restart your computer to complete." if needs_restart else ""
            self.typing_status_label.set_markup(
                f'<span color="#4CAF50"><b>✓ Ready!</b> Permissions set.{restart_note}</span>'
            )

            # Show success dialog
            show_message(self.dialog,
                         Gtk.MessageType.INFO,
                         "Typing Setup Complete!",
                         ("A restart will be needed to activate this — but finish "
                          "setup first. You'll be reminded at the end.\n\n"
                          if needs_restart else "")
                         + "TuxTalk can now type directly into your applications!")
        else:
            # Failed or cancelled: show what went wrong and let the user try again.
            button.set_sensitive(True)
            button.set_label("🔧 Fix Typing (Recommended)")
            result_text = "\n".join(outcome["results"]) or "Setup was cancelled"
            self.typing_status_label.set_markup(_status_markup(result_text, "#FF9800"))

    def _build_gnome_extension_option(self, vbox):
        """Build GNOME extension checkbox and details."""
        ext_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ext_box.set_margin_start(20)
        ext_box.set_margin_top(5)

        # Checkbox with label
        self.extension_check = Gtk.CheckButton()
        self.extension_check.get_style_context().add_class('welcome-checkbox')
        ext_label = Gtk.Label()
        ext_label.set_markup('📦 <b>Install GNOME Extension</b> <span size="small">(~10KB)</span>')
        ext_label.set_halign(Gtk.Align.START)
        self.extension_check.add(ext_label)
        self.extension_check.set_tooltip_text(
            "Install native GNOME Shell extension for enhanced integration.\n"
            "Adds TuxTalk's menu to the top bar, and keeps the dictation keys from reaching other apps.\n"
            "Lightweight (~10KB) and requires logging out/in after installation."
        )
        ext_box.pack_start(self.extension_check, False, False, 0)

        # Plain GNOME (Fedora, Silverblue, Debian...) shows no tray icons, so
        # without the extension TuxTalk has no menu on screen at all: tick it
        # and say why. Ubuntu's GNOME has a tray, so there it stays optional.
        from .desktop_detect import tray_host_available
        if not tray_host_available():
            self.extension_check.set_active(True)
            no_tray = Gtk.Label(xalign=0)
            no_tray.set_markup(
                '<span color="#e6aa3c"><b>⚠️ Recommended here: this desktop has no system tray.</b>\n'
                'Without the extension, TuxTalk has no menu on screen. Its menu '
                '(Preferences, Transcribe a File and more) then opens only when you '
                'start TuxTalk again from your apps.</span>')
            no_tray.set_line_wrap(True)
            no_tray.set_max_width_chars(60)
            no_tray.set_margin_start(30)
            # Clear of the checkbox's glow (up to 16px of box-shadow).
            no_tray.set_margin_top(14)
            no_tray.set_margin_bottom(4)
            ext_box.pack_start(no_tray, False, False, 0)

        # GNOME detected badge
        gnome_badge = Gtk.Label()
        gnome_badge.set_markup('<b>GNOME Desktop Detected!</b>')
        gnome_badge.set_halign(Gtk.Align.START)
        gnome_badge.set_margin_start(30)
        ext_box.pack_start(gnome_badge, False, False, 0)

        # Extension description
        ext_desc = Gtk.Label()
        ext_desc.set_markup('Add native desktop integration to your GNOME panel:')
        ext_desc.set_halign(Gtk.Align.START)
        ext_desc.set_margin_start(30)
        ext_desc.set_margin_top(5)
        ext_desc.set_opacity(0.9)
        ext_box.pack_start(ext_desc, False, False, 0)

        # Extension benefits
        ext_benefits = [
            "🔵 Panel indicator showing recording state",
            "🎮 Service start/stop controls",
            "✨ Visual feedback and notifications"
        ]

        ext_benefits_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        ext_benefits_box.set_margin_start(50)

        for benefit in ext_benefits:
            label = Gtk.Label()
            label.set_markup(benefit)
            label.set_halign(Gtk.Align.START)
            label.set_opacity(0.85)
            ext_benefits_box.pack_start(label, False, False, 0)

        ext_box.pack_start(ext_benefits_box, False, False, 0)

        # Requires log out note
        logout_note = Gtk.Label()
        logout_note.set_markup('<span style="italic">(Requires logging out and back in after installation)</span>')
        logout_note.set_halign(Gtk.Align.START)
        logout_note.set_margin_start(30)
        logout_note.set_margin_top(3)
        logout_note.set_opacity(0.85)
        ext_box.pack_start(logout_note, False, False, 0)

        vbox.pack_start(ext_box, False, False, 0)

    def _build_recommendation(self, vbox):
        """The "Recommended for your computer" card and Other options.

        The card always shows exactly what will be set up; choices that can't
        work are greyed out with the reason (recommend.option_states)."""
        from . import recommend as rec
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.get_style_context().add_class("tt-rec-card")
        tag = Gtk.Label(xalign=0)
        tag.set_markup("RECOMMENDED FOR YOUR COMPUTER")
        tag.get_style_context().add_class("tt-rec-tag")
        self.rec_title = Gtk.Label(xalign=0)
        self.rec_explanation = Gtk.Label(xalign=0)
        self.rec_explanation.set_line_wrap(True)
        self.rec_download = Gtk.Label(xalign=0)
        self.rec_download.set_opacity(0.85)
        for w in (tag, self.rec_title, self.rec_explanation, self.rec_download):
            card.pack_start(w, False, False, 0)
        vbox.pack_start(card, False, False, 6)

        # No summary beside the button: the card above says how it's set.
        fold = self.options_fold = Fold("Change language, model or graphics card",
                                        "Hide language, model and graphics card",
                                        on_toggle=self._on_options_toggled)
        other = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        lang_row = Gtk.Box(spacing=8)
        lang_row.pack_start(Gtk.Label(label="I dictate in", xalign=0), False, False, 0)
        # Not the desktop's language: the one spoken into the microphone.
        speak_hint = Gtk.Label(xalign=0)
        speak_hint.set_markup('<span alpha="70%">(the language you speak)</span>')
        # The first dropdown a new user ever touches. Without this its popup
        # opens in menu mode, on top of the button and the text above it.
        from .ui_style import apply_dropdown_list_style
        apply_dropdown_list_style()
        self.lang_combo = Gtk.ComboBoxText()
        codes = [c for c, _ in rec.LANGUAGES]
        for code, _name in rec.LANGUAGES:
            self.lang_combo.append(code, rec.language_label(code))
        if self.initial_language not in codes:
            self.lang_combo.append(self.initial_language, rec.language_label(self.initial_language))
        self.lang_combo.set_active_id(self.initial_language)
        lang_row.pack_start(self.lang_combo, False, False, 0)
        lang_row.pack_start(speak_hint, False, False, 0)
        other.pack_start(lang_row, False, False, 0)

        model_header = Gtk.Label(xalign=0)
        model_header.set_markup("<b>Speech model</b>")
        other.pack_start(model_header, False, False, 4)
        self.model_radios, self.model_reasons, group = {}, {}, None
        for state in rec.option_states(self.initial_language, self.hardware):
            radio = Gtk.RadioButton.new_from_widget(group)
            group = group or radio
            text = Gtk.Label(xalign=0)
            text.set_line_wrap(True)
            radio.add(text)
            radio.connect("toggled", self._on_model_radio, state.model)
            self.model_radios[state.model] = radio
            self.model_reasons[state.model] = text
            other.pack_start(radio, False, False, 0)
        more = Gtk.Label(xalign=0)
        more.set_markup('<span><i>More models (Tiny, Base, Medium) are in Preferences.</i></span>')
        other.pack_start(more, False, False, 0)

        where = Gtk.Label(xalign=0)
        where.set_markup("<b>Where it runs</b>")
        other.pack_start(where, False, False, 4)
        if self.hardware.gpu:
            self.gpu_check = Gtk.CheckButton(label=f"Use my graphics card ({self.hardware.gpu_name})")
            self.gpu_check.set_active(True)
            self.gpu_check.set_tooltip_text("Several times faster. TuxTalk checks it really is "
                                            "faster than your processor before using it.")
            self.gpu_check.connect("toggled", lambda *_: self._refresh_recommendation())
            other.pack_start(self.gpu_check, False, False, 0)
        else:
            other.pack_start(Gtk.Label(label="On this computer's processor", xalign=0), False, False, 0)

        fold.content.attach(other, 0, 0, 1, 1)
        vbox.pack_start(fold, False, False, 0)
        self.lang_combo.connect("changed", lambda *_: self._refresh_recommendation())
        self._refresh_recommendation()

    def _language(self):
        return self.lang_combo.get_active_id() or self.initial_language

    def _use_gpu(self):
        return bool(self.gpu_check and self.gpu_check.get_active())

    def _current_setup(self):
        """The setup the card shows for the current language, model and device."""
        from . import recommend as rec
        return rec.recommend(self._language(), self.hardware,
                             model=self._chosen_model, use_gpu=self._use_gpu())

    def _on_model_radio(self, radio, model):
        if radio.get_active() and not self._syncing:
            self._chosen_model = model
            self._refresh_recommendation()

    def _on_options_toggled(self, opening):
        """Opening the options adds its choices below the bottom of the
        window. Scroll so they start near the top instead of leaving the user
        to find them."""
        fold = self.options_fold
        scrolled = fold.get_ancestor(Gtk.ScrolledWindow)
        if not opening or scrolled is None:
            return

        def scroll():
            # After the slide-down, so the choices have their size.
            target = fold.translate_coordinates(scrolled.get_child(), 0, 0)
            if target:
                adj = scrolled.get_vadjustment()
                adj.set_value(min(max(target[1] - 12, 0), adj.get_upper() - adj.get_page_size()))
            return False
        GLib.timeout_add(fold.revealer.get_transition_duration() + 60, scroll)

    def _refresh_recommendation(self):
        """Update the card and the greyed choices from recommend.py."""
        from . import recommend as rec
        setup = self._current_setup()
        name = rec.language_name(self._language())
        self.rec_title.set_markup(f"<b>{GLib.markup_escape_text(setup.title)}</b>")
        self.rec_explanation.set_markup(
            f"{GLib.markup_escape_text(setup.explanation)} For <b>{GLib.markup_escape_text(name)}</b>.")
        self.rec_download.set_text(setup.download_text)
        self._syncing = True
        try:
            for state in rec.option_states(self._language(), self.hardware,
                                           use_gpu=self._use_gpu()):
                radio, text = self.model_radios[state.model], self.model_reasons[state.model]
                radio.set_sensitive(state.available)
                note = state.description if state.available else state.reason
                mark = " (recommended)" if state.model == setup.model else ""
                text.set_markup(f"<b>{state.label}</b>{mark}, {state.size}\n"
                                f'<span>{GLib.markup_escape_text(note)}</span>')
                if state.model == setup.model:
                    radio.set_active(True)
        finally:
            self._syncing = False

    def _build_footer(self, vbox):
        """Build the footer section."""
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(15)

        if self.hotkey_unsupported:
            # Hard stop: the app can't function on this desktop, so there is no
            # "continue" — only quit. The AppImage download button sits above.
            next_label = Gtk.Label()
            next_label.set_markup(
                '<span foreground="#ff6b6b"><b>This Flatpak can’t run on your '
                'desktop — get one of the packages above, then quit.</b></span>'
            )
            next_label.set_halign(Gtk.Align.CENTER)
            next_label.set_line_wrap(True)
            next_label.set_margin_top(10)
            vbox.pack_start(next_label, False, False, 0)

            quit_btn = Gtk.Button(label="Quit TuxTalk")
            quit_btn.set_size_request(200, 40)
            # Neutral secondary style: a clear button, but understated so it
            # doesn't fight the red warning or the blue primary download button.
            quit_btn.get_style_context().add_class("tt-quit")
            quit_btn.connect("clicked", lambda w: self.dialog.response(Gtk.ResponseType.CLOSE))
            button_box.pack_start(quit_btn, False, False, 0)
            vbox.pack_start(button_box, False, False, 0)
            return

        # Next steps note. On the Flatpak there is no evdev hotkey test; instead,
        # finishing here starts TuxTalk and the desktop pops up its own window to
        # bind the key — so tell the user that's what's coming, not a test.
        next_label = Gtk.Label()
        if self.is_flatpak:
            next_label.set_markup(
                '<span><b>Next:</b> TuxTalk downloads your speech model, then starts '
                'and prompts you to set your dictation key.</span>')
        else:
            next_label.set_markup('<span><b>Next:</b> You\'ll test your hotkeys to ensure they work correctly</span>')
        next_label.set_halign(Gtk.Align.START)
        next_label.set_line_wrap(True)
        next_label.set_margin_top(10)
        next_label.set_opacity(0.85)
        vbox.pack_start(next_label, False, False, 0)

        # Centered "Set it up" button
        continue_btn = Gtk.Button(label="Set it up")
        continue_btn.set_size_request(200, 40)
        continue_btn.get_style_context().add_class("suggested-action")
        continue_btn.connect("clicked", self._on_set_it_up)
        self.continue_button = continue_btn

        button_box.pack_start(continue_btn, False, False, 0)
        vbox.pack_start(button_box, False, False, 0)

    def _pulse_targets(self):
        """The widgets that pulse: the extension checkbox and the Flatpak
        "Required" warning box (red, via .tt-required). Often neither."""
        return [w for w in (self.extension_check, self.required_pulse_box) if w]

    def _pulse_checkboxes(self):
        """Advance the pulsing glow one step on each of _pulse_targets()."""

        targets = self._pulse_targets()

        # A sine wave from dim (0) to bright (4) and back.
        phase = getattr(self, '_pulse_phase', 0)
        self._pulse_phase = phase + 0.15
        intensity = 2 + 2 * math.sin(phase)

        # Map to one of 5 pulse states (0=dimmest, 4=brightest)
        pulse_level = int(round(intensity))

        for widget in targets:
            # Checkboxes stop pulsing once checked; the required box always pulses.
            if hasattr(widget, 'get_active') and widget.get_active():
                continue
            style_context = widget.get_style_context()

            # Remove all pulse classes
            for i in range(5):
                style_context.remove_class(f'pulse-{i}')

            # Add the current pulse level class
            style_context.add_class(f'pulse-{pulse_level}')

        return True  # Continue animation

    def run(self):
        """
        Show the dialog and return user selections.

        Returns:
            dict: User selections with keys:
                - 'install_extension': bool (if GNOME detected)
                - 'model', 'device', 'dictation_language': the setup the
                  recommendation card shows (absent on an unsupported desktop)
                - 'continue': bool (True if user clicked "Set it up")
        """
        # Show dialog and fade in
        self.dialog.set_opacity(0.0)
        self.dialog.show_all()
        fade_in(self.dialog)

        # Pulse the checkbox/warning every 50 ms, only when there is one:
        # source_remove below warns "Source ID not found" for a timer that has
        # already stopped itself.
        self._pulse_phase = 0
        pulse_timer = (GLib.timeout_add(50, self._pulse_checkboxes)
                       if self._pulse_targets() else None)

        response = self.dialog.run()
        self._closed = True

        if pulse_timer:
            GLib.source_remove(pulse_timer)

        result = {'continue': response == Gtk.ResponseType.OK}
        # On an unsupported desktop the only footer action is "Quit TuxTalk"
        # (ResponseType.CLOSE): the app cannot function, so signal a real quit.
        result['quit'] = (response == Gtk.ResponseType.CLOSE) or self.hotkey_unsupported

        if self.extension_check:
            result['install_extension'] = self.extension_check.get_active()

        if hasattr(self, "rec_title"):
            setup = self._current_setup()
            result['model'] = setup.model
            result['device'] = setup.device
            result['dictation_language'] = self._language()

        # Include typing setup status
        result['uinput_fixed'] = self.uinput_fixed
        result['needs_restart'] = self.restart_needed
        result['needs_uinput_fix'] = self.needs_uinput_fix and not self.uinput_fixed

        # Include PortAudio status
        result['portaudio_installed'] = not self.needs_portaudio_install or self.portaudio_fixed
        result['needs_portaudio'] = self.needs_portaudio_install and not self.portaudio_fixed

        logger.info(f"Welcome dialog result: {result}")

        return result

    def destroy(self):
        """Destroy the dialog."""
        self._closed = True
        if self.dialog:
            self.dialog.destroy()


def show_welcome_dialog(parent=None):
    """
    Convenience function to show the welcome dialog with splash screen.

    Args:
        parent: Parent window (optional)

    Returns:
        dict: User selections from the dialog
    """
    result_container = [None]  # Use list to store result from callback

    def show_dialog_after_splash():
        """Called after splash screen finishes."""
        dialog = None
        try:
            dialog = WelcomeDialog(parent=parent)
            result_container[0] = dialog.run()
        except Exception:
            # An error here would otherwise leave Gtk.main() below running for
            # good with nothing on screen. None reads as a cancelled onboarding.
            logger.error("Welcome dialog failed", exc_info=True)
            result_container[0] = None
        finally:
            if dialog is not None:
                try:
                    dialog.destroy()
                except Exception:
                    pass
            Gtk.main_quit()

    # Show splash screen first
    splash = SplashScreen()
    splash.show_and_fade(show_dialog_after_splash)

    # Run GTK main loop until dialog closes
    Gtk.main()

    return result_container[0]


if __name__ == '__main__':
    # Test the welcome dialog
    Gtk.init(None)
    from .ui_style import prepare_windows
    prepare_windows()
    result = show_welcome_dialog()
    print(f"Welcome dialog result: {result}")
