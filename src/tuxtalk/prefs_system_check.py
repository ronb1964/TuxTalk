"""Preferences, Advanced tab, System check: the graphics card, typing setup
and GNOME extension, each with what it needs fixed, closed while all is well."""
import os
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from .desktop_detect import is_gnome
from .logger import setup_logger
from .prefs_tab import (Tab, fold, run_with_progress,
                        subheading)
from .ui_style import WARNING_AMBER, message_dialog, show_message
from .whisper_vulkan import ENGINE_SIZE_TEXT

logger = setup_logger(__name__)

FIX_TYPING_LABEL = "Fix Typing Permissions"
SWITCH_TO_HELPER_LABEL = "Switch to Hotkey Helper"
UPDATE_HELPER_LABEL = "Update Hotkey Helper"
LEAVE_GROUP_LABEL = "Leave the 'input' Group"

# What the GNOME extension adds, shared by the install and success dialogs so
# the two can't promise different things.
EXTENSION_FEATURES = (
    "  • A panel icon that shows when you're dictating\n"
    "  • Start or stop dictation, Recent Dictations and Fix a Word\n"
    "  • Performance presets and Preferences\n"
    "  • The recording indicator where you put it, on Wayland too"
)


def extension_reload_steps():
    """How to make GNOME Shell load (or drop) the extension in this session.

    Alt+F2 r restarts the shell only in an X11 session; GNOME on Wayland has
    no shell restart, and GNOME 50 has no X11 session at all.
    """
    steps = "Log out and log back in."
    if os.environ.get("XDG_SESSION_TYPE") == "x11":
        steps += "\nOn X11 you can instead press Alt+F2, type r and press Enter."
    return steps


def _hide_widgets(*widgets):
    """Hide widgets and keep them hidden through the window's show_all()."""
    for w in widgets:
        w.set_no_show_all(True)
        w.hide()


_READY = "✓ Ready"


def _typing_state(refused_shortcuts):
    """What System check says about typing and the hotkeys: (markup, the Fix
    Typing button's label, whether it's offered, whether all is well).
    *refused_shortcuts* are the shortcuts the hotkey helper won't take."""
    from . import secure_input, uinput_helper
    from .hotkey_helper import VERSION as HELPER_VERSION
    # Both halves, not just uinput: on Fedora a logind ACL can grant
    # /dev/uinput while /dev/input, which the hotkeys need, stays unreadable.
    has_access, _reason = uinput_helper.check_all_device_permissions()
    running = secure_input.running_version() if has_access else None
    if secure_input.is_older(running) and secure_input.is_installed():
        # A TuxTalk update brought a newer helper: the old one keeps
        # running until it's installed again.
        return (f'<span color="{WARNING_AMBER}">⚠ This TuxTalk brings a newer hotkey helper '
                f'({GLib.markup_escape_text(running)} → {HELPER_VERSION}). '
                'Update it to get its fixes</span>', UPDATE_HELPER_LABEL, True, False)
    if running is not None:
        return _helper_state(refused_shortcuts)
    if has_access:
        return _input_group_state()
    if secure_input.is_installed():
        # The service waits for an installed helper, so a silent one means
        # no hotkeys; installing it again starts it, with no restart.
        return (f'<span color="{WARNING_AMBER}">⚠ TuxTalk\'s hotkey helper is installed but '
                'isn\'t answering, so the dictation key won\'t work. Updating it '
                'starts it again</span>', UPDATE_HELPER_LABEL, True, False)
    return _no_access_state()


def _helper_state(refused_shortcuts):
    """_typing_state with the hotkey helper running."""
    from . import uinput_helper
    if uinput_helper.check_user_in_input_group():
        return (f'<span color="{WARNING_AMBER}">⚠ TuxTalk uses its hotkey helper, but you\'re '
                'still in the \'input\' group, so other programs can still read your '
                'keyboard</span>', LEAVE_GROUP_LABEL, True, False)
    if refused_shortcuts:
        bad = ", ".join(refused_shortcuts)
        return (f'<span color="{WARNING_AMBER}">⚠ {GLib.markup_escape_text(bad)} won\'t work: '
                'with the hotkey helper a shortcut needs two of Ctrl, Alt and Super. '
                'Pick one from the list in General → More shortcuts</span>', _READY, False, False)
    return ('<span color="#4CAF50">✓ Ready. Only TuxTalk\'s hotkey helper reads the '
            'keyboard, and it tells TuxTalk about its hotkeys and Right Ctrl or '
            'Right Alt going down, never what you type</span>', _READY, False, True)


