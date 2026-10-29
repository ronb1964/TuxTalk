"""Updating TuxTalk from its windows: the tray's Check for Updates and its
once-a-day check, the Update Available window, and the two ways an update
goes in, shared with Preferences → Updates & Backup so both behave the same.

A self-managed AppImage is downloaded and swapped in place. A package
(.rpm/.deb) install gets the release's package, installed as root via pkexec
(the desktop's PolicyKit password prompt) so it goes through the system
package manager: dependencies resolved, package database kept correct.
"""
import logging
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402
from .ui_style import message_dialog, show_message  # noqa: E402

from . import notes_text, update_checker  # noqa: E402
from .progress_window import run_with_progress  # noqa: E402

logger = logging.getLogger(__name__)

DOWNLOAD_FAILED = "The download could not be completed."
UNEXPECTED = "Something TuxTalk didn't expect went wrong; the log has the details."


def check_for_updates(show_result, quitting=lambda: False):
    """Check for Updates in the tray's menu: "Checking for Updates..." while
    GitHub is asked on a thread, then *show_result(result)* on GTK's thread,
    or "Update Check Failed". Nothing once *quitting()*: the check can
    outlast a Quit."""
    progress_dialog = message_dialog(closable=False, message_type=Gtk.MessageType.INFO,
                                     buttons=Gtk.ButtonsType.NONE, text="Checking for Updates...")
    progress_dialog.format_secondary_text("Connecting to GitHub...")
    progress_dialog.set_position(Gtk.WindowPosition.CENTER)
    progress_dialog.set_keep_above(True)
    progress_dialog.show_all()

    def answered(result):
        progress_dialog.destroy()
        if quitting():
            return False
        if result and result.get("success"):
            show_result(result)
        else:
            _error(result.get("error", "Unknown error") if result else "Unknown error",
                   title="Update Check Failed")
        return False

    def check():
        try:
            result = update_checker.check_for_updates()
        except Exception as e:
            logger.error(f"Update check raised: {e}", exc_info=True)
            result = {"success": False, "error": str(e)}
        r = result or {}
        logger.debug(f"Update check done: success={r.get('success')}, "
                     f"update_available={r.get('update_available')}, error={r.get('error')}")
        GLib.idle_add(answered, result)

    threading.Thread(target=check, daemon=True).start()


def auto_check_for_updates(show_result, quitting=lambda: False):
    """The once-a-day check at start-up, when it's on, on a thread: an app
    update opens *show_result(result)*, the same window as Check for
    Updates, and an extension update only a notification. False, for its
    GLib timer."""
    from .config import load_config, record_update_check
    config = load_config()
    if not config.auto_check_updates:
        logger.debug("Auto-update check disabled in config")
        return False
    if not update_checker.should_check_today(config.last_update_check):
        logger.debug("Already checked for updates today")
        return False
    logger.info("Auto-checking for updates...")

    def show(result):
        if not quitting():
            show_result(result)
        return False

    def check():
        """On its own thread: ask GitHub, note when, and hand on what's new."""
        try:
            result = update_checker.check_for_updates()
            # Saved against a fresh read (`config` is from before the ~30 s
            # check) and on GTK's thread, where the tray saves its own
            # settings: neither undoes a change made meanwhile.
            GLib.idle_add(record_update_check, update_checker.get_current_timestamp())
            if not (result and result.get("success")):
                logger.debug(f"Update check failed: {result.get('error', 'unknown')}")
            elif result.get("update_available", False):
                # The window a manual check opens, not a surprise Preferences.
                GLib.idle_add(show, result)
            elif result.get("extension_update", False):
                GLib.idle_add(extension_update_notice)
            else:
                logger.info("No updates available")
        except Exception as e:
            logger.error(f"Error in auto-update check: {e}")

    threading.Thread(target=check, daemon=True).start()
    return False


def extension_update_notice():
    """Tell the user a GNOME extension update is out. Check for Updates
    covers only the app, so point to the tab that installs it."""
    from .notify import notify
    notify("GNOME extension update available",
           "Open Preferences → Updates & Backup to install it.")
    logger.info("Showed extension update notification")
    return False


