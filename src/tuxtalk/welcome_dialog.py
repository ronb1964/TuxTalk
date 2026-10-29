#!/usr/bin/env python3
"""
First-run onboarding for TuxTalk: splash, welcome screen, hotkey test, tips,
model download and the final Ready screen.

The welcome screen adapts to the computer: every one gets the
recommended-setup card; PortAudio and typing setup (or, in a Flatpak, the
shortcut step) appear only when needed, and the GNOME extension option only
on GNOME. A Flatpak on a desktop without the GlobalShortcuts portal gets a
stop screen instead.
"""

import math
import os
import select
import threading
import time
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
from .ui_style import Fold, message_dialog, more_below_bar, show_message

from .hotkey_labels import layout_label
from .hotkey_test_devices import (close_devices, flush_pending_events, hotkey_test_helper,
                                  open_key_devices, read_ready)
from .hotkey_test_keys import (captured_key_problem, evdev_key_action, evdev_key_name,
                               gtk_key_name, hotkeys_to_save, loggable_key, service_hears)
from .logger import setup_logger
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


class SplashScreen:
    """
    Simple splash screen showing TuxTalk icon.
    Fades in, shows briefly, then fades out before showing welcome dialog.
    """

    def __init__(self):
        """Create the splash screen window."""
        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title("TuxTalk")
        self.window.set_default_size(400, 400)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.set_decorated(False)  # No title bar
        self.window.set_keep_above(True)  # Stay on top
        self.window.set_type_hint(Gdk.WindowTypeHint.SPLASHSCREEN)
        self.window.get_style_context().add_class('tt-splash')

        # Enable transparency
        screen = self.window.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.window.set_visual(visual)
        self.window.set_app_paintable(True)

        # Transparent window with rounded dark box - glow will be animated via CSS classes
        self.css_provider = Gtk.CssProvider()
        self.css_provider.load_from_data(b"""
            window.tt-splash {
                background-color: transparent;
            }
            .tt-splash .splash-box {
                background-color: #262626;
                border-radius: 16px;
                padding: 80px;
            }
            .tt-splash image {
                padding: 0px;
                margin: 0px;
            }
            /* Glow steps 0-15; -gtk-icon-shadow follows the icon's shape. */
            .tt-splash image.glow-0  { -gtk-icon-shadow: 0 0 10px rgba(66, 133, 244, 0.4); }
            .tt-splash image.glow-1  { -gtk-icon-shadow: 0 0 15px rgba(66, 133, 244, 0.45); }
            .tt-splash image.glow-2  { -gtk-icon-shadow: 0 0 20px rgba(66, 133, 244, 0.5); }
            .tt-splash image.glow-3  { -gtk-icon-shadow: 0 0 25px rgba(66, 133, 244, 0.55); }
            .tt-splash image.glow-4  { -gtk-icon-shadow: 0 0 30px rgba(66, 133, 244, 0.6); }
            .tt-splash image.glow-5  { -gtk-icon-shadow: 0 0 35px rgba(66, 133, 244, 0.65); }
            .tt-splash image.glow-6  { -gtk-icon-shadow: 0 0 40px rgba(66, 133, 244, 0.7); }
            .tt-splash image.glow-7  { -gtk-icon-shadow: 0 0 45px rgba(66, 133, 244, 0.75); }
            .tt-splash image.glow-8  { -gtk-icon-shadow: 0 0 50px rgba(66, 133, 244, 0.8); }
            .tt-splash image.glow-9  { -gtk-icon-shadow: 0 0 55px rgba(66, 133, 244, 0.82), 0 0 90px rgba(66, 133, 244, 0.4); }
            .tt-splash image.glow-10 { -gtk-icon-shadow: 0 0 60px rgba(66, 133, 244, 0.84), 0 0 100px rgba(66, 133, 244, 0.5); }
            .tt-splash image.glow-11 { -gtk-icon-shadow: 0 0 65px rgba(66, 133, 244, 0.86), 0 0 110px rgba(66, 133, 244, 0.55); }
            .tt-splash image.glow-12 { -gtk-icon-shadow: 0 0 70px rgba(66, 133, 244, 0.88), 0 0 120px rgba(66, 133, 244, 0.6); }
            .tt-splash image.glow-13 { -gtk-icon-shadow: 0 0 75px rgba(66, 133, 244, 0.9), 0 0 130px rgba(66, 133, 244, 0.65); }
            .tt-splash image.glow-14 { -gtk-icon-shadow: 0 0 80px rgba(66, 133, 244, 0.95), 0 0 140px rgba(66, 133, 244, 0.7); }
            .tt-splash image.glow-15 { -gtk-icon-shadow: 0 0 90px rgba(66, 133, 244, 1.0), 0 0 160px rgba(66, 133, 244, 0.8); }
        """)
        # Screen-wide, because a provider on the window's own style context never
        # reaches the child image. Every rule is scoped to .tt-splash, and the
        # provider is removed with the splash, so other windows in the tray
        # process keep their normal background.
        self._screen = self.window.get_screen() or Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            self._screen,
            self.css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.glow_level = 0
        self.image_widget = None
        self._stop_glow = False

        # Try to load icon from standard paths
        icon_path = None
        icon_paths = []

        # Check AppImage mount point first (has PNG in root)
        appimage_mount = os.environ.get('APPDIR')
        if appimage_mount:
            icon_paths.append(os.path.join(appimage_mount, "io.github.ronb1964.TuxTalk.png"))
            icon_paths.append(os.path.join(appimage_mount, "io.github.ronb1964.TuxTalk.svg"))
            icon_paths.append(os.path.join(appimage_mount, "usr", "share", "icons", "hicolor", "scalable", "apps", "io.github.ronb1964.TuxTalk.svg"))

        # Development/system paths - prioritize SVG for splash (cleaner rendering)
        icon_paths.extend([
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons", "OFFICIAL_ICON_DO_NOT_CHANGE.svg"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons", "TT_retro_square_light_transparent.png"),
            "/app/share/icons/hicolor/scalable/apps/io.github.ronb1964.TuxTalk.svg",
            # System install locations (.deb / .rpm): the hicolor SVG, then the
            # pixmaps PNG. A packaged install has no $APPDIR and no project
            # icons/ folder, so without these the splash shows plain text.
            "/usr/share/icons/hicolor/scalable/apps/io.github.ronb1964.TuxTalk.svg",
            "/usr/share/icons/hicolor/256x256/apps/io.github.ronb1964.TuxTalk.png",
            "/usr/share/pixmaps/io.github.ronb1964.TuxTalk.png",
            "../icons/OFFICIAL_ICON_DO_NOT_CHANGE.svg",
            "../icons/TT_retro_square_light_transparent.png",
            "icons/OFFICIAL_ICON_DO_NOT_CHANGE.svg",
            os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/io.github.ronb1964.TuxTalk.svg"),
        ])

        for path in icon_paths:
            if os.path.exists(path):
                icon_path = path
                logger.debug(f"Found splash icon at: {path}")
                break

        # Create centered icon image with rounded dark background
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.get_style_context().add_class('splash-box')

        if icon_path:
            try:
                # Load icon from file (works with both SVG and PNG)
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, 256, 256)
                self.image_widget = Gtk.Image.new_from_pixbuf(pixbuf)
                box.pack_start(self.image_widget, False, False, 0)
                logger.info(f"Splash screen loaded icon from: {icon_path}")
            except Exception as e:
                logger.warning(f"Could not load splash icon from {icon_path}: {e}")
                # Fallback to text
                label = Gtk.Label(label="TuxTalk")
                label.set_markup('<span size="xx-large" weight="bold">TuxTalk</span>')
                box.pack_start(label, False, False, 0)
                self.image_widget = None
        else:
            # No icon found, use text
            logger.warning("No icon path found for splash screen")
            label = Gtk.Label(label="TuxTalk")
            label.set_markup('<span size="xx-large" weight="bold">TuxTalk</span>')
            box.pack_start(label, False, False, 0)
            self.image_widget = None

        self.window.add(box)

    def _animate_glow(self):
        """Animate the glow effect by expanding once to maximum, then staying there."""
        if not self.image_widget or self._stop_glow:
            return False  # Stop animation if widget gone or stop flag set

        style_context = self.image_widget.get_style_context()

        # Remove old glow class
        style_context.remove_class(f'glow-{self.glow_level}')

        # Increment glow level (0-15), but stop at maximum
        if self.glow_level < 15:
            self.glow_level += 1
            # Add new glow class
            style_context.add_class(f'glow-{self.glow_level}')
            return True  # Continue animation
        else:
            # Stay at maximum glow
            style_context.add_class(f'glow-{self.glow_level}')
            return False  # Stop animation (GLib will auto-remove timer)

    def show_and_fade(self, callback):
        """
        Show splash screen, wait briefly, then fade out and call callback.

        Args:
            callback: Function to call after splash finishes (no arguments)
        """
        self.window.set_opacity(0.0)
        self.window.show_all()

        # The glow grows one step every 100 ms.
        GLib.timeout_add(100, self._animate_glow)

        # Fade in over about half a second (20 steps of 25 ms).
        self._fade_in(0.0, callback)

    def _fade_in(self, opacity, callback):
        """Gradually increase opacity."""
        opacity += 0.05
        self.window.set_opacity(opacity)

        if opacity < 1.0:
            GLib.timeout_add(25, lambda: self._fade_in(opacity, callback))
        else:
            # Hold for 2.5 s while the glow grows to full, then fade out.
            GLib.timeout_add(2500, lambda: self._fade_out(1.0, callback))

    def _fade_out(self, opacity, callback):
        """Gradually decrease opacity."""
        opacity -= 0.05  # same pace as the fade-in
        self.window.set_opacity(opacity)

        if opacity > 0.0:
            GLib.timeout_add(25, lambda: self._fade_out(opacity, callback))
        else:
            # Stop glow animation by setting flag (timer will auto-remove)
            self._stop_glow = True

            # Close splash and show welcome dialog
            Gtk.StyleContext.remove_provider_for_screen(self._screen, self.css_provider)
            self.window.destroy()
            callback()


