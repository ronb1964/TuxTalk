"""What the tray says once at start-up, about what happened before it: an
old TalkType Flatpak it stopped, the update that just went in, and the
rename from TalkType; and the one question before TuxTalk turns on the
desktop's accessibility switch (a11y_switch), asked here or at the end of
first-run setup. Only from the tray's start, though a dictation may be
going on then (offer_a11y_switch says how that's handled)."""
import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .logger import setup_logger  # noqa: E402
from .ui_style import show_message  # noqa: E402

logger = setup_logger(__name__)

OLD_FLATPAK_NOTICE_TITLE = "Stopped the old TalkType Flatpak"
OLD_FLATPAK_NOTICE_BODY = (
    "It was running next to TuxTalk, its new name, so everything you said "
    "would be typed twice. You can uninstall it from your software center, or "
    "with: flatpak uninstall io.github.ronb1964.TalkType")


A11Y_QUESTION_TITLE = "Let dictation fit into your sentences?"
A11Y_QUESTION_BODY = (
    "TuxTalk can match capitals and spaces to the text around your cursor, so dictating "
    "into the middle of a sentence doesn't start with a capital. To see that text in most "
    "apps, it needs your desktop's accessibility switch on, the one screen readers use. "
    "It stays on until you turn it off in Preferences. Apps use a little more memory with "
    "it on.")
A11Y_ON_TITLE = "Turned on"
A11Y_ON_BODY = (
    "Most apps, like KDE and GNOME apps and Firefox, work with it right away. Chrome, "
    "Brave and apps like Claude Desktop, Discord and VS Code need to be closed and opened "
    "again first.")


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
    notice, never on top of it, then the accessibility switch kept on or
    asked about (offer_a11y_switch_at_start). False, for its GLib timer."""
    try:
        _show_update_complete_once()
    except Exception as e:
        logger.warning(f"Could not show the update notice: {e}")
    try:
        _show_rename_notice_once()
    except Exception as e:
        logger.warning(f"Could not show the rename notice: {e}")
    try:
        offer_a11y_switch_at_start()
    except Exception as e:
        logger.warning(f"Could not offer the accessibility switch: {e}")
    return False


def offer_a11y_switch_at_start():
    """At the tray's start: the switch on again after a yes (a11y_switch.keep_on),
    or the question, unless first-run setup is still to come, which asks at
    its end."""
    from . import a11y_switch, first_run_marker
    a11y_switch.keep_on()
    if not first_run_marker.is_first_run():
        offer_a11y_switch()


def offer_a11y_switch(parent=None):
    """Ask once whether TuxTalk may turn on the desktop's accessibility
    switch, unless it's on already (a screen reader, the user's own) or
    there's none to turn on. After a yes, say which apps need a restart.
    Enter alone says Not Now: TuxTalk's own auto-send presses Enter, and the
    question can come up while the user dictates."""
    from . import a11y_switch
    if a11y_switch.answer() or a11y_switch.is_on() is not False:
        return
    answer = show_message(parent, Gtk.MessageType.QUESTION, A11Y_QUESTION_TITLE,
                          A11Y_QUESTION_BODY, [("Not Now", Gtk.ResponseType.CANCEL),
                                               ("Turn On", Gtk.ResponseType.OK)],
                          keep_above=True, default=Gtk.ResponseType.CANCEL)
    if answer != Gtk.ResponseType.OK:
        a11y_switch.decline()
    elif a11y_switch.allow():
        show_message(parent, Gtk.MessageType.INFO, A11Y_ON_TITLE, A11Y_ON_BODY, keep_above=True)


def _show_update_complete_once():
    """"Update Complete!", once, when the updater's marker says this start
    is the first of a new version (update_install.check_just_updated)."""
    from . import __version__, update_install
    if not update_install.check_just_updated():
        return
    show_message(None, Gtk.MessageType.INFO, "Update Complete!",
                 f"TuxTalk has been updated to <b>v{__version__}</b>\n\n"
                 "The update was installed automatically and "
                 "TuxTalk has restarted with the new version.",
                 keep_above=True, markup=True)


def _show_rename_notice_once():
    """'TalkType is now TuxTalk', once, after this start converted an old
    TalkType install (rename_migration leaves a marker for it). It is cleared
    only once the message has been seen, so quitting first just shows it on
    the next start."""
    from . import config, rename_migration
    folder = os.path.dirname(config.CONFIG_PATH)
    if not rename_migration.notice_pending(folder):
        return
    show_message(None, Gtk.MessageType.INFO, "TalkType is now TuxTalk", "\n\n".join((
        "Same app, new name. Your settings, models, snippets and stats all came "
        "across. Look for TuxTalk in your app menu from now on.",
        "Why the change: another company owns the name TalkType for dictation "
        "software.")), [("Got it", Gtk.ResponseType.OK)], keep_above=True)
    rename_migration.notice_shown(folder)
    logger.info("Rename to TuxTalk: showed the one-time notice")
