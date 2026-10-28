"""Preferences, Updates & Backup tab: checking for a new version, a backup
file of the settings, the automatic copies (settings_backup.py), and
Uninstall."""
import datetime
import os
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango

from .desktop_detect import is_gnome
from .logger import setup_logger
from .prefs_tab import Tab, dropdown, run_with_progress, subheading
from .ui_style import WARNING_AMBER, show_message

logger = setup_logger(__name__)


def _header(markup):
    """A section's heading, in bold."""
    label = Gtk.Label(xalign=0)
    label.set_markup(f"<b>{markup}</b>")
    return label


def _indented(widget):
    """*widget*, set in under its heading."""
    widget.set_margin_start(10)
    return widget


def _dim(text):
    """A dim line under a heading that says what the section is for."""
    label = Gtk.Label(label=text, xalign=0)
    label.set_line_wrap(True)
    label.get_style_context().add_class("dim-label")
    return _indented(label)


def _separator():
    """The line between two sections."""
    line = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    line.set_margin_top(8)
    line.set_margin_bottom(4)
    return line


def _how_it_updates(install_type):
    """One dim line on how this install updates (the way it was installed
    decides), with the changelog link at its end."""
    from . import update_checker
    package = ("Check for Updates installs the new .deb or .rpm through your package "
               "manager, with your password.")
    how = {
        "appimage": "Updates install and TuxTalk restarts by itself.",
        "rpm": package, "deb": package, "package": package,
        "aur": "Installed from the AUR: update with your AUR helper (yay -S tuxtalk-appimage).",
        "flatpak": "Installed as a Flatpak: update with flatpak update or your software center.",
    }
    row = _indented(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4))
    info = Gtk.Label(label=how.get(install_type, "Development version: update with git pull."),
                     xalign=0)
    info.set_line_wrap(True)
    info.get_style_context().add_class("dim-label")
    row.pack_start(info, False, True, 0)
    changelog_link = Gtk.LinkButton.new_with_label(
        update_checker.get_releases_url(), "Full changelog")
    changelog_link.set_valign(Gtk.Align.CENTER)
    row.pack_start(changelog_link, False, False, 0)
    return row