def _input_group_state():
    """_typing_state working through the 'input' group, which lets every
    program read the keyboard: the helper is offered where it can go, but
    nothing changes until asked."""
    from . import secure_input
    if secure_input.can_install()[0]:
        return ('<span color="#4CAF50">✓ Working</span>, through the \'input\' group. '
                'TuxTalk\'s hotkey helper is safer: with it, other programs '
                'can\'t read what you type', SWITCH_TO_HELPER_LABEL, True, True)
    return ('<span color="#4CAF50">✓ Typing permissions configured correctly</span>',
            _READY, False, True)


def _no_access_state():
    """_typing_state without access. "Configured, just needs a restart" is
    only true when BOTH halves of setup were done: the udev rule
    (uinput, typing) AND 'input' group membership in the system database
    (hotkey reading). With only the rule, Fix Typing stays offered: it runs
    usermod."""
    from . import uinput_helper
    if uinput_helper.check_udev_rule_exists() and uinput_helper.check_user_in_input_group():
        return ('<span color="#FF9800">⚠ Permissions configured - '
                'please restart your computer</span>', "✓ Configured", False, False)
    # Without /dev/input the hotkey is never seen, so no mode of output can
    # help; say so rather than offer a fallback.
    hotkey_ok, _ = uinput_helper.check_input_read_permission()
    blocked = "TuxTalk can't type" if hotkey_ok else "The dictation key won't work"
    return (f'<span color="#FF9800">⚠ {blocked} until you run '
            'Fix Typing and restart</span>', FIX_TYPING_LABEL, True, False)