def show_update_result(result, install_appimage, install_package):
    """What an update check found for the app itself: "You're Up to Date!",
    or Update Available with the release notes and how this install updates.
    Download & Install calls *install_appimage(release)* or
    *install_package(release, install_type)*. GNOME extension updates are
    installed from Preferences → Updates & Backup instead."""
    if not result.get("update_available", False):
        _up_to_date(result.get("current_version", "unknown"))
        return
    release = result.get("release", {})
    # How TuxTalk was installed decides HOW to update. Only a self-managed
    # AppImage can be swapped in place; .deb/.rpm/AUR/Flatpak users must use
    # their package manager, so we show instructions instead of downloading
    # and running an AppImage (which fails, e.g. missing libfuse.so.2).
    install_type = update_checker.get_install_type()
    guidance = update_checker.get_update_guidance(
        install_type, result.get("latest_version", "unknown"), release.get("html_url", ""))

    # NON-modal and NOT keep-above on purpose: the auto-check pops this on
    # launch, and a modal, always-on-top dialog grabs input and floats over
    # the About/Help/Voice windows so they open hidden behind it. The
    # Preferences window works precisely because it is neither.
    dialog = Gtk.Dialog(title="Update Available")
    dialog.set_default_size(450, 350)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    content = dialog.get_content_area()
    content.set_spacing(10)
    content.set_margin_start(15)
    content.set_margin_end(15)
    content.set_margin_top(15)
    content.set_margin_bottom(10)
    _update_heading(content, result.get("current_version", "unknown"),
                    result.get("latest_version", "unknown"))
    if not guidance["can_auto_update"]:
        _how_to_update(content, guidance)
    if release.get("body"):
        notes_label = Gtk.Label(label="Release Notes:")
        notes_label.set_halign(Gtk.Align.START)
        notes_label.set_margin_top(10)
        content.pack_start(notes_label, False, False, 0)
        content.pack_start(notes_text.scrolled_notes(release["body"])[0], True, True, 0)
    _update_buttons(dialog, guidance["update_method"], release)

    # Non-blocking rather than run(), as in About: a nested loop from a tray
    # menu callback can fail to map the window on Wayland.
    def on_response(dlg, response):
        dlg.destroy()
        if response == Gtk.ResponseType.ACCEPT:
            update_checker.open_release_page(release.get("html_url", ""))
        elif response == Gtk.ResponseType.YES:
            install_appimage(release)
        elif response == Gtk.ResponseType.APPLY:
            install_package(release, install_type)
    dialog.connect("response", on_response)
    dialog.show_all()
    dialog.present()


def _up_to_date(current):
    """"You're Up to Date!" for TuxTalk *current*."""
    dialog = message_dialog(message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK,
                            text="You're Up to Date!")
    dialog.format_secondary_markup(f"<b>TuxTalk {current}</b> is the latest version.")
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)
    dialog.connect("response", lambda d, _r: d.destroy())
    dialog.show_all()
    dialog.present()


def _update_heading(content, current, latest):
    """"Update Available!" and the versions, from *current* to *latest*."""
    header = Gtk.Label()
    header.set_markup("<big><b>Update Available!</b></big>")
    header.set_halign(Gtk.Align.START)
    content.pack_start(header, False, False, 0)
    version_label = Gtk.Label()
    version_label.set_markup(f"<b>TuxTalk:</b> {current} → <b>{latest}</b>")
    version_label.set_halign(Gtk.Align.START)
    content.pack_start(version_label, False, False, 5)


def _how_to_update(content, guidance):
    """For package, AUR and Flatpak installs, which update through their own
    tools: how, and the exact command, selectable to copy."""
    how_label = Gtk.Label()
    how_label.set_line_wrap(True)
    how_label.set_halign(Gtk.Align.START)
    how_label.set_markup(guidance["message"])
    content.pack_start(how_label, False, False, 5)
    if guidance.get("command"):
        cmd_entry = Gtk.Entry()
        cmd_entry.set_text(guidance["command"])
        cmd_entry.set_editable(False)
        cmd_entry.get_style_context().add_class("monospace")
        content.pack_start(cmd_entry, False, False, 0)


