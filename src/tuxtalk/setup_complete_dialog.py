"""Setup Complete, the last first-run screen: what was installed, the three
steps of dictating, a few tips, and either a way into Preferences or, when
the typing setup needs it, the restart.
"""

import os

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk

from . import desktop_detect, extension_helper
from .config import saved_hold_key
from .logger import setup_logger
from .ui_style import more_below_bar, show_message

logger = setup_logger(__name__)

_WIDTH = 480


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


def menu_location() -> str:
    """Where this desktop shows TuxTalk's menu, for the welcome screen's and
    Setup Complete's tips: plain GNOME has no tray, so "the tray icon" sent
    people looking for nothing."""
    if extension_helper.is_extension_enabled():
        return "its icon in the top bar"
    if desktop_detect.tray_host_available():
        return "its tray icon"
    return "open TuxTalk again from your apps"


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
    tips.append(f"⚙️ Open the TuxTalk menu ({GLib.markup_escape_text(menu_location())}) "
                "to change these keys and more")
    # Onboarding turns on launch at login (appimage_copy._start_at_login) and
    # records it only once its file is written: say so, and where to turn it off.
    if getattr(cfg, "launch_at_login", False):
        tips.append("🔁 TuxTalk will start automatically when you log in "
                    "(turn this off in Preferences → General)")
    return tips


def _centered(markup, top=0):
    """A centered line of markup."""
    label = Gtk.Label()
    label.set_markup(markup)
    label.set_halign(Gtk.Align.CENTER)
    label.set_justify(Gtk.Justification.CENTER)
    label.set_margin_top(top)
    return label


def _install_summary(vbox, appimage_installed, launcher_created):
    """Where the AppImage went and the menu entry, when first run did that."""
    if not (appimage_installed or launcher_created):
        return
    install_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    install_box.set_margin_top(10)
    if appimage_installed:
        install_box.pack_start(_centered(
            '<span>📁 Installed to: <b>~/AppImages/TuxTalk.AppImage</b></span>'), False, False, 0)
    if launcher_created:
        install_box.pack_start(_centered('<span>✅ Added to Applications menu</span>'),
                               False, False, 0)
    vbox.pack_start(install_box, False, False, 0)


def _how_it_works(vbox, is_flatpak):
    """The three steps, naming the hold key the user has."""
    heading = Gtk.Label()
    heading.set_markup('<b>🚀 How It Works</b>')
    heading.set_halign(Gtk.Align.START)
    vbox.pack_start(heading, False, False, 0)

    steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    steps_box.set_margin_start(5)
    steps_box.set_margin_top(5)
    hold_step = ("1. Hold your <b>Hold-to-talk</b> key" if is_flatpak
                 else f"1. Hold <b>{GLib.markup_escape_text(saved_hold_key())}</b>")
    for step in (hold_step, "2. Talk", "3. Let go: your words are typed where your cursor is"):
        step_label = Gtk.Label()
        step_label.set_markup(f'<span>{step}</span>')
        step_label.set_halign(Gtk.Align.START)
        steps_box.pack_start(step_label, False, False, 0)
    vbox.pack_start(steps_box, False, False, 0)


def _tips(vbox, is_flatpak):
    """What is worth knowing on day one (_ready_tips), dim, under the steps."""
    for i, tip in enumerate(_ready_tips(is_flatpak)):
        tip_label = Gtk.Label()
        tip_label.set_markup(f'<span><i>{tip}</i></span>')
        tip_label.set_halign(Gtk.Align.START)
        tip_label.set_line_wrap(True)
        tip_label.set_margin_top(8 if i == 0 else 2)
        tip_label.set_opacity(0.85)
        vbox.pack_start(tip_label, False, False, 0)


def _note(markup, width=-1):
    """A wrapped, left-aligned note set apart from the line above."""
    label = Gtk.Label()
    label.set_markup(markup)
    label.set_halign(Gtk.Align.START)
    label.set_xalign(0)
    label.set_margin_top(10)
    label.set_line_wrap(True)
    label.set_max_width_chars(width)
    return label


def _notes(vbox, extension_installed):
    """The feedback invitation, and the log-out note when the GNOME
    extension went in (it starts after logging out and back in)."""
    # A single, passive feedback invitation. It lives here on purpose: this
    # screen is seen once and dismissed, so the ask never interrupts dictation
    # later. GTK opens the link in the user's default browser when clicked.
    vbox.pack_start(_note(
        '<span>Once you\'ve tried it out, I\'d love to hear how it\'s '
        'working for you, good or bad. Leave a note on the '
        '<a href="https://github.com/ronb1964/TuxTalk/discussions">'
        'TuxTalk Discussions page</a>.</span>', width=55), False, False, 0)
    if extension_installed:
        vbox.pack_start(_note(
            '<span color="#FF9800"><b>Log out and back in</b> to start the '
            'GNOME extension (TuxTalk in the top bar).</span>'), False, False, 0)


def _restart_reminder(vbox):
    """The deferred restart reminder. It appears here, at the true end of
    setup (model downloaded, everything installed), instead of interrupting
    mid-onboarding where the user might reboot too early."""
    restart_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    restart_sep.set_margin_top(12)
    restart_sep.set_margin_bottom(8)
    vbox.pack_start(restart_sep, False, False, 0)

    reminder = _centered(
        '<span size="medium"><b>⚠️ One last step: restart your computer</b></span>\n'
        '<span>Typing and hotkeys need a restart to activate. '
        'Logging out is often not enough.</span>')
    reminder.set_line_wrap(True)
    reminder.set_max_width_chars(50)
    vbox.pack_start(reminder, False, False, 0)