def _extension_already_active():
    """Whether TuxTalk's GNOME extension is installed and enabled."""
    try:
        from .extension_helper import is_extension_enabled
        return bool(is_extension_enabled())
    except Exception:
        return False


def _menu_location():
    """Where this desktop shows TuxTalk's menu, for the tips: plain GNOME has
    no tray, so "the tray icon" sent people looking for nothing."""
    try:
        if _extension_already_active():
            return "its icon in the top bar"
        from .desktop_detect import tray_host_available
        if tray_host_available():
            return "its tray icon"
    except Exception:
        return "its tray icon"
    return "open TuxTalk again from your apps"


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
            _supported, self.unsupported_desktop_name = self._shortcut_support()
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

        # Add CSS for checkbox styling with pulsating glow
        # Note: CSS needs to be applied globally (add_provider_for_screen) for child widgets,
        # but we use specific class names to avoid affecting other dialogs
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            /* Welcome dialog checkbox styles - using .welcome-checkbox class to avoid conflicts */
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
            .tt-required.pulse-0 { background-color: rgba(255, 80, 80, 0.10); box-shadow: 0 0 8px rgba(255, 80, 80, 0.20); }
            .tt-required.pulse-1 { background-color: rgba(255, 80, 80, 0.14); box-shadow: 0 0 10px rgba(255, 80, 80, 0.28); }
            .tt-required.pulse-2 { background-color: rgba(255, 80, 80, 0.18); box-shadow: 0 0 12px rgba(255, 80, 80, 0.36); }
            .tt-required.pulse-3 { background-color: rgba(255, 80, 80, 0.22); box-shadow: 0 0 14px rgba(255, 80, 80, 0.44); }
            .tt-required.pulse-4 { background-color: rgba(255, 80, 80, 0.28); box-shadow: 0 0 16px rgba(255, 80, 80, 0.55); }
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
""")
        # Apply CSS globally but with specific class names to avoid affecting other dialogs
        Gtk.StyleContext.add_provider_for_screen(
            self.dialog.get_screen() if self.dialog.get_screen() else Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Create scrolled window for content (handles small screens)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = self.dialog.get_content_area()
        content.pack_start(scrolled, True, True, 0)

        # VBox inside scrolled window
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(6)
        vbox.set_margin_start(30)
        vbox.set_margin_end(30)
        scrolled.add(vbox)

        # Build content sections
        self._build_header(vbox)

        if self.hotkey_unsupported:
            # Hard stop: skip the welcome blurb, features, quick-start and all the
            # setup sections — the app can't run here. Go straight to the red stop
            # box and "get a package that works on your desktop".
            self._build_unsupported_desktop_section(vbox, self.unsupported_desktop_name)
        else:
            self._build_base_content(vbox)
            # Always: it holds the recommended-setup card, which every computer
            # needs (language, model, graphics card). Its other parts check
            # their own conditions.
            self._build_optional_features(vbox)

        # The footer ("Next:" and Set it up / Quit) sits below the scrolling
        # part so it is always fully visible: inside the scroll, a title bar the
        # desktop draws (KDE) or a short screen can push the button out of sight.
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        footer.set_margin_bottom(20)
        footer.set_margin_start(30)
        footer.set_margin_end(30)
        content.pack_end(footer, False, False, 0)
        self._build_footer(footer)
        # A "More below" bar just above the footer (packed after it), so the
        # optional features under the fold don't get missed.
        content.pack_end(more_below_bar(
            scrolled, tooltip="There's more on this page, including optional features"),
            False, False, 0)
        # Left to GTK, the focus went to the first button in the scrolling
        # part ("Change language, model or graphics card"), and the window
        # opened scrolled down to it with its title cut off.
        if self.continue_button is not None:
            self.dialog.set_focus(self.continue_button)

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

        hold = GLib.markup_escape_text(_saved_hold_key())      # a hand edit may hold "&"
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
            f"Settings and Help are in the TuxTalk menu ({_menu_location()})",
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
            self._build_shortcut_setup_section(vbox)

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

    # Desktops whose portal backend (xdg-desktop-portal-xapp) does NOT implement
    # GlobalShortcuts, so the Flatpak dictation hotkey can never register there.
    _NO_GLOBALSHORTCUTS = {"XFCE", "CINNAMON", "X-CINNAMON", "MATE"}
    _DESKTOP_NAMES = {
        "KDE": "KDE Plasma", "GNOME": "GNOME", "HYPRLAND": "Hyprland",
        "XFCE": "XFCE", "CINNAMON": "Cinnamon", "X-CINNAMON": "Cinnamon",
        "MATE": "MATE",
    }

    def _globalshortcuts_available(self):
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

    def _shortcut_support(self):
        """Return (supported: bool, friendly_desktop_name: str) for the hotkey."""
        raw = os.environ.get("XDG_CURRENT_DESKTOP", "")
        base = raw.split(":")[0].upper() if raw else ""
        name = self._DESKTOP_NAMES.get(base, (raw.split(":")[0] or "your desktop"))
        # Known-unsupported by name (also lets us preview the message via env).
        if base in self._NO_GLOBALSHORTCUTS:
            return (False, name)
        # Otherwise trust a real portal check when it is conclusive.
        if self._globalshortcuts_available() is False:
            return (False, name)
        return (True, name)

    def _build_unsupported_desktop_section(self, vbox, desktop_name):
        """Shown when the desktop lacks the GlobalShortcuts portal: the Flatpak
        hotkey cannot work, so point the user to the AppImage (Backend A/evdev)."""
        esc = GLib.markup_escape_text
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # This is a hard stop: the app cannot function here, so the warning
        # pulses red so it can't be skipped.
        pulse = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pulse.get_style_context().add_class('tt-required')
        self.required_pulse_box = pulse  # picked up by the red pulse animation

        header = Gtk.Label()
        header.set_markup(
            f'<span size="large" foreground="#ffffff"><b>🛑 The dictation hotkey '
            f'won’t work on {esc(desktop_name)}</b></span>'
        )
        header.set_halign(Gtk.Align.START)
        header.set_line_wrap(True)
        header.set_max_width_chars(54)
        pulse.pack_start(header, False, False, 0)

        msg = Gtk.Label()
        msg.set_markup(
            '<span size="medium" foreground="#ffffff">TuxTalk’s hotkey relies on '
            f'your desktop’s <b>global-shortcut</b> system, and <b>{esc(desktop_name)}'
            '</b> doesn’t provide it. The Flatpak will open, but the Hold-to-talk and '
            'Toggle keys can’t be registered — so there is no key to press.</span>'
        )
        msg.set_halign(Gtk.Align.START)
        msg.set_line_wrap(True)
        msg.set_max_width_chars(54)
        msg.set_margin_top(6)
        pulse.pack_start(msg, False, False, 0)

        box.pack_start(pulse, False, False, 0)

        fix = Gtk.Label()
        fix.set_markup(
            '<span size="medium" foreground="#ffffff"><b>Good news — another '
            'TuxTalk package works on your desktop.</b></span> '
            '<span size="medium">Pick the one for your system; they all use a '
            'method that works everywhere:</span>'
        )
        fix.set_halign(Gtk.Align.START)
        fix.set_line_wrap(True)
        fix.set_max_width_chars(56)
        fix.set_margin_start(10)
        fix.set_margin_top(12)
        box.pack_start(fix, False, False, 0)

        pkgs = Gtk.Label()
        pkgs.set_markup(
            '<span size="medium">'
            '• <b>AppImage</b> — runs on any Linux, nothing to install\n'
            '• <b>.deb</b> — Debian, Ubuntu, Linux Mint\n'
            '• <b>.rpm</b> — Fedora, openSUSE\n'
            '• <b>AUR</b> — Arch, CachyOS, EndeavourOS'
            '</span>'
        )
        pkgs.set_halign(Gtk.Align.START)
        pkgs.set_line_wrap(True)
        pkgs.set_max_width_chars(56)
        pkgs.set_margin_start(20)
        pkgs.set_margin_top(6)
        box.pack_start(pkgs, False, False, 0)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_box.set_margin_start(10)
        btn_box.set_margin_top(14)
        download_btn = Gtk.Button(label="⬇  Open the Downloads Page")
        download_btn.get_style_context().add_class("suggested-action")
        download_btn.connect("clicked", self._on_download_appimage)
        btn_box.pack_start(download_btn, False, False, 0)
        box.pack_start(btn_box, False, False, 0)

        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        frame.set_margin_top(6)
        frame.set_margin_bottom(6)
        frame.add(box)
        vbox.pack_start(frame, False, False, 0)

    def _on_download_appimage(self, button):
        """Open the TuxTalk releases page. Opening a URL from a Flatpak goes
        through the OpenURI portal — no special sandbox permission needed."""
        url = "https://github.com/ronb1964/TuxTalk/releases"
        try:
            Gtk.show_uri_on_window(self.dialog, url, Gdk.CURRENT_TIME)
        except Exception as e:
            logger.warning(f"Could not open releases page ({e}); URL: {url}")

    def _build_shortcut_setup_section(self, vbox):
        """Flatpak-only setup step: the desktop assigns the dictation shortcut.

        Inside the sandbox typing works via the portal + libei path with no
        uinput/input-group setup, so the host "Typing Setup Required"
        step does not apply. The one thing the user must do is let their desktop
        bind a key to the TuxTalk dictate shortcuts. Desktops without the
        GlobalShortcuts portal never reach here — _build_dialog shows them the
        unsupported screen instead.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        header = Gtk.Label()
        header.set_markup('<span size="large"><b>🔴 Required: Set Your Dictation Key</b></span>')
        header.set_halign(Gtk.Align.START)
        box.pack_start(header, False, False, 0)

        must_do = Gtk.Label()
        # The emoji triangle (⚠️, with the VS16 selector) renders in full colour like
        # the other icons; keep it OUTSIDE the white span or the colour override flattens it.
        must_do.set_markup(
            '<span size="x-large">⚠️</span>  '
            '<span size="medium" foreground="#ffffff"><b>You’ll set your key at '
            'the end: once you finish these setup screens, TuxTalk starts and '
            'your desktop pops up a window to bind it (about 30 seconds). It '
            'won’t dictate until you do.</b></span>'
        )
        must_do.set_halign(Gtk.Align.START)
        must_do.set_line_wrap(True)
        must_do.set_max_width_chars(56)
        # Wrap in a box so the red pulse animation has a surface to paint (a bare
        # GtkLabel does not reliably render background-color in GTK3).
        self.required_pulse_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.required_pulse_box.get_style_context().add_class('tt-required')
        self.required_pulse_box.pack_start(must_do, False, False, 0)
        self.required_pulse_box.set_margin_top(4)
        box.pack_start(self.required_pulse_box, False, False, 0)

        explain = Gtk.Label()
        explain.set_markup(
            '<span size="medium">Typing into your apps then works automatically — no '
            'drivers, groups, or reboot needed. You just pick which key(s) start '
            'dictation. TuxTalk offers <b>two</b>, and you can set either or both:</span>'
        )
        explain.set_halign(Gtk.Align.START)
        explain.set_line_wrap(True)
        explain.set_max_width_chars(58)
        explain.set_margin_start(10)
        explain.set_margin_top(5)
        box.pack_start(explain, False, False, 0)

        keys_label = Gtk.Label()
        keys_label.set_markup(
            '<span size="medium">'
            '🎤 <b>“Hold to talk”</b> — hold it while you speak (push-to-talk)\n'
            '🔁 <b>“Toggle dictation”</b> — tap once to start, tap again to stop'
            '</span>'
        )
        keys_label.set_halign(Gtk.Align.START)
        keys_label.set_line_wrap(True)
        keys_label.set_max_width_chars(58)
        keys_label.set_margin_start(20)
        keys_label.set_margin_top(8)
        box.pack_start(keys_label, False, False, 0)

        # Desktop-specific instructions: precise steps for KDE and GNOME, generic
        # wording for other portal desktops (e.g. Hyprland).
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
        if "KDE" in desktop:
            how_title = "To set your keys on KDE Plasma:"
            steps = (
                "When TuxTalk starts, KDE pops up a <b>“Global Shortcuts "
                "Requested”</b> window for <b>Hold to talk</b> and "
                "<b>Toggle dictation</b>. Click the button next to each, press "
                "the key you want (for example F8, then F9), and confirm.\n"
                "<i>You can change these any time later in "
                "System Settings → Keyboard → Shortcuts (under Applications).</i>"
            )
        elif self.has_gnome or "GNOME" in desktop:
            how_title = "To set your keys on GNOME:"
            steps = (
                "When TuxTalk starts, GNOME pops up an <b>Add Keyboard "
                "Shortcuts</b> window listing <b>Hold to talk</b> and "
                "<b>Toggle dictation</b>. For each one you want to use:\n"
                "1. Click the <b>pencil</b> icon on that row.\n"
                "2. Click <b>Allow</b> on the “inhibit shortcuts” prompt "
                "(that’s just GNOME letting it capture your key).\n"
                "3. Press the key you want (for example F8, then F9).\n"
                "4. Click <b>Add</b> to save.\n"
                "<i>You can change these any time later in "
                "Settings → Keyboard → Keyboard Shortcuts.</i>"
            )
        else:
            how_title = "To set your keys:"
            steps = (
                "When TuxTalk starts (after you finish these screens), your "
                "desktop’s global-shortcuts prompt lets you bind <b>Hold to "
                "talk</b> and <b>Toggle dictation</b> — press the key you want "
                "for each when it appears.\n"
                "<i>If no prompt shows, you can assign them later in your "
                "desktop’s keyboard-shortcut settings, under <b>TuxTalk</b>.</i>"
            )

        how_label = Gtk.Label()
        how_label.set_markup(f'<span size="medium"><b>{how_title}</b></span>')
        how_label.set_halign(Gtk.Align.START)
        how_label.set_margin_start(10)
        how_label.set_margin_top(12)
        box.pack_start(how_label, False, False, 0)

        steps_label = Gtk.Label()
        steps_label.set_markup(f'<span size="medium">{steps}</span>')
        steps_label.set_halign(Gtk.Align.START)
        steps_label.set_line_wrap(True)
        steps_label.set_max_width_chars(58)
        steps_label.set_margin_start(20)
        steps_label.set_margin_top(4)
        box.pack_start(steps_label, False, False, 0)

        # The "type into other apps" consent (the RemoteDesktop portal) differs
        # by desktop, and each has a gotcha worth calling out:
        #  - GNOME defaults its "Allow Remote Interaction" switch to OFF; click
        #    Share without it and dictation types nothing while beeps still play.
        #  - KDE opens a fresh remote-control session per dictation, so it shows a
        #    "Started Remote Desktop" popup EVERY time. "Allow restoring" only
        #    stops the APPROVAL dialog re-asking — the popup is a separate KDE
        #    notification, turned off in System Settings.
        if "KDE" in desktop:
            perm_note = (
                "💡 The first time you dictate, KDE's <b>Remote Control</b> window "
                "asks to allow controlling input — check <b>Allow restoring on "
                "future sessions</b>, then <b>Approve</b> (a one-time OK). KDE also "
                "shows a small notification after each dictation; the <b>last setup "
                "screen</b> shows how to turn it off."
            )
        elif self.has_gnome or "GNOME" in desktop:
            perm_note = (
                "💡 The first time you dictate, GNOME asks to allow typing into "
                "other apps. Turn <b>on</b> the <b>Allow Remote Interaction</b> "
                "switch, keep <b>Remember This Selection</b> checked, and click "
                "<b>Share</b> — just once."
            )
        else:
            perm_note = (
                "💡 The first time you dictate, your desktop will ask permission "
                "to type into other apps — approve it once."
            )
        note = Gtk.Label()
        note.set_markup(f'<span size="medium"><i>{perm_note}</i></span>')
        note.set_halign(Gtk.Align.START)
        note.set_line_wrap(True)
        note.set_max_width_chars(58)
        note.set_margin_start(10)
        note.set_margin_top(10)
        box.pack_start(note, False, False, 0)

        # A bordered frame makes this read as a distinct, mandatory step rather
        # than one of the optional sections below it.
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        frame.set_margin_top(6)
        frame.set_margin_bottom(6)
        frame.add(box)
        vbox.pack_start(frame, False, False, 0)

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

    def _fade_in_dialog(self, opacity):
        """Fade in the dialog gradually."""
        opacity += 0.05
        self.dialog.set_opacity(opacity)

        if opacity < 1.0:
            GLib.timeout_add(25, lambda: self._fade_in_dialog(opacity))

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
        self._fade_in_dialog(0.0)

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