def _update_buttons(dialog, method, release):
    """Close, View on GitHub, and Download & Install where this install can
    take the update itself, which is then the highlighted button: YES swaps
    in the AppImage (appimage_swap), APPLY installs the .rpm/.deb as root
    (pkexec_package). Otherwise (manual) View on GitHub is highlighted."""
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    view_btn = (dialog.add_button("View on GitHub", Gtk.ResponseType.ACCEPT)
                if release.get("html_url") else None)
    if method == "appimage_swap" and release.get("appimage_url"):
        suggested = dialog.add_button("Download & Install", Gtk.ResponseType.YES)
    elif method == "pkexec_package":
        suggested = dialog.add_button("Download & Install", Gtk.ResponseType.APPLY)
    else:
        suggested = view_btn
    if suggested is not None:
        suggested.get_style_context().add_class("suggested-action")


def run_appimage_update(parent, release, restart, *, failed=None, quitting=lambda: False,
                        install=None):
    """Download *release*'s AppImage (checked against the release's
    SHA256SUMS.txt when it publishes one) and install it, behind a progress
    window over *parent* (None: from the tray) that can't be closed
    meanwhile; then *restart()* onto it, or say why it didn't go in, also to
    *failed(message)*. Nothing is installed once *quitting()*, and nothing
    said. *install(put_in)* runs the install: the tray's lets a Quit wait
    for an install under way."""
    url = release.get("appimage_url")
    if not url:
        logger.warning("This release has no AppImage to download")
        return

    def work(progress):
        """On its own thread: the download, then the install; (installed, why not)."""
        path = update_checker.download_update(
            url, release.get("appimage_name", "TuxTalk-update.AppImage"), progress,
            checksums_url=release.get("checksums_url"))
        if not path:
            return False, DOWNLOAD_FAILED
        if quitting():
            return False, None
        progress("Installing update...", 90)

        def put_in():
            return update_checker.install_appimage_update(path, progress)
        return install(put_in) if install else put_in()

    def finish(result):
        success, message = result or (False, UNEXPECTED)
        logger.info(f"AppImage update: installed={success}, {message}")
        if success:
            restart()
        elif message and not quitting():
            if failed:
                failed(message)
            show_message(parent, Gtk.MessageType.ERROR, "Update Failed",
                         f"{message}\n\nYou can try again or download the update "
                         "manually from GitHub.", keep_above=parent is None)

    run_with_progress(parent, "Updating TuxTalk", "Starting download...", work, finish)


def _set_margins(widget, value):
    widget.set_margin_start(value)
    widget.set_margin_end(value)
    widget.set_margin_top(value)
    widget.set_margin_bottom(value)


def run_package_update(release, install_type, restart_callback=None, quitting=lambda: False):
    """Download + pkexec-install the .rpm/.deb for `install_type`, then offer to
    restart. Shows its own progress and result dialogs. Call from the GTK thread.

    restart_callback: called when the user clicks "Restart Now". The tray passes
    its restart_app (clean detached relaunch + quit_app); Preferences passes
    restart_via_tray, which asks the tray to do the same. If None, the dialog
    tells the user to reopen TuxTalk instead. *quitting* says when TuxTalk
    began quitting meanwhile: then nothing is installed after the download,
    and no result is shown (an install already started finishes)."""
    url, filename = update_checker.get_package_asset(release, install_type)
    if not url:
        _error("This release has no package for your install type.\n"
               "Please update from the GitHub release page.")
        return

    progress = Gtk.Dialog(title="Updating TuxTalk", flags=Gtk.DialogFlags.MODAL)
    # The download and the password prompt carry on whatever happens to this
    # window, so it stays until they finish rather than vanishing and leaving
    # a password prompt to appear later with no context.
    progress.set_deletable(False)
    progress.connect("delete-event", lambda *_a: True)
    progress.set_default_size(440, 130)
    progress.set_position(Gtk.WindowPosition.CENTER)
    progress.set_keep_above(True)
    content = progress.get_content_area()
    content.set_spacing(10)
    _set_margins(content, 20)
    status = Gtk.Label(label="Starting download…")
    status.set_halign(Gtk.Align.START)
    content.pack_start(status, False, False, 0)
    bar = Gtk.ProgressBar()
    bar.set_show_text(True)
    content.pack_start(bar, False, False, 10)
    progress.show_all()

    # download_update reports why it failed (e.g. a checksum that doesn't
    # match) only through its progress messages.
    last_message = [""]

    def progress_cb(message, percent):
        last_message[0] = message
        GLib.idle_add(lambda: status.set_text(message))
        GLib.idle_add(lambda: bar.set_fraction(percent / 100.0))
        GLib.idle_add(lambda: bar.set_text(f"{percent}%"))

    def finish(success, message):
        progress.destroy()
        if quitting():
            return
        if success:
            _installed_restart_prompt(restart_callback)
        else:
            _error(message)

    def worker():
        """Download, then install with pkexec; always ends in finish()."""
        try:
            path = update_checker.download_update(
                url, filename, progress_cb,
                checksums_url=release.get("checksums_url"))
            if not path:
                reason = last_message[0].rstrip(".!") or "The download could not be completed"
                GLib.idle_add(lambda: finish(
                    False, f"{reason}.\n\nPlease try again or update from GitHub."))
                return
            if quitting():
                ok, msg = False, "TuxTalk quit before the update was installed."
            else:
                GLib.idle_add(lambda: status.set_text(
                    "Waiting for your password to install…"))
                ok, msg = update_checker.install_package_update(path, install_type)
        except Exception as e:
            # The window refuses to close while this runs: without an
            # answer it would stay on screen until TuxTalk quit.
            logger.exception("Package update failed")
            ok, msg = False, f"The update failed: {e}\n\nPlease try again or update from GitHub."
        GLib.idle_add(lambda: finish(ok, msg))

    threading.Thread(target=worker, daemon=True).start()