class SystemCheck(Tab):
    """The System check fold. It opens by itself and says so beside its
    button when something needs fixing (report)."""

    def build(self, grid, row):
        """The fold at *row* of the Advanced tab's *grid*; returns the next row.
        The three checks run once the window is up."""
        # The graphics card, typing and GNOME extension status. Closed while
        # all is well; it opens and says so when something needs fixing
        # (report).
        self.fold = fold(grid, row, "Show system check", "Hide system check")
        check = self.fold.content
        self._system_problems = set()
        self.report(None, True)
        row += 1
        crow = self._build_gpu(check, 0)
        crow = self._build_typing(check, crow)
        self._build_extension(check, crow)
        return row

    def _build_gpu(self, check, crow):
        """The graphics card: what TuxTalk found, and CUDA's state; returns the next row."""
        check.attach(subheading("Graphics card"), 0, crow, 2, 1)
        crow += 1
        self.gpu_status_label = Gtk.Label(label="Checking...", xalign=0)
        self.gpu_status_label.set_margin_start(10)
        check.attach(self.gpu_status_label, 0, crow, 2, 1)
        crow += 1

        # CUDA Status Label. There is no download button here: CUDA is chosen
        # like Vulkan, in General → Device, which offers the download.
        self.cuda_status_label = Gtk.Label(label="", xalign=0)
        self.cuda_status_label.set_line_wrap(True)
        self.cuda_status_label.set_margin_start(10)
        self.cuda_status_label.set_margin_bottom(10)
        check.attach(self.cuda_status_label, 0, crow, 2, 1)
        crow += 1

        # The light alternative to CUDA (and the only GPU route on AMD / Intel).
        if self.win.vulkan_offered():
            vulkan_hint = Gtk.Label(xalign=0)
            vulkan_hint.set_line_wrap(True)
            vulkan_hint.set_margin_start(10)
            vulkan_hint.set_margin_top(4)
            vulkan_hint.set_text(
                "Lighter option: choose Vulkan (any GPU) as the Device in General → Change model, "
                f"device or language. It's a {ENGINE_SIZE_TEXT} download instead of 1.4 GB, works "
                "on AMD, Intel and NVIDIA, and was about as fast as CUDA in our tests.")
            vulkan_hint.get_style_context().add_class("dim-label")
            check.attach(vulkan_hint, 0, crow, 2, 1)
            crow += 1

        GLib.timeout_add(500, self._initial_gpu_check)
        return crow

    def _build_typing(self, check, crow):
        """Typing setup: its state and Fix Typing (on the Flatpak, a note); returns the next row."""
        check.attach(subheading("Typing setup"), 0, crow, 2, 1)
        crow += 1
        # The Typing Setup body is host-only — it configures the /dev/uinput
        # permission TuxTalk's virtual keyboard needs. The Flatpak types through the
        # libei portal (nothing to configure), so show a one-line note instead of
        # a "Fix Typing Permissions" button that can't do anything.
        if os.environ.get("FLATPAK_ID"):
            typing_note = Gtk.Label()
            typing_note.set_markup(
                "<span size='small'>Typing works automatically through your "
                "desktop's secure input portal — nothing to set up.</span>")
            typing_note.set_xalign(0)
            typing_note.set_line_wrap(True)
            typing_note.set_margin_start(10)
            typing_note.set_margin_bottom(10)
            check.attach(typing_note, 0, crow, 2, 1)
            crow += 1
        else:
            self.typing_status_label = Gtk.Label(label="Checking...", xalign=0)
            # Wrapped: "Working, through the 'input' group…" would widen the window.
            self.typing_status_label.set_line_wrap(True)
            self.typing_status_label.set_max_width_chars(70)
            self.typing_status_label.set_margin_start(10)
            self.typing_status_label.set_margin_bottom(10)
            check.attach(self.typing_status_label, 0, crow, 2, 1)
            crow += 1

            typing_button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            typing_button_box.set_margin_start(10)

            self.fix_typing_button = Gtk.Button(label=FIX_TYPING_LABEL)
            self.fix_typing_button.connect("clicked", self._on_fix_typing_clicked)
            self.fix_typing_button.get_child().set_xalign(0.5)
            self.fix_typing_button.set_tooltip_text(
                "Configure system permissions for keystroke injection.\n"
                "Required for typing mode to work. Uses /dev/uinput.\n"
                "Will prompt for admin password."
            )
            self.fix_typing_button.set_sensitive(False)
            typing_button_box.pack_start(self.fix_typing_button, False, False, 0)

            check.attach(typing_button_box, 0, crow, 2, 1)
            crow += 1

            GLib.timeout_add(600, self._initial_typing_check)
        return crow

    def _build_extension(self, check, crow):
        """The GNOME extension: its state and buttons, hidden off GNOME; returns the next row."""
        # Everything from here down is about the GNOME Shell extension. On KDE
        # and other desktops it can only say "not available" above three
        # greyed-out buttons, so it is hidden there (see end of method).
        # Hidden rather than skipped: install/uninstall code still updates
        # these widgets, and must not trip over missing attributes.
        extension_header = subheading("GNOME extension")
        check.attach(extension_header, 0, crow, 2, 1)
        crow += 1

        self.extension_status_label = Gtk.Label(label="Checking...", xalign=0)
        self.extension_status_label.set_margin_start(10)
        self.extension_status_label.set_margin_bottom(10)
        check.attach(self.extension_status_label, 0, crow, 2, 1)
        crow += 1

        ext_button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ext_button_box.set_margin_start(10)

        self.install_extension_button = Gtk.Button(label="Install Extension")
        self.install_extension_button.connect("clicked", self._on_install_extension_clicked)
        self.install_extension_button.get_child().set_xalign(0.5)
        self.install_extension_button.set_tooltip_text(
            "Download and install the GNOME Shell extension\n"
            "Adds TuxTalk's menu to the top bar, and keeps the dictation keys from "
            "reaching other apps")
        self.install_extension_button.set_sensitive(False)
        ext_button_box.pack_start(self.install_extension_button, False, False, 0)

        self.uninstall_extension_button = Gtk.Button(label="Uninstall Extension")
        self.uninstall_extension_button.connect("clicked", self._on_uninstall_extension_clicked)
        self.uninstall_extension_button.get_child().set_xalign(0.5)
        self.uninstall_extension_button.set_tooltip_text("Remove GNOME Shell extension")
        self.uninstall_extension_button.set_sensitive(False)
        ext_button_box.pack_start(self.uninstall_extension_button, False, False, 0)

        self.restart_info_button = Gtk.Button(label="Restart Info")
        self.restart_info_button.connect("clicked", self._on_restart_info_clicked)
        self.restart_info_button.get_child().set_xalign(0.5)
        self.restart_info_button.set_tooltip_text(
            "How to restart GNOME Shell to activate extension")
        ext_button_box.pack_start(self.restart_info_button, False, False, 0)

        check.attach(ext_button_box, 0, crow, 2, 1)
        crow += 1

        if is_gnome():
            GLib.timeout_add(500, self._initial_extension_check)
        else:
            _hide_widgets(extension_header, self.extension_status_label, ext_button_box)
        return crow

    def report(self, part, ok):
        """Note whether *part* ("typing", "extension") is fine, and say beside
        the System check button whether anything needs fixing. The fold
        opens by itself when a problem first shows up."""
        if part is not None:
            if ok:
                self._system_problems.discard(part)
            elif part not in self._system_problems:
                self._system_problems.add(part)
                self.fold.set_open(True)
        if self._system_problems:
            self.fold.set_summary(
                f'<span color="{WARNING_AMBER}">⚠ something needs your attention</span>',
                dim=False)
        else:
            self.fold.set_summary(
                '<span color="#4CAF50">✓ everything is set up</span>')

    def _initial_gpu_check(self):
        """Perform initial GPU check when preferences window opens."""
        self.check_gpu_status()
        return False  # Don't repeat

    def check_gpu_status(self):
        """Show the NVIDIA card and whether CUDA's files are installed."""
        try:
            from . import cuda_helper
            gpu_name = self.win.nvidia_gpu()
            # TuxTalk's own download only, not a system-wide CUDA install
            has_cuda = cuda_helper.has_talktype_cuda_libraries()

            if gpu_name:
                if isinstance(gpu_name, str) and len(gpu_name) > 5:
                    self.gpu_status_label.set_markup(
                        f'<span color="#4CAF50">✓ NVIDIA GPU detected: '
                        f'{GLib.markup_escape_text(gpu_name)}</span>')
                else:
                    self.gpu_status_label.set_markup(
                        '<span color="#4CAF50">✓ NVIDIA GPU detected</span>')

                if has_cuda:
                    self.cuda_status_label.set_markup(
                        '<span color="#4CAF50">✓ CUDA libraries installed</span>')
                else:
                    self.cuda_status_label.set_markup(
                        '<span color="#FF9800">CUDA libraries not installed. To download them '
                        '(1.4 GB), choose CUDA as the Device in General → Change model, '
                        'device or language.</span>')
            else:
                self.gpu_status_label.set_markup(
                    '<span color="#9E9E9E">⊘ No NVIDIA GPU detected</span>')
                self.cuda_status_label.set_text("CUDA needs an NVIDIA graphics card.")

        except Exception as e:
            self.gpu_status_label.set_text(f"Error checking GPU: {e}")
            self.cuda_status_label.set_text("")
            logger.warning(f"Could not check the graphics card: {e}")

    def _initial_typing_check(self):
        """Perform initial typing permission check when preferences window opens."""
        self._check_typing_status()
        return False  # Don't repeat

    def _check_typing_status(self):
        """Show the typing status, and in the System check line whether it needs fixing."""
        self.report("typing", self._show_typing_status())

    def _show_typing_status(self):
        """Show the typing permission status; True when nothing needs fixing."""
        try:
            markup, label, offered, ok = _typing_state(self._shortcuts_the_helper_refuses())
        except Exception as e:
            self.typing_status_label.set_text(f"Error checking typing: {e}")
            logger.warning(f"Could not check typing: {e}")
            return False
        self.typing_status_label.set_markup(markup)
        self.fix_typing_button.set_label(label)
        self.fix_typing_button.set_sensitive(offered)
        return ok

    def _on_fix_typing_clicked(self, button):
        """Fix Typing (or Switch to / Update the hotkey helper): say what it
        does, then run it with the admin password, off the GTK thread."""
        if button.get_label() == LEAVE_GROUP_LABEL:
            self._leave_input_group(button)
            return
        try:
            from . import secure_input

            lines, restarts = secure_input.fix_description()
            note = secure_input.fallback_note()     # on the "input" group path
            switching = button.get_label() == SWITCH_TO_HELPER_LABEL
            updating = button.get_label() == UPDATE_HELPER_LABEL
            response = show_message(
                self.window, Gtk.MessageType.QUESTION,
                "Switch to the hotkey helper?" if switching else "Configure Typing Permissions?",
                ("This sets TuxTalk up so the hotkeys and typing work without the "
                 "'input' group, and takes you out of that group, so other programs "
                 "can't read your keyboard. (If another program you use needs the "
                 "group, a key remapper for instance, add yourself back.)\n\n"
                 if switching else
                 "This updates TuxTalk's hotkey helper.\n\n" if updating else
                 "This will configure system permissions to allow TuxTalk to type "
                 "directly into applications.\n\n")
                + "What this does:\n"
                + "".join(f"• {line}\n" for line in lines)
                + (f"\n{note}\n" if note else "")
                + "\nYou will be prompted for your admin password.\n\n"
                + ("After setup, restart your computer for the changes to take "
                   "effect. Logging out and back in is often not enough — a "
                   "background session can keep the old permissions alive.\n\n"
                   if restarts else "No restart is needed.\n\n")
                + "Continue?",
                buttons=Gtk.ButtonsType.YES_NO)
            if response != Gtk.ResponseType.YES:
                return

            button.set_sensitive(False)
            button.set_label("Setting up...")
            if updating:
                from .update_offers import update_helper
                update_helper(lambda ok, message: self._finish_update(button, ok, message))
                return
        except Exception as e:
            logger.warning(f"Could not fix typing permissions: {e}")
            button.set_sensitive(True)
            button.set_label(FIX_TYPING_LABEL)
            return

        # pkexec waits for the password prompt, up to a minute: off the GTK
        # thread, or the window freezes and "Setting up..." is never drawn.
        def work():
            needs_restart = True
            try:
                if switching:
                    success, message = secure_input.install_with_pkexec(leave_group=True)
                    needs_restart = False
                else:
                    success, message, needs_restart = secure_input.fix_permissions()
            except Exception as e:
                logger.warning(f"Fix Typing failed: {e}")
                success, message = False, str(e)
            GLib.idle_add(self._finish_fix_typing, button, success, message, needs_restart,
                          switching)

        threading.Thread(target=work, daemon=True).start()

    def _shortcuts_the_helper_refuses(self):
        """The Voice Commands / Paste Last shortcuts (only a hand-edited
        setting can be one) that the hotkey helper won't report."""
        from .hotkey_helper_client import refused_shortcut
        config = self.config
        return [value for value in (config.get(key, "") or ""
                                    for key in ("voice_commands_hotkey", "paste_last_hotkey"))
                if refused_shortcut(value)]

    def _leave_input_group(self, button):
        """Take the user out of the input group once the helper is doing its job."""
        from . import secure_input
        answer = show_message(
            self.window,
            Gtk.MessageType.QUESTION,
            "Leave the 'input' group?",
            "TuxTalk doesn't need it any more: its hotkey helper reads the hotkeys. "
            "Leaving it means other programs can't read your keyboard.\n\n"
            "If some other program you use needs the group (a key remapper, for "
            "instance), it will stop working until you add yourself back.\n\n"
            "It takes effect after you restart your computer. "
            "You will be prompted for your admin password.",
            buttons=Gtk.ButtonsType.YES_NO)
        if answer != Gtk.ResponseType.YES:
            return
        button.set_sensitive(False)

        def work():
            ok, message = secure_input.leave_input_group_with_pkexec()
            GLib.idle_add(self._finish_leave_group, button, ok, message)

        threading.Thread(target=work, daemon=True).start()

    def _finish_leave_group(self, button, ok, message):
        from .secure_input import STILL_IN_GROUP
        if ok and message == STILL_IN_GROUP:
            show_message(self.window, Gtk.MessageType.WARNING, "You're still in the 'input' group",
                         "TuxTalk couldn't take you out of it. Your system may keep "
                         "that group somewhere TuxTalk can't change.")
        elif ok:
            show_message(self.window, Gtk.MessageType.INFO, "You've left the 'input' group",
                         "Restart your computer to finish. Until then, programs that "
                         "started before keep the old access.")
        elif message != "cancelled":
            show_message(self.window, Gtk.MessageType.ERROR, "That didn't work", message)
        self._check_typing_status()
        return False

    def _finish_update(self, button, ok, message):
        """Back on the GTK thread after Update Hotkey Helper. Dictation isn't
        restarted: it would throw away a dictation under way, and the
        service's helper connection comes back by itself (update_helper)."""
        if not ok:
            self._finish_fix_typing(button, ok, message, needs_restart=False)
            return
        show_message(self.window, Gtk.MessageType.INFO, "Hotkey helper updated",
                     "Your dictation keys use it straight away. No restart is needed.")
        self._check_typing_status()

    def _finish_fix_typing(self, button, success, message, needs_restart=True, switching=False):
        """Back on the GTK thread: report how Fix Typing Permissions went."""
        from .secure_input import STILL_IN_GROUP
        still_in_group = success and message == STILL_IN_GROUP
        still_in_group_text = (
            "TuxTalk couldn't take you out of the 'input' group, so other programs "
            "can still read your keyboard. Your system may keep that group "
            "somewhere TuxTalk can't change. Your hotkeys work either way.")
        if success and not needs_restart:
            show_message(self.window,
                         Gtk.MessageType.INFO,
                         "Hotkeys and Typing Set Up!",
                         "TuxTalk's hotkey helper is installed, and TuxTalk can type into "
                         "your applications. No restart is needed.\n\n"
                         + (still_in_group_text if still_in_group else
                            "You've also left the 'input' group. Restart your computer when "
                            "it suits you: until then, programs that were already running "
                            "keep the old access." if switching else
                            "Dictation restarts now to use it."))
            # The service picks its hotkey source when it starts.
            self.win.restart_service()
            self._check_typing_status()
        elif success:
            show_message(self.window,
                         Gtk.MessageType.INFO,
                         "Typing Permissions Configured!",
                         "The system has been configured for keystroke injection.\n\n"
                         "IMPORTANT: Restart your computer to apply the changes.\n"
                         "(Logging out and back in is often not enough.)\n"
                         "(Some systems may require a full reboot instead.)\n\n"
                         "After that, TuxTalk will be able to type "
                         "directly into your applications.")
            self._check_typing_status()
        else:
            # The state names the button again: after a cancelled Switch to
            # (or Update) Hotkey Helper, the plain fix would leave the user
            # in the input group.
            self._check_typing_status()
            if "cancelled" in message.lower():
                self.typing_status_label.set_markup(
                    '<span color="#FF9800">⚠ Setup cancelled. Click '
                    f'{button.get_label()} to try again</span>'
                )
            else:
                show_message(self.window, Gtk.MessageType.ERROR, "Setup Failed", message)
        return False

    def _initial_extension_check(self):
        """Perform initial extension check when preferences window opens."""
        self._check_extension_status()
        return False  # Don't repeat

    def _check_extension_status(self):
        """Show the extension status, and in the System check line whether it needs fixing."""
        self.report("extension", self._show_extension_status())

    def _show_extension_status(self):
        """Show the GNOME extension's status; True unless it needs fixing.
        Not installed is a choice (the tray works without it), not a problem."""
        try:
            from . import extension_helper
            status = extension_helper.get_extension_status()

            if not status['available']:
                self.extension_status_label.set_markup(
                    '<span color="#9E9E9E">⊘ Not available (requires GNOME desktop)</span>')
                self.install_extension_button.set_sensitive(False)
                self.uninstall_extension_button.set_sensitive(False)
                # Its dialog explains how to reload GNOME Shell, which
                # is meaningless off GNOME — on KDE or any other desktop it is
                # advice for software that isn't running.
                self._set_restart_info_sensitive(False)
                return True
            if status['installed']:
                if status['enabled']:
                    self.extension_status_label.set_markup(
                        '<span color="#4CAF50">✓ Extension installed and enabled</span>')
                else:
                    self.extension_status_label.set_markup(
                        '<span color="#FF9800">⚠ Extension installed but not enabled</span>')
                self.install_extension_button.set_label("✓ Installed")
                self.install_extension_button.set_sensitive(False)
                self.uninstall_extension_button.set_sensitive(True)
                self._set_restart_info_sensitive(True)
                return status['enabled']

            self.extension_status_label.set_markup(
                '<span color="#FF9800">⊘ Extension not installed</span>')
            # It said "✓ Installed" until an uninstall in this window.
            self.install_extension_button.set_label("Install Extension")
            self.install_extension_button.set_sensitive(True)
            self.uninstall_extension_button.set_sensitive(False)
            self._set_restart_info_sensitive(True)
            return True

        except Exception as e:
            self.extension_status_label.set_text(f"Error checking extension: {e}")
            logger.warning(f"Could not check the GNOME extension: {e}")
            return False

    def _set_restart_info_sensitive(self, sensitive):
        """Restart Info works only where there's a GNOME Shell to restart."""
        self.restart_info_button.set_sensitive(sensitive)
        self.restart_info_button.set_tooltip_text(
            "How to restart GNOME Shell to activate extension"
            if sensitive
            else "Only applies to GNOME Shell, which this desktop is not running"
        )

    def _on_install_extension_clicked(self, button):
        """Install Extension: what it adds, and install it if asked to."""
        dialog = message_dialog(
            transient_for=self.window,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Install GNOME Extension?"
        )
        dialog.format_secondary_markup(
            "<b>TuxTalk GNOME Shell Extension</b>\n\n"
            "✨ <b>Features:</b>\n"
            f"{EXTENSION_FEATURES}\n\n"
            "⚠️  <b>Note:</b> it starts working after you log out and back in.\n\n"
            "Install now?"
        )

        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            self._install_extension(button)

    def _install_extension(self, button):
        """Download and install the extension with a progress window, then
        say how to make GNOME load it."""
        from . import extension_helper
        button.set_sensitive(False)

        def finish(success):
            """What to do next, or why it failed."""
            if success:
                self._check_extension_status()
                done = message_dialog(
                    transient_for=self.window, flags=0, message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK, text="Extension Installed Successfully!")
                done.format_secondary_markup(
                    "<b>Next Steps:</b>\n\n"
                    "1. To activate the extension: "
                    f"{GLib.markup_escape_text(extension_reload_steps())}\n\n"
                    "2. The TuxTalk icon will appear in your top panel\n\n"
                    "3. The icon gives you:\n"
                    f"{EXTENSION_FEATURES}")
                done.run()
                done.destroy()
            else:
                button.set_sensitive(True)
                show_message(self.window, Gtk.MessageType.ERROR, "Installation Failed",
                             "Failed to install GNOME extension.\n\n"
                             "Please check your internet connection and try again.")

        run_with_progress(self.window, "Installing Extension", "Preparing installation...",
                          extension_helper.download_and_install_extension, finish)

    def _on_uninstall_extension_clicked(self, button):
        """Remove the GNOME extension, once the user says yes."""
        from . import extension_helper
        response = show_message(
            self.window, Gtk.MessageType.WARNING, "Uninstall GNOME Extension?",
            "This will remove the TuxTalk GNOME Shell extension.\n\n"
            "The main TuxTalk application will continue to work normally.\n\n"
            "Uninstall?",
            buttons=Gtk.ButtonsType.YES_NO)
        if response == Gtk.ResponseType.YES:
            success = extension_helper.uninstall_extension()

            if success:
                self._check_extension_status()

                show_message(self.window,
                             Gtk.MessageType.INFO,
                             "Extension Uninstalled",
                             "The GNOME extension has been removed.\n\n"
                             "To finish: " + extension_reload_steps())
            else:
                show_message(self.window,
                             Gtk.MessageType.ERROR,
                             "Uninstall Failed",
                             "Failed to uninstall the extension.")

    def _on_restart_info_clicked(self, button):
        """Show information about restarting GNOME Shell."""
        dialog = message_dialog(
            transient_for=self.window,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="How to Restart GNOME Shell"
        )
        dialog.format_secondary_markup(
            "<b>To activate or update the extension:</b>\n\n"
            + GLib.markup_escape_text(extension_reload_steps())
        )
        dialog.run()
        dialog.destroy()
