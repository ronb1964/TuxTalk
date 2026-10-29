"""What the tray says once at start-up, about what happened before it: an
old TalkType Flatpak it stopped, the update that just went in, and the
rename from TalkType. Never during dictation: only from the tray's start."""
import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .logger import setup_logger  # noqa: E402
from .ui_style import message_dialog  # noqa: E402

logger = setup_logger(__name__)

OLD_FLATPAK_NOTICE_TITLE = "Stopped the old TalkType Flatpak"
OLD_FLATPAK_NOTICE_BODY = (
    "It was running next to TuxTalk, its new name, so everything you said "
    "would be typed twice. You can uninstall it from your software center, or "
    "with: flatpak uninstall io.github.ronb1964.TalkType")


def handle_old_talktype_flatpak(stop=None, notify=None):
    """Stop an old TalkType Flatpak and say so; runs off the main thread, so
    the notice is handed to the main loop."""
    if stop is None:
        from .service_launcher import stop_old_talktype_flatpak as stop
    if notify is None:
        from .notify import notify as _notify

        def notify(title, body):
            def show():
                _notify(title, body)
                return False    # once
            GLib.idle_add(show)
    try:
        if stop():
            notify(OLD_FLATPAK_NOTICE_TITLE, OLD_FLATPAK_NOTICE_BODY)
    except Exception as e:      # never stop the tray from starting
        logger.warning(f"Could not check for the pre-rename Flatpak: {e}")


def show_after_start():
    """"Update Complete!" if this start follows an update, then the rename
    notice, never on top of it. False, for its GLib timer."""
    try:
        _show_update_complete_once()
    except Exception as e:
        logger.warning(f"Could not show the update notice: {e}")
    try:
        _show_rename_notice_once()
    except Exception as e:
        logger.warning(f"Could not show the rename notice: {e}")
    return False


def _show_update_complete_once():
    """"Update Complete!", once, when the updater's marker says this start
    is the first of a new version (update_checker.check_just_updated)."""
    from . import __version__, update_checker
    if not update_checker.check_just_updated():
        return
    dialog = message_dialog(
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.OK,
        text="Update Complete!"
    )
    dialog.format_secondary_markup(
        f"TuxTalk has been updated to <b>v{__version__}</b>\n\n"
        f"The update was installed automatically and "
        f"TuxTalk has restarted with the new version."
    )
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)
    dialog.run()
    dialog.destroy()


def _show_rename_notice_once():
    """'TalkType is now TuxTalk', once, after this start converted an old
    TalkType install (rename_migration leaves a marker for it). It is cleared
    only once the message has been seen, so quitting first just shows it on
    the next start."""
    from . import config, rename_migration
    folder = os.path.dirname(config.CONFIG_PATH)
    if not rename_migration.notice_pending(folder):
        return
    dialog = message_dialog(
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.NONE,
        text="TalkType is now TuxTalk",
    )
    dialog.format_secondary_text("\n\n".join((
        "Same app, new name. Your settings, models, snippets and stats all came "
        "across. Look for TuxTalk in your app menu from now on.",
        "Why the change: another company owns the name TalkType for dictation "
        "software.")))
    dialog.add_button("Got it", Gtk.ResponseType.OK)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)
    dialog.run()
    dialog.destroy()
    rename_migration.notice_shown(folder)
    logger.info("Rename to TuxTalk: showed the one-time notice")