def _installed_restart_prompt(restart_callback=None):
    """Say the update is installed and offer Restart Now or Later."""
    done = Gtk.Dialog(title="Update Installed")
    done.set_position(Gtk.WindowPosition.CENTER)
    dc = done.get_content_area()
    _set_margins(dc, 20)
    lbl = Gtk.Label()
    lbl.set_markup("<b>Update installed!</b>\n"
                   "Restart TuxTalk to use the new version.")
    lbl.set_halign(Gtk.Align.START)
    lbl.set_line_wrap(True)
    dc.pack_start(lbl, False, False, 0)
    done.add_button("Later", Gtk.ResponseType.CLOSE)
    rb = done.add_button("Restart Now", Gtk.ResponseType.OK)
    rb.get_style_context().add_class("suggested-action")

    def _resp(d, r):
        d.destroy()
        if r == Gtk.ResponseType.OK:
            if restart_callback is not None:
                restart_callback()
            else:
                _reopen_to_finish()
    done.connect("response", _resp)
    done.show_all()
    done.present()


def tray_interface():
    """The running tray's D-Bus interface. Raises when it can't be reached."""
    import dbus
    from .dbus_service import DBUS_NAME, DBUS_PATH, DBUS_INTERFACE
    bus = dbus.SessionBus()
    return dbus.Interface(bus.get_object(DBUS_NAME, DBUS_PATH), DBUS_INTERFACE)


def restart_via_tray(get_interface=None) -> bool:
    """Ask the running tray to restart TuxTalk into a just-installed update.

    Preferences is a separate process started by the tray, so it can't
    restart the app itself: a new tray it started would find the old one
    still holding the single-instance lock and exit, and the old dictation
    service would keep running. The tray's restart_app stops the service,
    quits (closing Preferences too) and starts the new copy.
    get_interface is injectable for testing. Returns True when the tray took
    the request; otherwise the user is told to reopen TuxTalk."""
    try:
        (get_interface or tray_interface)().RestartApp()
        return True
    except Exception as e:
        logger.warning(f"Could not ask the tray to restart: {e}")
        _reopen_to_finish()
        return False


def _reopen_to_finish():
    """Tell the user to quit and reopen TuxTalk, when it can't restart itself."""
    _info("Update installed",
          "Please quit TuxTalk from the tray icon and open it "
          "again to finish updating.")


def _info(title, message):
    dlg = message_dialog(message_type=Gtk.MessageType.INFO,
                            buttons=Gtk.ButtonsType.OK, text=title)
    dlg.format_secondary_text(message)
    dlg.set_position(Gtk.WindowPosition.CENTER)
    dlg.connect("response", lambda d, _r: d.destroy())
    dlg.show_all()
    dlg.present()


def _error(message, title="Update Failed"):
    """*title* and *message* in an error box over the other windows."""
    err = message_dialog(message_type=Gtk.MessageType.ERROR,
                         buttons=Gtk.ButtonsType.OK, text=title)
    err.format_secondary_text(message)
    err.set_position(Gtk.WindowPosition.CENTER)
    err.set_keep_above(True)
    err.connect("response", lambda d, _r: d.destroy())
    err.show_all()
    err.present()