def _confirm_and_restart(dialog, button):
    """Restart computer now: ask first (a reboot closes everything), mark
    first run done, and reboot once the AppImage copy has finished."""
    resp = show_message(
        dialog, Gtk.MessageType.QUESTION, "Restart now?",
        "Save any work in your other apps first — this will close everything.",
        [("Cancel", Gtk.ResponseType.CANCEL), ("Restart computer now", Gtk.ResponseType.OK)],
        destructive=Gtk.ResponseType.OK)
    if resp != Gtk.ResponseType.OK:
        return
    # Mark onboarding complete BEFORE rebooting. The reboot happens here,
    # inside the dialog, so the normal completion path in tray.py (which
    # calls mark_first_run_complete after this dialog returns) never runs
    # — without this the machine reboots mid-completion and the next
    # launch starts onboarding all over again.
    try:
        from tuxtalk.first_run_marker import mark_first_run_complete
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
    appimage_copy.reboot_when_copied(dialog, button)


def _button(label, width, on_click, main=False):
    """A bottom button of the given width; *main* is the suggested one."""
    button = Gtk.Button(label=label)
    button.set_size_request(width, 35)
    if main:
        button.get_style_context().add_class("suggested-action")
    button.connect("clicked", on_click)
    return button


def _buttons(dialog, needs_restart, model_ready=True):
    """(box, main button). When a restart is owed, the choice IS restart now
    or later: dictation won't work until the reboot, so "Start Using
    TuxTalk!" would mislead. Otherwise start dictating, or adjust settings."""
    button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    button_box.set_halign(Gtk.Align.CENTER)
    button_box.set_margin_top(12)
    button_box.set_margin_bottom(16)

    def close(_button):
        dialog.response(Gtk.ResponseType.OK)

    if needs_restart:
        left = _button("I'll restart later", 150, close)
        main = _button("Restart computer now", 150,
                       lambda button: _confirm_and_restart(dialog, button), main=True)
    else:
        def open_preferences(_button):
            # Close this dialog too — leaving it floating above Preferences would
            # sit on top of the window the user just asked for.
            _open_preferences()
            dialog.response(Gtk.ResponseType.OK)
        # Setup is finished here, so it is safe to send the user to Preferences.
        left = _button("Open Preferences", 150, open_preferences)
        left.set_tooltip_text(
            "Adjust models, hotkeys, punctuation, the recording indicator and more")
        # Without a model there's nothing to start using yet.
        main = _button("Start Using TuxTalk!" if model_ready else "Continue", 180, close,
                       main=True)
    button_box.pack_start(left, False, False, 0)
    button_box.pack_start(main, False, False, 0)
    return button_box, main


def _text_area(content):
    """(scrolled window, box inside it, height cap or None): the text
    scrolls only on a screen too short for it; the buttons stay below."""
    from .ui_style import workarea_height
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
    return scrolled, vbox, text_cap


def show_setup_complete_dialog(appimage_installed=False, launcher_created=False,
                               needs_restart=False, extension_installed=False,
                               model_ready=True):
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
        model_ready: False when no speech model was downloaded (cancelled,
            or no download worked). The screen then says so instead of
            "ready to use".
    """
    from .ui_style import fit_dialog_to_screen
    dialog = Gtk.Dialog(title="TuxTalk - Ready!" if model_ready else "TuxTalk - Setup Complete")
    dialog.set_border_width(0)
    fit_dialog_to_screen(dialog, _WIDTH, -1)  # auto-height, resizable; capped when realized
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)
    content = dialog.get_content_area()
    content.set_spacing(0)
    scrolled, vbox, text_cap = _text_area(content)

    vbox.pack_start(_centered('<span size="x-large"><b>Setup Complete!</b></span>'),
                    False, False, 0)
    vbox.pack_start(_centered('TuxTalk is ready to use.' if model_ready else
                              '<span color="#FF9800"><b>The speech model isn\'t downloaded '
                              'yet.</b>\nTuxTalk asks to download it when it starts.</span>',
                              top=4), False, False, 0)
    _install_summary(vbox, appimage_installed, launcher_created)
    sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    sep.set_margin_top(10)
    sep.set_margin_bottom(8)
    vbox.pack_start(sep, False, False, 0)
    is_flatpak = bool(os.environ.get("FLATPAK_ID"))
    _how_it_works(vbox, is_flatpak)
    _tips(vbox, is_flatpak)
    _notes(vbox, extension_installed)
    if needs_restart:
        _restart_reminder(vbox)
    button_box, main_button = _buttons(dialog, needs_restart, model_ready)

    # GTK sizes the scroller at its narrowest width, where the wrapped lines
    # are taller, and comes up short: ask for their height at the window's
    # width (less the side margins). Hidden widgets measure 0, so show first.
    vbox.show_all()
    _minimum, natural = vbox.get_preferred_height_for_width(_WIDTH - 50)
    scrolled.set_min_content_height(min(natural, text_cap) if text_cap else natural)

    content.pack_start(more_below_bar(scrolled), False, False, 0)
    content.pack_start(button_box, False, False, 0)
    # Left to GTK, the focus went to the Discussions link in the scrolling
    # part, and on a short screen the window opened scrolled down to it with
    # "Setup Complete!" cut off, as Welcome did.
    dialog.set_focus(main_button)

    dialog.show_all()
    dialog.run()
    dialog.destroy()
