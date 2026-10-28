"""Shared GTK flow for updating a package (.rpm/.deb) install.

Downloads the correct package for the current install type, installs it as root
via pkexec (the desktop shows a PolicyKit password prompt so it goes through the
system package manager — dependencies resolved, package DB kept correct), then
offers to restart. Used by both the tray "Check for Updates" dialog and the
Preferences → Updates & Backup tab so the behaviour stays identical.
"""
import logging
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402
from .ui_style import message_dialog  # noqa: E402

from . import update_checker  # noqa: E402

logger = logging.getLogger(__name__)


def _set_margins(widget, value):
    widget.set_margin_start(value)
    widget.set_margin_end(value)
    widget.set_margin_top(value)
    widget.set_margin_bottom(value)


def run_package_update(release, install_type, restart_callback=None):
    """Download + pkexec-install the .rpm/.deb for `install_type`, then offer to
    restart. Shows its own progress and result dialogs. Call from the GTK thread.

    restart_callback: called when the user clicks "Restart Now". The tray passes
    its restart_app (clean detached relaunch + quit_app); Preferences passes
    restart_via_tray, which asks the tray to do the same. If None, the dialog
    tells the user to reopen TuxTalk instead."""
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


def _error(message):
    err = message_dialog(message_type=Gtk.MessageType.ERROR,
                            buttons=Gtk.ButtonsType.OK, text="Update Failed")
    err.format_secondary_text(message)
    err.set_position(Gtk.WindowPosition.CENTER)
    err.set_keep_above(True)
    err.connect("response", lambda d, _r: d.destroy())
    err.show_all()
    err.present()