def _hotkey_test_row(title, key):
    """(row, label, button, status) for one key in the hotkey test: "Push-to-
    talk: F8", a Change Key button a screen reader names by its key, and how
    the key tested."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    label = Gtk.Label(xalign=0)
    label.set_markup(f'<b>{title}:</b> {layout_label(key)}')
    label.set_size_request(200, -1)
    button = Gtk.Button(label="Change Key")
    button.set_size_request(120, -1)
    button.get_accessible().set_name(f"Change the {title.lower()} key")
    status = Gtk.Label(xalign=0)
    status.set_markup('<span color="#999999">Not tested</span>')
    row.pack_start(label, False, False, 0)
    row.pack_start(button, False, False, 0)
    row.pack_start(status, True, True, 0)
    return row, label, button, status


def _key_seen_markup(service_can_read):
    """A tested key's status. Only this dialog saw the key when the
    dictation service can't read the keyboards, or reads them through a
    hotkey helper too old for the key (hotkey_test_keys.service_hears)."""
    if service_can_read:
        return '<span color="#4CAF50">✓ Working!</span>'
    return '<span color="#FF9800">✓ Seen, not active yet</span>'


def _hotkey_test_notice(service_can_read):
    """What makes the hotkeys work when the service can't read the keyboard
    yet, or None when it can."""
    if service_can_read:
        return None
    from tuxtalk import uinput_helper
    if uinput_helper._input_group_pending_restart():
        return ("TuxTalk can't read the keyboard yet. These keys will work "
                "once you restart your computer to finish the typing setup.")
    return ("TuxTalk can't read the keyboard yet. These keys will work after "
            "Preferences, Advanced, Fix Typing Permissions, and a restart of "
            "your computer.")


def show_hotkey_test_dialog():
    """
    Show hotkey testing dialog for first-run experience.
    Allows users to test and customize both hotkeys in one place.

    Reads keys through evdev (as the dictation service does) as well as GTK,
    so a key the compositor grabs before GTK sees it still tests as working.

    Returns:
        bool: True if user completed the test
    """
    from tuxtalk.config import HOTKEY_CHOICES, load_config, save_config
    from tuxtalk import gnome_keybindings

    # The service would intercept F8/F9 before we can detect them. The tray
    # already stopped it and keeps it from starting during onboarding; this
    # makes sure, once, just before the dialog appears.
    def kill_dictation_service():
        """Kill a dictation service started since the tray ended the one that
        ran (gracefully, before setup): none should be."""
        try:
            from tuxtalk.service_launcher import find_service_pids, stop_dictation_service
            if not find_service_pids():
                return
            logger.warning("A dictation service started during setup; stopping it")
            stop_dictation_service(force=True)
        except Exception as e:
            logger.warning(f"Error killing service: {e}")

    # Try to import evdev for low-level key detection
    try:
        from evdev import InputDevice, ecodes, list_devices
        EVDEV_AVAILABLE = True
        logger.info("evdev available for hotkey testing")
    except ImportError:
        EVDEV_AVAILABLE = False
        logger.warning("evdev not available - falling back to GTK key events")

    config = load_config()

    # State variables (accessible from threads)
    # Use F8/F9 as suggested defaults if config is empty (first run).
    # What gets saved is decided when the dialog closes (hotkeys_to_save).
    current_hold_key = [config.hotkey if config.hotkey else "F8"]
    current_toggle_key = [config.toggle_hotkey if config.toggle_hotkey else "F9"]
    capturing_key = [None]  # "hold", "toggle", or None
    dialog_ready = [False]
    stop_evdev = threading.Event()
    keyboards = []  # evdev devices, opened just before the dialog shows

    # Build dialog
    dialog = Gtk.Dialog(title="Test Your Hotkeys")
    dialog.set_default_size(550, 420)
    dialog.set_modal(True)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)

    content = dialog.get_content_area()
    content.set_margin_top(20)
    content.set_margin_bottom(20)
    content.set_margin_start(25)
    content.set_margin_end(25)
    content.set_spacing(15)

    # Instructions
    instructions = Gtk.Label()
    instructions.set_markup('<span size="large"><b>Configure Your Hotkeys</b></span>\n\nTest both hotkeys to ensure they work on your system:')
    instructions.set_xalign(0)
    content.pack_start(instructions, False, False, 0)

    # Hotkey configuration box with buttons
    hotkey_config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    hotkey_config_box.set_margin_top(10)

    # Push-to-talk row
    hold_row, hold_label, hold_change_btn, hold_status = _hotkey_test_row(
        "Push-to-talk", current_hold_key[0])
    hotkey_config_box.pack_start(hold_row, False, False, 0)
    toggle_row, toggle_label, toggle_change_btn, toggle_status = _hotkey_test_row(
        "Toggle mode", current_toggle_key[0])
    hotkey_config_box.pack_start(toggle_row, False, False, 0)

    content.pack_start(hotkey_config_box, False, False, 0)

    # Status label — updated by key detection
    ready_label = Gtk.Label()
    ready_label.set_markup('<span>Hold each hotkey, then release to confirm it works</span>')
    ready_label.set_xalign(0)
    ready_label.set_halign(Gtk.Align.CENTER)
    ready_label.set_margin_top(2)
    content.pack_start(ready_label, False, False, 0)

    # Shown when the service can't read the keyboard yet (set before show_all).
    keyboard_notice = Gtk.Label()
    keyboard_notice.set_line_wrap(True)
    keyboard_notice.set_xalign(0)
    keyboard_notice.set_no_show_all(True)
    content.pack_start(keyboard_notice, False, False, 0)

    # Capture instruction (hidden initially)
    capture_instruction = Gtk.Label()
    _capture_prompt = ('<span background="#FFA500" color="white"> Press F1 to F12, Right Ctrl, '
                       'Right Alt or Scroll Lock to set the hotkey... (ESC to cancel) </span>')
    capture_instruction.set_markup(_capture_prompt)
    capture_instruction.set_margin_top(10)
    capture_instruction.set_no_show_all(True)
    content.pack_start(capture_instruction, False, False, 0)

    def update_ui_for_key(keyname, is_capture_mode, action="press"):
        """Update UI when a key is pressed or released.
        action='press'  → no UI change (phantoms only generate presses — ignore them)
        action='release' → show green '✓ Working!' (requires physical key activity)
        """
        if not dialog_ready[0]:
            return False

        logger.info(f"Hotkey test: {action} {loggable_key(keyname)}, capture={is_capture_mode}")

        if is_capture_mode and capturing_key[0]:
            # In capture mode we only care about presses
            if action != "press":
                return False
            if keyname == "Escape":
                capturing_key[0] = None
                capture_instruction.hide()
                capture_instruction.set_markup(_capture_prompt)
                hold_change_btn.set_sensitive(True)
                toggle_change_btn.set_sensitive(True)
                return False

            other = current_toggle_key[0] if capturing_key[0] == "hold" else current_hold_key[0]
            problem = captured_key_problem(keyname, other)
            if problem:
                # Stay in capture mode so the user can press another key.
                capture_instruction.set_markup(
                    f'<span background="#E57373" color="white"> '
                    f'{GLib.markup_escape_text(problem)} (ESC to cancel) </span>')
                return False

            if capturing_key[0] == "hold":
                current_hold_key[0] = keyname
                hold_label.set_markup(f'<b>Push-to-talk:</b> {layout_label(keyname)}')
                hold_status.set_markup(_key_seen_markup(service_hears(keyboards, keyname)))
            elif capturing_key[0] == "toggle":
                current_toggle_key[0] = keyname
                toggle_label.set_markup(f'<b>Toggle mode:</b> {layout_label(keyname)}')
                toggle_status.set_markup(_key_seen_markup(service_hears(keyboards, keyname)))

            capturing_key[0] = None
            capture_instruction.hide()
            capture_instruction.set_markup(_capture_prompt)
            hold_change_btn.set_sensitive(True)
            toggle_change_btn.set_sensitive(True)
        else:
            # Normal testing mode:
            # press → no UI change (phantoms only generate presses, so we ignore them entirely)
            # release → green "✓ Working!" (requires a physical press+release, phantom-proof)
            if action == "release":
                if keyname == current_hold_key[0]:
                    hold_status.set_markup(_key_seen_markup(service_hears(keyboards, keyname)))
                elif keyname == current_toggle_key[0]:
                    toggle_status.set_markup(_key_seen_markup(service_hears(keyboards, keyname)))

        return False  # Don't repeat

    # EVDEV key reading thread
    def evdev_key_reader():
        """Background thread that reads keys via evdev (bypasses Wayland)."""
        reading = list(keyboards)       # its own: service_hears reads keyboards (GTK's thread)
        if not reading:
            return
        logger.info(f"Hotkey test: reading {len(reading)} keyboard(s)")

        # Flush any stale events buffered in the kernel queue before we start
        # monitoring. Without this, a prior F8 press (e.g. from using TuxTalk
        # just before onboarding) would appear as "Working!" immediately.
        flush_pending_events(reading)

        modifier_codes = {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT,
                          ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL,
                          ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA}
        held_modifiers = set()

        while not stop_evdev.is_set():
            try:
                # The helper can hold events already taken in, with nothing
                # left on its socket for select() to see.
                r = [d for d in reading
                     if hasattr(d, "needs_service") and d.needs_service()]
                if not r:
                    r, w, x = select.select(reading, [], [], 0.1)
                # A keyboard unplugged with Ctrl held must not leave Ctrl held.
                for event in read_ready(reading, r, on_gone=held_modifiers.clear):
                    if event.type != ecodes.EV_KEY:
                        continue
                    # Releases count too: they are what marks a key as
                    # working, and GTK never sees a grabbed key.
                    found = evdev_key_action(event, held_modifiers,
                                             modifier_codes, evdev_key_name)
                    if found:
                        keyname, action = found
                        is_capture = capturing_key[0] is not None
                        GLib.idle_add(update_ui_for_key, keyname, is_capture, action)
            except Exception as e:
                if not stop_evdev.is_set():
                    logger.debug(f"Select error: {e}")
                time.sleep(0.1)
        close_devices(keyboards)

    # GTK key handlers: they cover keys when evdev can't open the keyboards.
    # Modifier mask: reject Alt (MOD1), Ctrl, Super — GNOME delivers Alt+F8
    # (begin-resize binding) to focused windows; stripping modifiers would
    # make it look like a plain F8 press, causing a false positive.
    _NO_MODIFIER_MASK = (Gdk.ModifierType.MOD1_MASK |
                         Gdk.ModifierType.CONTROL_MASK |
                         Gdk.ModifierType.SUPER_MASK)
    # The modifier Right Ctrl and Right Alt each set themselves, which their
    # own release carries.
    _OWN_MODIFIER = {"Right Ctrl": Gdk.ModifierType.CONTROL_MASK,
                     "Right Alt": Gdk.ModifierType.MOD1_MASK}

    # Track currently-held hardware key codes to suppress GTK auto-repeat.
    # GTK sends repeated KEY_PRESS events while a key is held — there's no
    # built-in "is_repeat" flag. We ignore a press if the hardware keycode is
    # already in this set, and clear it on KEY_RELEASE.
    _held_keycodes = set()

    def on_key_press(widget, event):
        """A dictation key for the test, or any key while Change Key waits
        for one. Other keys are the dialog's own, for using it without a
        mouse: Tab moves the focus, Space and Enter press the button."""
        if not dialog_ready[0] or event.type != Gdk.EventType.KEY_PRESS:
            return True
        keyname = gtk_key_name(Gtk.accelerator_name(event.keyval, 0), event.hardware_keycode)
        is_capture = capturing_key[0] is not None
        if not is_capture and keyname not in HOTKEY_CHOICES:
            return False
        # Ignore events with Alt, Ctrl, or Super held (e.g. GNOME's Alt+F8 begin-resize)
        if event.state & _NO_MODIFIER_MASK:
            return True
        # Suppress GTK auto-repeat: ignore if this key is already held down
        if event.hardware_keycode in _held_keycodes:
            return True
        _held_keycodes.add(event.hardware_keycode)
        update_ui_for_key(keyname, is_capture, action="press")
        return True

    def on_key_release(widget, event):
        # Clear the held state so the next press is treated as a fresh press
        _held_keycodes.discard(event.hardware_keycode)
        if not dialog_ready[0]:
            return False
        # Release = physical action confirmed — mark the key as working
        keyname = gtk_key_name(Gtk.accelerator_name(event.keyval, 0), event.hardware_keycode)
        if event.state & _NO_MODIFIER_MASK & ~_OWN_MODIFIER.get(keyname, 0):
            return False
        is_capture = capturing_key[0] is not None
        update_ui_for_key(keyname, is_capture, action="release")
        return False

    def on_change(button, which):
        capturing_key[0] = which        # "hold" or "toggle"
        capture_instruction.show()
        hold_change_btn.set_sensitive(False)
        toggle_change_btn.set_sensitive(False)

    hold_change_btn.connect("clicked", on_change, "hold")
    toggle_change_btn.connect("clicked", on_change, "toggle")

    # Always connect the GTK key handlers. They return straight away until
    # dialog_ready is set, so events that arrive before the gate opens are
    # ignored.
    dialog.connect("key-press-event", on_key_press)
    dialog.connect("key-release-event", on_key_release)

    # Info label
    info_label = Gtk.Label()
    info_label.set_markup(
        '<span><i>💡 Both hotkeys will be saved to your configuration.\n'
        'You can change between Push-to-talk and Toggle mode anytime in Preferences.</i></span>'
    )
    info_label.set_line_wrap(True)
    info_label.set_xalign(0)
    info_label.set_margin_top(15)
    content.pack_start(info_label, False, False, 0)

    # Note about conflicts
    conflict_label = Gtk.Label()
    conflict_label.set_markup(
        '<span><i>⚠️ If a key doesn\'t respond, it may be used by another application.</i></span>'
    )
    conflict_label.set_line_wrap(True)
    conflict_label.set_xalign(0)
    conflict_label.set_opacity(0.85)
    content.pack_start(conflict_label, False, False, 0)

    # Continue button
    continue_button = Gtk.Button(label="Continue")

    continue_button.get_style_context().add_class("suggested-action")
    dialog.add_action_widget(continue_button, Gtk.ResponseType.OK)  # keys saved after run()

    # Clear GNOME's Alt+F8/Alt+F7 bindings before show_all(): GNOME may send a
    # synthetic F8 to whichever window has focus while it applies the change,
    # and that must not be this dialog. gnome_keybindings saves the originals
    # to a file first, so a crash can't lose them.
    gnome_saved = gnome_keybindings.disable_for_hotkey_test()
    if gnome_saved:
        time.sleep(0.3)  # let GNOME process the change before we take focus

    # Bound before the try so the finally can always clean it up, however
    # early an exception lands.
    evdev_thread = None
    response = Gtk.ResponseType.NONE

    try:
        # Kill the dictation service BEFORE showing the dialog so its evdev keyboard
        # grab is released cleanly. If we kill it AFTER the dialog gets focus, the
        # kernel releases a stuck F8 key state and floods the dialog with phantom
        # key-repeat events via GTK, making F8 appear "Working!" before being pressed.
        kill_dictation_service()
        time.sleep(0.3)  # Allow kernel/X server to settle keyboard state

        # Opened here rather than in the reader thread, so the very first key
        # already shows whether the service will hear it.
        if EVDEV_AVAILABLE:
            keyboards.extend(open_key_devices(list_devices, InputDevice))
            if not keyboards:
                # With the hotkey helper the user isn't in the "input" group,
                # so the keyboards can't be opened here: hear the keys through
                # the helper, as the dictation service does. Without this the
                # test said the keys wouldn't work until a restart.
                helper = hotkey_test_helper(ecodes)
                if helper is not None:
                    keyboards.append(helper)
        if not keyboards:
            logger.warning("No keyboards found via evdev")
            keyboard_notice.set_markup(
                f'<span color="#FF9800">'
                f'{GLib.markup_escape_text(_hotkey_test_notice(False))}</span>')
            keyboard_notice.show()

        dialog.show_all()

        # Start evdev reader thread
        if keyboards:
            evdev_thread = threading.Thread(target=evdev_key_reader, daemon=True)
            evdev_thread.start()

        # Presses never mark a key as working, only releases do: the phantom
        # events a released keyboard grab produces are presses only.
        dialog_ready[0] = True

        response = dialog.run()
        dialog.destroy()

    finally:
        # Always: gsettings persists across reboots, so a skipped restore
        # would leave Alt+F8/Alt+F7 dead.
        gnome_keybindings.restore(gnome_saved)

        stop_evdev.set()
        if evdev_thread is not None:
            evdev_thread.join(timeout=1.0)
        else:
            close_devices(keyboards)    # the reader thread closes them otherwise

        # Whatever closed the dialog, never leave the settings without a
        # hotkey: the service then watches no key at all.
        confirmed = response == Gtk.ResponseType.OK
        hold, toggle = hotkeys_to_save(config, current_hold_key[0],
                                       current_toggle_key[0], confirmed)
        if (hold, toggle) != (config.hotkey, config.toggle_hotkey):
            config.hotkey, config.toggle_hotkey = hold, toggle
            try:
                save_config(config)
            except Exception as e:
                # save_config refuses to write when the settings file could
                # not be read; onboarding still moves on.
                logger.error(f"Could not save hotkey choice: {e}")

    logger.info("Hotkey test complete")

    return response == Gtk.ResponseType.OK


def _vulkan_speed_check_first_run(model):
    """True if the graphics chip should run *model*: the same speed check (and
    messages) as choosing Vulkan in Preferences.

    Without it, the small graphics built into some processors could end up
    slower than the processor itself. Any error keeps the processor, as
    Preferences does.
    """
    try:
        from tuxtalk import vulkan_setup_dialogs
        return vulkan_setup_dialogs.run_speed_check(None, model)
    except Exception as e:
        logger.warning(f"First-run speed check failed, using the processor: {e}")
        return False


def _download_vulkan_model_first_run(model, result_info=None):
    """Download *model* in whisper.cpp's format (and an engine that can run
    it, if the one there can't). True when both are on disk.

    result_info: optional dict; "cancelled" is set True in it when the user
    cancelled a download or closed the window, which False alone can't tell
    apart from a failed download.
    """
    try:
        from tuxtalk import whisper_vulkan as wv
        from tuxtalk.download_progress_dialog import DownloadTask, UnifiedDownloadDialog
        if wv.is_installed(model):
            return True
        dialog = UnifiedDownloadDialog(
            parent=None, title="Downloading Your Model",
            description="The speech model in the format your graphics engine uses. One-time download.")
        if not wv.is_engine_installed(model):
            dialog.add_task(DownloadTask("Graphics engine", wv.ENGINE_SIZE_TEXT,
                                         wv.make_engine_download_func(model)))
        if wv.model_path(model) is None:
            dialog.add_task(DownloadTask("Speech model", wv.MODEL_FILES[model][1],
                                         wv.make_model_download_func(model)))
        results = dialog.run() or {}
        installed = wv.is_installed(model)
        if (not installed and result_info is not None
                and any(r.get("cancelled") for r in results.values())):
            result_info["cancelled"] = True
        return installed
    except Exception as e:
        logger.warning(f"Vulkan model download failed: {e}")
        return False


def _processor_model(model, result):
    """The model to use when setup ends up on the processor after all. Large-v3
    needs a graphics card (the card greys it out without one), so it becomes
    what the card recommends there: Whisper Small."""
    from tuxtalk import recommend
    return recommend.recommend(result.get("dictation_language") or "en",
                               recommend.Hardware(None), model=model).model


def _ask_retry_download(model):
    """After a failed first-run download: True to try *model* again, False
    to use a smaller model."""
    from .recommend import model_title
    msg = message_dialog(
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.NONE,
        text=f"Try downloading {model_title(model)} again?",
    )
    msg.format_secondary_text(
        "The download didn't finish, often just a slow moment on the network. "
        "You can try again, or use a smaller model for now and switch later.")
    msg.add_button("Use a Smaller Model", Gtk.ResponseType.NO)
    msg.add_button("Try Again", Gtk.ResponseType.YES)
    msg.set_default_response(Gtk.ResponseType.YES)
    answer = msg.run()
    msg.destroy()
    return answer == Gtk.ResponseType.YES


def _tell_using_smaller_model(chosen, used):
    """Say that setup fell back from *chosen* to *used*, and how to go back."""
    from .recommend import model_title
    show_message(None,
                 Gtk.MessageType.INFO,
                 f"Using {model_title(used)} for now",
                 f"{model_title(chosen)} couldn't be downloaded, so TuxTalk will use "
                 f"{model_title(used)}. To switch later, open Preferences, General and "
                 "click Use Recommended.")


# A failed first-run download is offered again at most this many times.
_FIRST_RUN_RETRIES = 2


def _apply_first_run_setup(result):
    """Save the setup the welcome card showed, then download it.

    *result* is WelcomeDialog.run()'s: model, device ("vulkan" or "cpu") and
    dictation_language. On the graphics card this fetches the Vulkan engine
    and the model in its format, then runs the speed check; if any of that
    doesn't work out it falls back to the processor. CUDA is never fetched
    here (it lives in Preferences). The update notice is marked shown, since
    a new install starts on the recommendation already.
    """
    from tuxtalk.config import load_config, save_config
    selected_model = result["model"]
    try:
        config = load_config()
        config.model = selected_model
        config.device = result["device"]
        config.dictation_language = result.get("dictation_language", "")
        config.recommend_notice_shown = True
        save_config(config)
    except Exception as e:
        logger.warning(f"Could not save the first-run setup: {e}")

    logger.info(f"Setting up {selected_model} on {result['device']}")
    from tuxtalk.model_helper import download_model_with_progress, is_model_cached_fast
    try:
        from tuxtalk import whisper_vulkan

        # Check if model is already cached
        already_cached = is_model_cached_fast(selected_model)
        logger.info(f"Model {selected_model} cached status: {already_cached}")

        config = load_config()
        device = config.device
        if device.lower() == "vulkan" and not whisper_vulkan.supports_model(selected_model):
            selected_model = _processor_model(selected_model, result)
            config.model, config.device = selected_model, "cpu"
            device = "cpu"
            save_config(config)
            already_cached = is_model_cached_fast(selected_model)

        # Vulkan: the model comes in whisper.cpp's format instead. If that
        # download doesn't work out, fall back to the processor so setup
        # still ends with a model that works.
        vulkan_ready = False
        download_info = {}
        if device.lower() == "vulkan":
            # One download window: it fetches the engine too when it's missing.
            vulkan_ready = (_download_vulkan_model_first_run(selected_model,
                                                             result_info=download_info)
                            and _vulkan_speed_check_first_run(selected_model))
            # Re-read: the steps above saved what they learned (the speed
            # check records vulkan_slower), and saving the copy loaded before
            # them would quietly undo it.
            config = load_config()
            if not vulkan_ready:
                selected_model = _processor_model(selected_model, result)
                config.model, config.device = selected_model, "cpu"
                device = "cpu"
                save_config(config)
                logger.warning(f"Not using Vulkan after first-run setup; {selected_model} on the processor")
                already_cached = is_model_cached_fast(selected_model)

        compute_type = "float16" if device.lower() == "cuda" else "int8"
        logger.info(f"Download config: device={device}, compute_type={compute_type}")

        if vulkan_ready:
            config.model = selected_model
            save_config(config)
            model = True
            logger.info(f"{selected_model} ready for Vulkan")
        elif already_cached:
            # Already on disk: save the choice and leave loading to the
            # dictation service. Loading a large model here would freeze the
            # window for several seconds.
            logger.info(f"{selected_model} already cached — skipping load, saving config")
            config.model = selected_model
            save_config(config)
            model = True  # Treat as success so the flow continues normally
        elif download_info.get("cancelled"):
            # Cancel in the graphics download window: no processor download
            # straight after it. The service offers that one when it starts.
            model = None
        else:
            # Model not on disk yet — download it with a progress bar
            model = download_model_with_progress(
                selected_model,
                device=device,
                compute_type=compute_type,
                parent=None,
                show_confirmation=False,  # No confirmation - user already chose
                result_info=download_info,
            )

        if model:
            if model is not True:
                # Freshly downloaded and loaded: save the choice and drop the
                # model, which the service loads for itself. A short-lived thread
                # holds a reference so freeing a large model is less likely to
                # stall the GTK thread.
                config.model = selected_model
                save_config(config)
                logger.info(f"Config updated to use {selected_model} model")
                def _free_in_background(m):
                    pass
                threading.Thread(target=_free_in_background, args=(model,), daemon=True).start()
                del model
            logger.info(f"{selected_model} model ready")
        elif download_info.get("cancelled"):
            # "Not now": no surprise downloads after a Cancel. The setting
            # stays, and the service offers the download when it starts.
            logger.info(f"First-run download cancelled; leaving {selected_model} for later")
        else:
            # The download failed and its error was already shown. The user
            # may try it again; otherwise use a smaller model that is already
            # downloaded, or try ONE smaller download, and say so.
            logger.warning(f"Model download failed for {selected_model}")
            outcome = None
            for _ in range(_FIRST_RUN_RETRIES):
                if not _ask_retry_download(selected_model):
                    break
                retry_info = {}
                retried = download_model_with_progress(
                    selected_model, device=device, compute_type=compute_type, parent=None,
                    show_confirmation=False, result_info=retry_info)
                if retried:
                    config.model = selected_model
                    save_config(config)
                    del retried
                    logger.info(f"{selected_model} downloaded on a retry")
                    outcome = "ok"
                    break
                if retry_info.get("cancelled"):
                    logger.info(f"Retry of {selected_model} cancelled; leaving it for later")
                    outcome = "cancelled"
                    break
            fallback_models = ["small", "tiny"] if selected_model not in ["small", "tiny"] else ["tiny"]
            cached = None if outcome else next((m for m in fallback_models if is_model_cached_fast(m)), None)
            if outcome:
                pass
            elif cached:
                logger.info(f"Using cached {cached} model as fallback")
                config.model = cached
                save_config(config)
                _tell_using_smaller_model(selected_model, cached)
            else:
                fallback = fallback_models[0]
                logger.info(f"Attempting fallback to {fallback} model...")
                try:
                    fallback_model = download_model_with_progress(
                        fallback,
                        device=device,
                        compute_type=compute_type,
                        parent=None,
                        show_confirmation=False
                    )
                    if fallback_model:
                        logger.info(f"Fallback to {fallback} model successful")
                        config.model = fallback
                        save_config(config)
                        del fallback_model
                        _tell_using_smaller_model(selected_model, fallback)
                    else:
                        logger.warning(f"Fallback to {fallback} did not download either")
                except Exception as e:
                    logger.warning(f"Error downloading fallback {fallback}: {e}")

    except Exception:
        logger.warning(f"First-run setup of {selected_model} failed", exc_info=True)
        # Try to ensure SOME model is available (tiny on the processor as last
        # resort; whisper.cpp has no copy of tiny, so the device goes too).
        try:
            config = load_config()

            if not is_model_cached_fast("tiny"):
                logger.info("Downloading tiny model as emergency fallback...")
                tiny_model = download_model_with_progress(
                    "tiny",
                    device="cpu",
                    compute_type="int8",
                    parent=None,
                    show_confirmation=False
                )
                if tiny_model:
                    config.model, config.device = "tiny", "cpu"
                    save_config(config)
                    del tiny_model
            else:
                config.model, config.device = "tiny", "cpu"
                save_config(config)
        except Exception:
            logger.warning("Emergency Tiny fallback failed", exc_info=True)


def show_welcome_and_install():
    """
    Show welcome dialog and handle optional installations.
    This is the main entry point for first-run experience.

    Returns:
        dict: Result dictionary with installation status
    """
    # Show the welcome dialog
    result = show_welcome_dialog()

    if not result or not result.get('continue'):
        logger.info("User cancelled welcome dialog")
        return result

    # The GNOME extension is the welcome screen's only optional download; the
    # speech model (and the Vulkan engine) follow the hotkey test.
    install_extension = result.get('install_extension', False)
    extension_installed = False  # Track if extension was successfully installed

    if install_extension:
        try:
            from tuxtalk.download_progress_dialog import show_unified_download_dialog
            download_results = show_unified_download_dialog(
                cuda=False,
                extension=True,
                parent=None
            )
            logger.info(f"Download results: {download_results}")
            extension_installed = download_results.get('GNOME Extension', {}).get('success')
        except Exception as e:
            logger.error(f"Error during installations: {e}", exc_info=True)

    # Show hotkey testing dialog — but NOT on the Flatpak. That dialog detects
    # key presses via evdev (raw /dev/input), which the sandbox blocks ("No
    # keyboards found for evdev"), and the F8/F9 it shows are the evdev defaults,
    # not the portal shortcuts the desktop actually assigns. So it can't test
    # anything useful in the Flatpak — skip it entirely.
    if os.environ.get("FLATPAK_ID"):
        logger.info("Flatpak: skipping evdev hotkey test (portal shortcuts in use)")
    else:
        logger.info("Showing hotkey test dialog")
        show_hotkey_test_dialog()

    # Then download exactly what the welcome card showed.
    if "model" in result:
        _apply_first_run_setup(result)

    from tuxtalk import appimage_copy
    appimage_installed, launcher_created = appimage_copy.put_in_place()

    # Show final "Ready to Go!" screen after successful model download.
    # needs_restart carries the deferred restart reminder here: if the user fixed
    # device permissions during setup (uinput_fixed), typing/hotkeys won't work
    # until a reboot, so the final screen offers "Restart computer now" instead of an
    # interrupting popup earlier in the flow.
    needs_restart = bool(result.get('needs_restart', result.get('uinput_fixed')))

    show_setup_complete_dialog(
        appimage_installed=appimage_installed,
        launcher_created=launcher_created,
        needs_restart=needs_restart,
        extension_installed=extension_installed,
    )

    # On the KDE Flatpak the "Remote control session started" popup starts firing
    # after every dictation. Onboarding text is gone the moment the user clicks
    # through, and they need the steps ON SCREEN while they're in System Settings
    # — so raise the persistent, always-on-top help window now (deferred to the
    # tray's main loop so it's interactive). It's reopenable from Preferences too.
    try:
        # No local `import os` here: it would make os local to the whole
        # function and break its earlier use with UnboundLocalError.
        from .desktop_detect import get_desktop_environment
        if os.environ.get("FLATPAK_ID") and get_desktop_environment() == "kde":
            from .notification_help import show_notification_help_window

            # One-shot, briefly delayed so the shortcut picker appears first
            # (it must return False to run once — returning the truthy window
            # would make the timeout re-fire and keep re-presenting the window).
            def _show_help_once():
                show_notification_help_window()
                return False
            GLib.timeout_add(3000, _show_help_once)
    except Exception as e:
        logger.warning(f"Could not show KDE notification help window: {e}")

    logger.info("First-run setup completed")

    return result


def _open_preferences():
    """Launch the Preferences window as its own process, the same way the
    tray does (service_launcher.launch_preferences).

    Never raises: failing to open Preferences must not prevent the user from
    dismissing the final screen and getting on with dictating.
    """
    try:
        from .service_launcher import launch_preferences
        launch_preferences()
    except Exception as e:
        logger.warning(f"Could not open Preferences: {e}")


def _saved_hold_key():
    """The push-to-talk key in the settings, F8 when there is none yet."""
    try:
        from tuxtalk.config import load_config
        return load_config().hotkey or "F8"
    except Exception:
        return "F8"


def _ready_tips(is_flatpak):
    """The dim lines under Setup Complete's three steps, as markup: the
    keys this user has, where the menu is, and launch at login when on."""
    from .config import load_config
    cfg = load_config()
    tips = ['💡 Say "period", "comma" or "new line" as you talk']
    if not is_flatpak and getattr(cfg, "toggle_hotkey", ""):
        tips.append(f"⏯️ Or tap <b>{GLib.markup_escape_text(cfg.toggle_hotkey)}</b> "
                    "to start, and tap it again to stop")
    # The portal registers only the two dictation keys, so not on the Flatpak.
    if not is_flatpak and getattr(cfg, "paste_last_hotkey", ""):
        tips.append("📋 Typed into the wrong window? Press "
                    f"<b>{GLib.markup_escape_text(cfg.paste_last_hotkey)}</b> to type it again")
    tips.append(f"⚙️ Open the TuxTalk menu ({GLib.markup_escape_text(_menu_location())}) "
                "to change these keys and more")
    # Onboarding turns on launch at login (appimage_copy._start_at_login) and
    # records it only once its file is written: say so, and where to turn it off.
    if getattr(cfg, "launch_at_login", False):
        tips.append("🔁 TuxTalk will start automatically when you log in "
                    "(turn this off in Preferences → General)")
    return tips


def show_setup_complete_dialog(appimage_installed=False, launcher_created=False,
                               needs_restart=False, extension_installed=False):
    """
    Show the final setup complete dialog after model download.
    Tells the user they're ready to start dictating.

    Args:
        appimage_installed: True if AppImage was copied to ~/AppImages/
        launcher_created: True if desktop launcher was created
        needs_restart: True if permissions were changed this run and a restart
            is required before typing/hotkeys work. Only then are the restart
            reminder and "Restart computer now" button shown.
        extension_installed: True if the GNOME extension was installed, which
            starts working after logging out and back in.
    """
    dialog = Gtk.Dialog(title="TuxTalk - Ready!")
    dialog.set_border_width(0)
    from .ui_style import fit_dialog_to_screen
    from .ui_style import workarea_height
    fit_dialog_to_screen(dialog, 480, -1)  # auto-height, resizable; capped when realized
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)

    content = dialog.get_content_area()
    content.set_spacing(0)

    # Scrolls only on a screen too short for it; the buttons stay below.
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_propagate_natural_height(True)
    avail = workarea_height()
    text_cap = max(240, avail - 96 - 80) if avail else None
    if text_cap:
        scrolled.set_max_content_height(text_cap)
    content.pack_start(scrolled, True, True, 0)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    vbox.set_margin_start(25)
    vbox.set_margin_end(25)
    vbox.set_margin_top(20)
    vbox.set_margin_bottom(6)
    scrolled.add(vbox)

    # Header
    header_label = Gtk.Label()
    header_label.set_markup('<span size="x-large"><b>Setup Complete!</b></span>')
    header_label.set_halign(Gtk.Align.CENTER)
    header_label.set_justify(Gtk.Justification.CENTER)
    vbox.pack_start(header_label, False, False, 0)

    subtitle_label = Gtk.Label()
    subtitle_label.set_markup('TuxTalk is ready to use.')
    subtitle_label.set_halign(Gtk.Align.CENTER)
    subtitle_label.set_justify(Gtk.Justification.CENTER)
    subtitle_label.set_margin_top(4)
    vbox.pack_start(subtitle_label, False, False, 0)

    # Show installation info if AppImage was installed (compact)
    if appimage_installed or launcher_created:
        install_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        install_box.set_margin_top(10)

        if appimage_installed:
            path_label = Gtk.Label()
            path_label.set_markup('<span>📁 Installed to: <b>~/AppImages/TuxTalk.AppImage</b></span>')
            path_label.set_halign(Gtk.Align.CENTER)
            install_box.pack_start(path_label, False, False, 0)

        if launcher_created:
            launcher_label = Gtk.Label()
            launcher_label.set_markup('<span>✅ Added to Applications menu</span>')
            launcher_label.set_halign(Gtk.Align.CENTER)
            install_box.pack_start(launcher_label, False, False, 0)

        vbox.pack_start(install_box, False, False, 0)

    # Separator
    sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    sep.set_margin_top(10)
    sep.set_margin_bottom(8)
    vbox.pack_start(sep, False, False, 0)

    # The three steps, then the two things worth knowing on day one.
    instructions_label = Gtk.Label()
    instructions_label.set_markup('<b>🚀 How It Works</b>')
    instructions_label.set_halign(Gtk.Align.START)
    vbox.pack_start(instructions_label, False, False, 0)

    steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    steps_box.set_margin_start(5)
    steps_box.set_margin_top(5)

    is_flatpak = bool(os.environ.get("FLATPAK_ID"))
    _hold_step = ("1. Hold your <b>Hold-to-talk</b> key"
                  if is_flatpak
                  else f"1. Hold <b>{GLib.markup_escape_text(_saved_hold_key())}</b>")
    steps = [
        _hold_step,
        "2. Talk",
        "3. Let go: your words are typed where your cursor is",
    ]

    for step in steps:
        step_label = Gtk.Label()
        step_label.set_markup(f'<span>{step}</span>')
        step_label.set_halign(Gtk.Align.START)
        steps_box.pack_start(step_label, False, False, 0)

    vbox.pack_start(steps_box, False, False, 0)

    tips = _ready_tips(is_flatpak)
    for i, tip in enumerate(tips):
        tip_label = Gtk.Label()
        tip_label.set_markup(f'<span><i>{tip}</i></span>')
        tip_label.set_halign(Gtk.Align.START)
        tip_label.set_line_wrap(True)
        tip_label.set_margin_top(8 if i == 0 else 2)
        tip_label.set_opacity(0.85)
        vbox.pack_start(tip_label, False, False, 0)

    # A single, passive feedback invitation. It lives here on purpose: this
    # screen is seen once and dismissed, so the ask never interrupts dictation
    # later. GTK opens the link in the user's default browser when clicked.
    feedback_label = Gtk.Label()
    feedback_label.set_markup(
        '<span>Once you\'ve tried it out, I\'d love to hear how it\'s '
        'working for you, good or bad. Leave a note on the '
        '<a href="https://github.com/ronb1964/TuxTalk/discussions">'
        'TuxTalk Discussions page</a>.</span>'
    )
    feedback_label.set_halign(Gtk.Align.START)
    feedback_label.set_xalign(0)
    feedback_label.set_margin_top(10)
    feedback_label.set_line_wrap(True)
    feedback_label.set_max_width_chars(55)
    vbox.pack_start(feedback_label, False, False, 0)

    if extension_installed:
        logout_label = Gtk.Label()
        logout_label.set_markup(
            '<span color="#FF9800"><b>Log out and back in</b> to start the '
            'GNOME extension (TuxTalk in the top bar).</span>')
        logout_label.set_halign(Gtk.Align.START)
        logout_label.set_xalign(0)
        logout_label.set_line_wrap(True)
        logout_label.set_margin_top(10)
        vbox.pack_start(logout_label, False, False, 0)

    # Restart reminder — only when permissions were changed this run and won't
    # take effect until a reboot. This is the deferred reminder: it appears here,
    # at the true end of setup (model downloaded, everything installed), instead
    # of interrupting mid-onboarding where the user might reboot too early.
    if needs_restart:
        restart_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        restart_sep.set_margin_top(12)
        restart_sep.set_margin_bottom(8)
        vbox.pack_start(restart_sep, False, False, 0)

        restart_reminder = Gtk.Label()
        restart_reminder.set_markup(
            '<span size="medium"><b>⚠️ One last step: restart your computer</b></span>\n'
            '<span>Typing and hotkeys need a restart to activate. '
            'Logging out is often not enough.</span>'
        )
        restart_reminder.set_line_wrap(True)
        restart_reminder.set_justify(Gtk.Justification.CENTER)
        restart_reminder.set_halign(Gtk.Align.CENTER)
        restart_reminder.set_max_width_chars(50)
        vbox.pack_start(restart_reminder, False, False, 0)

    # Bottom actions. When a restart is owed, the choice IS restart-now vs later
    # (dictation won't work until the reboot, so "Start Using TuxTalk!" would
    # mislead). Otherwise, the normal start/settings choice.
    button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    button_box.set_halign(Gtk.Align.CENTER)
    button_box.set_margin_top(12)

    if needs_restart:
        def on_restart_now(_widget):
            # A reboot closes everything, so confirm first.
            confirm = message_dialog(
                transient_for=dialog, modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text="Restart now?",
            )
            confirm.format_secondary_text(
                "Save any work in your other apps first — this will close everything."
            )
            confirm.add_button("Cancel", Gtk.ResponseType.CANCEL)
            go = confirm.add_button("Restart computer now", Gtk.ResponseType.OK)
            go.get_style_context().add_class("destructive-action")
            resp = confirm.run()
            confirm.destroy()
            if resp != Gtk.ResponseType.OK:
                return
            # Mark onboarding complete BEFORE rebooting. The reboot happens here,
            # inside the dialog, so the normal completion path in tray.py (which
            # calls mark_first_run_complete after this dialog returns) never runs
            # — without this the machine reboots mid-completion and the next
            # launch starts onboarding all over again.
            try:
                from tuxtalk.cuda_helper import mark_first_run_complete
                mark_first_run_complete()
            except Exception as e:
                # If we can't mark completion, DON'T reboot — a reboot now would
                # re-run the whole wizard on next launch. Keep the working session
                # and let the user restart manually once things are healthy.
                logger.error(f"Could not mark first run complete; aborting reboot: {e}")
                show_message(dialog,
                             Gtk.MessageType.WARNING,
                             "Couldn't finish setup",
                             "TuxTalk couldn't save its setup state, so it won't restart "
                             "automatically. Please restart your computer yourself in a "
                             "moment to finish.")
                return
            from tuxtalk import appimage_copy
            appimage_copy.reboot_when_copied(dialog, _widget)

        later_button = Gtk.Button(label="I'll restart later")
        later_button.set_size_request(150, 35)
        later_button.connect("clicked", lambda w: dialog.response(Gtk.ResponseType.OK))
        button_box.pack_start(later_button, False, False, 0)

        restart_button = Gtk.Button(label="Restart computer now")
        restart_button.set_size_request(150, 35)
        restart_button.get_style_context().add_class("suggested-action")
        restart_button.connect("clicked", on_restart_now)
        button_box.pack_start(restart_button, False, False, 0)
        main_button = restart_button
    else:
        # Two ways out: start dictating, or adjust settings. Setup is finished
        # here, so it is safe to send the user to Preferences.
        prefs_button = Gtk.Button(label="Open Preferences")
        prefs_button.set_size_request(150, 35)
        prefs_button.set_tooltip_text(
            "Adjust models, hotkeys, punctuation, the recording indicator and more")

        def on_open_prefs_clicked(_widget):
            # Close this dialog too — leaving it floating above Preferences would
            # sit on top of the window the user just asked for.
            _open_preferences()
            dialog.response(Gtk.ResponseType.OK)

        prefs_button.connect("clicked", on_open_prefs_clicked)
        button_box.pack_start(prefs_button, False, False, 0)

        start_button = Gtk.Button(label="Start Using TuxTalk!")
        start_button.set_size_request(180, 35)
        start_button.get_style_context().add_class("suggested-action")
        start_button.connect("clicked", lambda w: dialog.response(Gtk.ResponseType.OK))
        button_box.pack_start(start_button, False, False, 0)
        main_button = start_button

    # GTK sizes the scroller at its narrowest width, where the wrapped lines
    # are taller, and comes up short: ask for their height at the window's
    # width (less the side margins). Hidden widgets measure 0, so show first.
    vbox.show_all()
    _minimum, natural = vbox.get_preferred_height_for_width(480 - 50)
    scrolled.set_min_content_height(min(natural, text_cap) if text_cap else natural)

    button_box.set_margin_bottom(16)
    content.pack_start(more_below_bar(scrolled), False, False, 0)
    content.pack_start(button_box, False, False, 0)
    # Left to GTK, the focus went to the Discussions link in the scrolling
    # part, and on a short screen the window opened scrolled down to it with
    # "Setup Complete!" cut off, as Welcome did.
    dialog.set_focus(main_button)

    dialog.show_all()
    dialog.run()
    dialog.destroy()


if __name__ == '__main__':
    # Test the welcome dialog
    Gtk.init(None)
    from .ui_style import prepare_windows
    prepare_windows()
    result = show_welcome_dialog()
    print(f"Welcome dialog result: {result}")