class UpdatesTab(Tab):
    """The Updates & Backup tab. Loading a backup or an earlier copy closes
    Preferences and restarts dictation with the settings it brings back."""

    def _build_backup(self, vbox):
        """Backup: a file of the settings to keep or take to another computer,
        and the folder Save Backup and Load Backup open in."""
        vbox.pack_start(_separator(), False, False, 0)
        vbox.pack_start(_header("💾 Backup"), False, False, 0)
        vbox.pack_start(_dim("Your settings, snippets, Your Words and stats in one "
                             "file, to keep or to load on another computer."), False, False, 0)
        row = _indented(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10))
        for text, handler in (("Save Backup…", self._on_save_backup_clicked),
                              ("Load Backup…", self._on_load_backup_clicked)):
            button = Gtk.Button(label=text)
            button.connect("clicked", handler)
            row.pack_start(button, False, False, 0)
        self.last_backup_label = Gtk.Label(xalign=0)
        row.pack_start(self.last_backup_label, False, False, 6)
        self._show_last_backup()
        vbox.pack_start(row, False, False, 0)

        # Picked here, or taken from wherever a backup was last saved.
        row = _indented(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10))
        row.pack_start(Gtk.Label(label="Backup folder:", xalign=0), False, False, 0)
        self.backup_folder_label = Gtk.Label(xalign=0)
        self.backup_folder_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        row.pack_start(self.backup_folder_label, True, True, 0)
        change_btn = Gtk.Button(label="Change…")
        change_btn.connect("clicked", self._on_change_backup_folder_clicked)
        row.pack_end(change_btn, False, False, 0)
        self._show_backup_folder()
        vbox.pack_start(row, False, False, 0)

    def _build_earlier(self, vbox):
        """Earlier settings, part of Backup (no line between them): the
        backups in the backup folder and the copies the tray takes when it
        starts (settings_backup.py), to go back to."""
        from . import settings_backup
        heading = _indented(subheading("Earlier settings"))
        heading.set_margin_top(10)
        vbox.pack_start(heading, False, False, 0)
        vbox.pack_start(_dim(f"Your backups in the backup folder, and the copies TuxTalk "
                             f"saves by itself when it starts (the last "
                             f"{settings_backup.KEEP}). Go back to one if a change or an "
                             f"update went wrong."), False, False, 0)
        row = _indented(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10))
        self.earlier_combo = dropdown(Gtk.ComboBoxText())
        # Named by the heading above it, for a screen reader.
        self.earlier_combo.get_accessible().set_name("Earlier settings")
        row.pack_start(self.earlier_combo, False, False, 0)
        self.restore_btn = Gtk.Button(label="Restore")
        self.restore_btn.connect("clicked", self._on_restore_earlier_clicked)
        row.pack_start(self.restore_btn, False, False, 0)
        vbox.pack_start(row, False, False, 0)
        self._fill_restore_list()

    def _fill_restore_list(self):
        """The Earlier settings dropdown: backup files and automatic copies,
        newest first. Refilled after Save Backup or a new backup folder."""
        from . import settings_backup
        self._restore_points = {p.path: p for p in settings_backup.restore_points()}
        self.earlier_combo.remove_all()
        for point in self._restore_points.values():
            what = "your backup" if point.kind == "file" else "automatic"
            self.earlier_combo.append(
                point.path, f"{settings_backup.when_label(point.when)} — {what}")
        if not self._restore_points:
            self.earlier_combo.append("", "None yet")
        self.earlier_combo.set_active(0)
        self.earlier_combo.set_sensitive(bool(self._restore_points))
        self.restore_btn.set_sensitive(bool(self._restore_points))

    def _show_last_backup(self):
        """'Last saved: never' in amber as a nudge, or when it was."""
        from . import settings_backup
        when = settings_backup.last_saved()
        if when:
            self.last_backup_label.set_markup(
                f'<span color="#6db896">Last saved: {settings_backup.when_label(when)}</span>')
        else:
            self.last_backup_label.set_markup(
                f'<span color="{WARNING_AMBER}">Last saved: never</span>')

    def _pick_file(self, title, save_name=None, folder_only=False):
        """The desktop's own file dialog (a portal in the Flatpak), starting
        in the backup folder. Saves when *save_name* is given, picks a folder
        with *folder_only*, opens a file otherwise. The chosen path, or None."""
        from .settings_backup import backup_folder
        if folder_only:
            action = Gtk.FileChooserAction.SELECT_FOLDER
        else:
            action = Gtk.FileChooserAction.SAVE if save_name else Gtk.FileChooserAction.OPEN
        chooser = Gtk.FileChooserNative.new(title, self.window, action, None, None)
        folder = backup_folder()
        if folder:
            chooser.set_current_folder(folder)
        if not folder_only:
            zips = Gtk.FileFilter()
            zips.set_name("TuxTalk backups (*.zip)")
            zips.add_pattern("*.zip")
            chooser.add_filter(zips)
        if save_name:
            chooser.set_current_name(save_name)
            chooser.set_do_overwrite_confirmation(True)
        path = chooser.get_filename() if chooser.run() == Gtk.ResponseType.ACCEPT else None
        chooser.destroy()
        return path

    def _show_backup_folder(self):
        """Show the backup folder (home shortened to ~), or what happens
        while there isn't one yet."""
        from .settings_backup import backup_folder
        folder = backup_folder()
        if folder:
            home = os.path.expanduser("~")
            shown = "~" + folder[len(home):] if folder.startswith(home + os.sep) else folder
            self.backup_folder_label.set_text(shown)
            self.backup_folder_label.set_tooltip_text(folder)
        else:
            self.backup_folder_label.set_markup(
                '<span color="#888888">the folder you save your first backup to</span>')
            self.backup_folder_label.set_tooltip_text(
                "Pick one now with Change…, or save a backup and its folder is used")

    def _on_change_backup_folder_clicked(self, _button):
        """Browse to a folder; saved straight away, like Save Backup itself."""
        from .settings_backup import set_backup_folder
        folder = self._pick_file("Choose the Folder for TuxTalk Backups", folder_only=True)
        if not folder:
            return
        try:
            set_backup_folder(folder)
        except OSError as e:
            show_message(self.window, Gtk.MessageType.ERROR, "The folder wasn't saved", str(e))
            return
        self._show_backup_folder()
        self._fill_restore_list()       # the backups already in that folder

    def _on_save_backup_clicked(self, _button):
        """Save Backup…: the settings, snippets, Your Words and stats in one
        .zip, whose folder becomes the backup folder."""
        from . import settings_backup
        name = settings_backup.suggested_name()
        path = self._pick_file("Save TuxTalk Backup", save_name=name)
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
            # The file dialog only asked about the name as typed, not this one.
            if os.path.exists(path) and show_message(
                    self.window, Gtk.MessageType.QUESTION, "Replace the existing backup?",
                    f"{path} already exists. Saving replaces it.",
                    Gtk.ButtonsType.OK_CANCEL) != Gtk.ResponseType.OK:
                return
        try:
            settings_backup.write_backup(path)
        except (settings_backup.BackupError, OSError) as e:
            show_message(self.window, Gtk.MessageType.ERROR, "The backup wasn't saved", str(e))
            return
        # Saving somewhere else makes that the backup folder too, so the next
        # Save Backup and Load Backup open where this one went.
        try:
            settings_backup.set_backup_folder(os.path.dirname(path))
        except OSError as e:
            logger.warning(f"Could not remember the backup folder: {e}")
        self._show_backup_folder()
        self._show_last_backup()
        self._fill_restore_list()       # saved into the backup folder: listed now
        show_message(self.window, Gtk.MessageType.INFO, "Backup saved",
                     f"Your settings, voice commands and stats are in:\n{path}")

    def _on_load_backup_clicked(self, _button):
        """Load Backup…: after saying when and by which version it was made,
        and asking, the backup replaces the settings (_restore_backup)."""
        from . import settings_backup
        path = self._pick_file("Load TuxTalk Backup")
        if not path:
            return
        try:
            backup = settings_backup.read_backup(path)
        except settings_backup.BackupError as e:
            show_message(self.window, Gtk.MessageType.ERROR, "That file can't be loaded", str(e))
            return
        made = backup.created
        try:
            made = settings_backup.when_label(datetime.datetime.fromisoformat(made))
        except ValueError:
            pass
        if show_message(
                self.window, Gtk.MessageType.QUESTION, "Load these settings?",
                f"This backup was made {made} by TuxTalk "
                f"{backup.version or '(unknown version)'}.\n\n"
                "It replaces your settings, voice commands and Fix a Word words. Its stats "
                "are added to yours. Your current settings are kept as a copy first, so "
                "Earlier settings, on this tab, can undo this.",
                buttons=Gtk.ButtonsType.OK_CANCEL) == Gtk.ResponseType.OK:
            self._restore_backup(backup)

    def _on_restore_earlier_clicked(self, _button):
        """Restore: go back to the copy picked in Earlier settings, after asking."""
        from . import settings_backup
        point = self._restore_points.get(self.earlier_combo.get_active_id() or "")
        if point is None:
            return
        # A backup file carries stats and adds them to these; an automatic
        # copy has none, so rolling back a setting keeps today's count.
        stats_note = ("Its stats are added to yours" if point.kind == "file"
                      else "Your stats stay as they are")
        if show_message(
                self.window, Gtk.MessageType.QUESTION, "Go back to these settings?",
                f"Your settings, voice commands and Fix a Word words go back to how they "
                f"were {settings_backup.when_label(point.when)}. {stats_note}, and your "
                f"current settings are kept as a copy first.",
                buttons=Gtk.ButtonsType.OK_CANCEL) != Gtk.ResponseType.OK:
            return
        path = point.path
        try:
            backup = settings_backup.read_backup(path)
        except settings_backup.BackupError as e:
            show_message(self.window, Gtk.MessageType.ERROR, "That copy can't be restored", str(e))
            return
        self._restore_backup(backup)

    def _restore_backup(self, backup):
        """Put *backup* in place, restart dictation on it, and close without
        saving: this window's own copy of the settings is now out of date and
        would overwrite what was just restored."""
        from . import settings_backup
        try:
            notes = settings_backup.restore(backup)
        except (OSError, settings_backup.BackupError) as e:
            show_message(self.window, Gtk.MessageType.ERROR, "The settings weren't restored",
                         str(e))
            return
        show_message(self.window, Gtk.MessageType.INFO, "Settings restored",
                     "\n\n".join(notes + ["Dictation restarts with them now, and Preferences "
                                           "closes. Open it again to see them."]))
        if not self.win.restart_service():
            show_message(self.window, Gtk.MessageType.WARNING, "Dictation didn't restart",
                         "Use Restart Service in the tray menu to start it with the "
                         "restored settings.")
        self.win.on_cancel(None)

    def build(self):
        """The tab's page. Packed tight so it fits the window without
        scrolling: the version shares a line with Check for Updates, and the
        Update Status box only appears once a check has run."""
        from . import update_checker
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(10)
        vbox.set_margin_bottom(10)
        vbox.pack_start(_header("🔄 Updates"), False, False, 0)
        self._build_version_row(vbox)
        self._build_update_status(vbox)
        auto_check = Gtk.CheckButton(label="Check automatically when TuxTalk starts")
        auto_check.set_active(self.config.get("auto_check_updates", True))
        auto_check.connect("toggled",
                           lambda b: self.update_config("auto_check_updates", b.get_active()))
        auto_check.set_tooltip_text(
            "When enabled, TuxTalk will check for updates once per day when it starts.\n"
            "If an update is found, you'll be notified and this tab will open.")
        vbox.pack_start(_indented(auto_check), False, False, 0)
        install_type = update_checker.get_install_type()
        vbox.pack_start(_how_it_updates(install_type), False, False, 0)
        self._build_backup(vbox)
        self._build_earlier(vbox)
        # A Flatpak is removed another way. The development version shows it
        # too, so the tab looks as users see it; there the button only says
        # it doesn't apply (uninstall.run).
        if install_type != "flatpak":
            vbox.pack_start(_separator(), False, False, 0)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.pack_start(_dim("Remove TuxTalk and everything it set up on this computer."),
                           True, True, 0)
            uninstall_btn = Gtk.Button(label="Uninstall TuxTalk\u2026")
            uninstall_btn.connect("clicked", self._on_uninstall_clicked)
            row.pack_end(uninstall_btn, False, False, 0)
            vbox.pack_start(row, False, False, 0)
        return vbox

    def _build_version_row(self, vbox):
        """TuxTalk's version, and the extension's on GNOME, with Check for Updates."""
        from . import update_checker
        row = _indented(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10))
        versions = f"TuxTalk {update_checker.get_current_version()}"
        if is_gnome():
            ext_version = update_checker.get_extension_version()
            versions += (f"  ·  GNOME extension {ext_version}" if ext_version
                         else "  ·  GNOME extension not installed")
        row.pack_start(Gtk.Label(label=versions, xalign=0), True, True, 0)
        check_btn = Gtk.Button(label="Check for Updates")
        check_btn.connect("clicked", self._on_check_updates_clicked)
        row.pack_end(check_btn, False, False, 0)
        vbox.pack_start(row, False, False, 0)

    def _build_update_status(self, vbox):
        """The result of a check, with its Download & Install, View on GitHub
        and Update Extension buttons. Hidden until a check runs, so the page
        spends no space on an empty box."""
        self.update_status_frame = Gtk.Frame(label="Update Status")
        self.update_status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        for side in ("start", "end", "top", "bottom"):
            getattr(self.update_status_box, f"set_margin_{side}")(10)
        self.update_status_label = Gtk.Label(xalign=0)
        self.update_status_label.set_line_wrap(True)
        self.update_status_box.pack_start(self.update_status_label, False, False, 0)
        self.update_actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.update_actions_box.set_margin_top(10)
        self.download_btn, self.view_release_btn, self.update_extension_btn = (
            self._action_button(label, handler, tip) for label, handler, tip in (
                ("Download & Install", self._on_download_update_clicked,
                 "Download the update and automatically install & restart TuxTalk"),
                ("View on GitHub", self._on_view_release_clicked, None),
                # Only for GNOME, with the extension installed.
                ("Update Extension", self._on_update_extension_clicked, None)))
        self.update_status_box.pack_start(self.update_actions_box, False, False, 0)
        self.update_status_frame.add(self.update_status_box)
        self.update_status_frame.set_no_show_all(True)
        vbox.pack_start(_indented(self.update_status_frame), False, False, 4)
        self._current_release = None        # what Download & Install installs

    def _action_button(self, label, handler, tip):
        """One of Update Status's buttons, shown by a check's result when it applies."""
        button = Gtk.Button(label=label)
        button.connect("clicked", handler)
        button.set_no_show_all(True)
        if tip:
            button.set_tooltip_text(tip)
        self.update_actions_box.pack_start(button, False, False, 0)
        return button

    def _on_uninstall_clicked(self, _button):
        """The running TuxTalk asks and does the work (uninstall.py), so it
        can stop itself and close this window. With none running, this
        window does it. Changes not applied are settled first: the window
        closes once the uninstall goes ahead."""
        if not self._settle_unapplied_changes():
            return
        from . import control
        try:
            if control.open_in_running("uninstall"):
                return
        except Exception as e:
            logger.warning(f"Could not ask TuxTalk to uninstall: {e}")
        from . import uninstall
        if uninstall.run():
            self.win.on_cancel(None)

    def _settle_unapplied_changes(self):
        """Ask about changes not applied, as closing the window does: saved
        (without restarting dictation, which is about to go) or thrown away.
        False when the user keeps editing, or saving failed."""
        win = self.win
        if not win._has_unapplied_changes():
            return True
        answer = win._ask_apply_before_closing()
        if answer == "apply":
            return win.words_tab.save() is not None and bool(win.save_config())
        return answer == "discard"

    def _on_check_updates_clicked(self, button):
        """Check GitHub for a newer TuxTalk, off the GTK thread."""
        from . import update_checker

        # Update status and hide action buttons. The status box is hidden
        # until the first check; show_all skips a no_show_all widget, so
        # that flag goes first (its buttons keep their own and stay hidden).
        self.update_status_frame.set_no_show_all(False)
        self.update_status_frame.show_all()
        self.update_status_label.set_text("Checking for updates...")
        self.download_btn.hide()
        self.view_release_btn.hide()
        self.update_extension_btn.hide()
        button.set_sensitive(False)

        def do_check():
            result = update_checker.check_for_updates()
            GLib.idle_add(lambda: self._handle_update_result(result, button))

        thread = threading.Thread(target=do_check, daemon=True)
        thread.start()

    def _handle_update_result(self, result, button):
        """Show what the update check found (on the GTK thread)."""
        button.set_sensitive(True)

        if not result.get("success"):
            # The error carries exception text and paths, which can hold the
            # & or < that make Pango reject the whole label.
            error = GLib.markup_escape_text(str(result.get("error", "Unknown error")))
            self.update_status_label.set_markup(
                f"<span color='red'>Check failed: {error}</span>"
            )
            return

        has_update = result.get("update_available", False)
        has_ext_update = result.get("extension_update", False)
        current = result.get("current_version", "unknown")
        latest = result.get("latest_version", "unknown")
        ext_current = result.get("extension_current")
        ext_latest = result.get("extension_latest")
        release = result.get("release", {})

        self._current_release = release

        if has_update:
            status_lines = [
                "<span color='#4CAF50'><b>Update available!</b></span>",
                f"TuxTalk: {current} → <b>{latest}</b>"
            ]
        else:
            status_lines = [
                "<span color='#4CAF50'>You're up to date!</span>",
                f"TuxTalk {current} is the latest version."
            ]

        if ext_current is not None:
            if has_ext_update:
                status_lines.append(
                    f"Extension: {ext_current} → <b>{ext_latest}</b> (update available)")
                status_lines.append(
                    "<span size='small'>Note: Extension updates require logout/login</span>")
            else:
                status_lines.append(f"Extension: Version {ext_current} (up to date)")

        self.update_status_label.set_markup("\n".join(status_lines))

        # How TuxTalk was installed decides whether it can auto-update. Only a
        # self-managed AppImage can be swapped in place; package/AUR/Flatpak
        # users get instructions + the release link, never an AppImage download
        # (which fails on package installs, e.g. missing libfuse.so.2).
        from . import update_checker
        install_type = update_checker.get_install_type()
        guidance = update_checker.get_update_guidance(
            install_type, latest, release.get("html_url", ""))

        if has_update:
            method = guidance["update_method"]
            show_download = (
                (method == "appimage_swap" and release.get("appimage_url")) or
                (method == "pkexec_package" and
                 update_checker.get_package_asset(release, install_type)[0]))
            if show_download:
                self.download_btn.show()
            else:
                self.download_btn.hide()
                if method == "manual":
                    extra = guidance["message"]
                    if guidance.get("command"):
                        cmd = GLib.markup_escape_text(guidance["command"])
                        extra += f"\n<tt>{cmd}</tt>"
                    self.update_status_label.set_markup(
                        "\n".join(status_lines) + "\n\n" + extra)
            if release.get("html_url"):
                self.view_release_btn.show()
        else:
            self.download_btn.hide()
            self.view_release_btn.hide()

        if has_ext_update:
            self.update_extension_btn.show()
        else:
            self.update_extension_btn.hide()

    def _restart_after_package_update(self):
        """The package is installed and Restart Now was clicked: what isn't
        saved yet is saved first, as before the AppImage's restart."""
        from . import update_ui
        if self.win.save_before_restart():
            update_ui.restart_via_tray()

    def _on_download_update_clicked(self, _button):
        """Download & Install, by how TuxTalk was installed: a package update
        through the shared pkexec flow, the release page (AUR, Flatpak, the
        development version), or a self-managed AppImage installed here."""
        from . import update_checker
        if not self._current_release:
            return
        install_type = update_checker.get_install_type()
        method = update_checker.get_update_guidance(install_type, "")["update_method"]
        if method == "manual":
            update_checker.open_release_page(self._current_release.get("html_url", ""))
            return
        if method == "pkexec_package":
            from . import update_ui
            update_ui.run_package_update(self._current_release, install_type,
                                         restart_callback=self._restart_after_package_update)
            return
        url = self._current_release.get("appimage_url")
        if not url:
            return
        response = show_message(self.window,
                                Gtk.MessageType.QUESTION,
                                "Download & Install Update?",
                                "TuxTalk will:\n"
                                "1. Download the new version\n"
                                "2. Install it to ~/AppImages/TuxTalk.AppImage\n"
                                "3. Restart automatically\n\n"
                                "Any unsaved preferences changes will be saved first.",
                                buttons=Gtk.ButtonsType.YES_NO)
        if response != Gtk.ResponseType.YES:
            return
        # As the question promised: the restart would lose what isn't saved.
        if not self.win.save_before_restart():
            return
        self._install_appimage(
            url, self._current_release.get("appimage_name", "TuxTalk-update.AppImage"))

    def _install_appimage(self, url, filename):
        """Download the AppImage at *url* (checked against the release's
        SHA256SUMS.txt when it publishes one) and install it, with a progress
        window; on success the tray restarts TuxTalk, which closes this
        window too (update_ui.restart_via_tray)."""
        from . import update_checker

        def work(progress):
            """On its own thread: the download, then the install; (installed, why not)."""
            downloaded_path = update_checker.download_update(
                url, filename, progress,
                checksums_url=self._current_release.get("checksums_url"))
            if not downloaded_path:
                return False, "Download failed"
            progress("Installing update...", 90)
            return update_checker.install_appimage_update(downloaded_path, progress)

        def finish(result):
            """Restart onto the update, or say in Update Status and in a
            message that it didn't go in."""
            success, error_msg = result or (False, None)
            if success:
                from . import update_ui
                update_ui.restart_via_tray()
                return
            error_msg = error_msg or ("Something TuxTalk didn't expect went wrong; "
                                      "the log has the details.")
            self.update_status_label.set_markup(
                f"<span color='red'>Update failed: "
                f"{GLib.markup_escape_text(str(error_msg))}</span>\n"
                f"Please try again or download manually from GitHub.")
            show_message(self.window,
                         Gtk.MessageType.ERROR,
                         "Update Failed",
                         f"{error_msg}\n\n"
                         f"You can try again or download the update manually from GitHub.")

        run_with_progress(self.window, "Updating TuxTalk", "Starting download...", work, finish)

    def _on_view_release_clicked(self, button):
        """Open the release page on GitHub."""
        from . import update_checker

        if self._current_release and self._current_release.get("html_url"):
            update_checker.open_release_page(self._current_release["html_url"])

    def _on_update_extension_clicked(self, button):
        """Download and install the newest GNOME extension, with a progress window."""
        from . import extension_helper

        def finish(success):
            if not success:
                show_message(self.window, Gtk.MessageType.ERROR, "Update Failed",
                             "Failed to update the GNOME extension.\n"
                             "Check the log file for details.")
                return
            show_message(self.window, Gtk.MessageType.INFO, "Extension Updated!",
                         "The GNOME extension has been updated.\n\n"
                         "You need to log out and log back in for the changes to take effect.")
            self.update_extension_btn.hide()
            self.update_status_label.set_markup(
                "<span color='#4CAF50'>Extension updated!</span>\n"
                "Log out and log back in to activate the new version.")

        run_with_progress(self.window, "Updating Extension", "Downloading extension...",
                          extension_helper.download_and_install_extension, finish)
