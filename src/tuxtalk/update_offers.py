"""The offers TuxTalk makes by itself on starting, when it brings something
the computer doesn't have yet: a newer hotkey helper (asked at every start
until it's updated: without it the dictation keys can stop working) and
Parakeet Ultra (asked once). At most one a start, so two never stack.

They come up while people may be typing, so Not Now is the default: an
Enter meant for their own window must not start a download or a password
prompt.
"""
import os
import threading

from gi.repository import GLib, Gtk

from .logger import setup_logger

logger = setup_logger(__name__)

# Seconds after the tray starts: the dictation service is up by then, and
# the desktop has finished opening what it opens at login.
OFFER_AFTER_S = 20


def start(restart) -> None:
    """Make this start's offer once dictation is running; *restart*(None)
    restarts dictation onto what was updated."""
    GLib.timeout_add_seconds(OFFER_AFTER_S, _offer_in_turn, restart)


def _offer_in_turn(restart) -> bool:
    """A GLib timeout: True (ask again next tick) until the dictation service
    is up, then the hotkey helper's offer or, without one, Parakeet Ultra's."""
    from .cuda_helper import is_first_run
    from .service_launcher import find_service_pids
    if is_first_run():
        return False
    if not find_service_pids():
        return True
    if not offer_helper_update():
        maybe_offer_parakeet_update(restart)
    return False


# --- The hotkey helper ----------------------------------------------------------

def offer_helper_update() -> bool:
    """While the installed hotkey helper is older than the one this TuxTalk
    brings (secure_input.is_older), ask to update it, at every start until
    it is. True when it asked. The Flatpak has no helper to update."""
    try:
        from . import secure_input
        if os.environ.get("FLATPAK_ID") or not secure_input.is_installed():
            return False
        running = secure_input.running_version()
        if not secure_input.is_older(running):
            return False
        if _ask_helper_update(running):
            update_helper(say_helper_updated)
        else:
            logger.info("Hotkey helper update offer: not now")
        return True
    except Exception as e:
        logger.warning(f"Hotkey helper update offer skipped: {e}")
        return False


def helper_update_text(running) -> str:
    """What the offer says to someone running helper version *running*."""
    from . import secure_input
    from .hotkey_helper import VERSION
    keys = [key for key in secure_input.KEYS_SINCE if not secure_input.hears_key(running, key)]
    why = (f": until it's updated, {' and '.join(keys)} can't be dictation keys, and it "
           "misses this version's fixes." if keys else
           ", with fixes the one you have doesn't get.")
    return ("The hotkey helper is the small background service that reads your dictation "
            f"keys. This computer runs version {running}, and this TuxTalk brings {VERSION}"
            f"{why}\n\nUpdating asks for your password once. No restart is needed. Until "
            "it's updated, TuxTalk asks each time it starts.")


def _ask_helper_update(running) -> bool:
    """The question; True for Update Helper."""
    from .ui_style import message_dialog
    dialog = message_dialog(message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE,
                            text="TuxTalk's hotkey helper needs an update")
    dialog.format_secondary_text(helper_update_text(running))
    dialog.add_button("Not Now", Gtk.ResponseType.CANCEL)
    dialog.add_button("Update Helper", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    dialog.set_keep_above(True)
    answer = dialog.run()
    dialog.destroy()
    return answer == Gtk.ResponseType.OK


def update_helper(done) -> None:
    """Update the hotkey helper as System check's button does, off the GTK
    thread (pkexec waits for the password), then *done*(ok, message) on
    GTK's thread. Dictation needn't restart: the service's helper connection
    comes back within a second (hotkey_helper_client) and asks for its keys
    again, and a restart would throw away a dictation under way."""
    from . import secure_input

    def work():
        try:
            ok, message = secure_input.install_with_pkexec()
        except Exception as e:
            logger.warning(f"Updating the hotkey helper failed: {e}")
            ok, message = False, str(e)

        def finish():
            done(ok, message)
            return False        # once
        GLib.idle_add(finish)
    threading.Thread(target=work, daemon=True, name="helper-update").start()


def say_helper_updated(ok, message) -> None:
    """An update nobody is waiting on is over (the offer's, or one that
    outlasted Preferences' wait): say so, or that it wasn't updated (a
    cancelled password prompt says nothing more: the offer comes back next
    start)."""
    from .notify import notify
    if ok:
        logger.info("Updated the hotkey helper from the offer at start")
        notify("Hotkey helper updated", "Your dictation keys use it straight away.")
    elif "cancelled" in message.lower():
        logger.info("Hotkey helper update: the password prompt was cancelled")
    else:
        logger.warning(f"Hotkey helper update failed: {message}")
        notify("The hotkey helper wasn't updated",
               f"{message} TuxTalk offers it again when it next starts.")


# --- Parakeet Ultra -------------------------------------------------------------

def _offer_parakeet_update(device):
    """Ask, then download. True when Parakeet Ultra arrived, False when the
    user said no or cancelled, None when the download failed (ask again on
    the next start)."""
    from .model_helper import download_parakeet_update, parakeet_update_size
    from .ui_style import message_dialog
    size_text = parakeet_update_size(device)
    dialog = message_dialog(message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE,
                            text="Parakeet got more accurate")
    dialog.format_secondary_text(
        "TuxTalk now uses Parakeet Ultra, a retrained Parakeet that makes fewer mistakes "
        f"at the same speed. It's a one-time {size_text} download, and your Parakeet "
        "keeps working while it downloads.")
    dialog.add_button("Not Now", Gtk.ResponseType.CANCEL)
    dialog.add_button(f"Download ({size_text})", Gtk.ResponseType.OK)
    # It comes up by itself while people may be typing: an Enter meant for
    # their own window must not start a 670 MB download.
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    dialog.set_keep_above(True)
    answer = dialog.run()
    dialog.destroy()
    if answer != Gtk.ResponseType.OK:
        logger.info("Parakeet Ultra offer: not now")
        return False
    updated = download_parakeet_update(device)
    if updated is None:
        from .notify import notify
        notify("The Parakeet update didn't finish",
               "TuxTalk will offer it again the next time it starts.")
    return updated


def maybe_offer_parakeet_update(restart) -> None:
    """Once, on the first start after updating: offer someone still on the
    original Parakeet v3 the download of Parakeet Ultra (parakeet_ultra.py),
    then *restart* dictation onto it. Their Parakeet works either way."""
    try:
        from .config import load_config, save_config
        cfg = load_config()
        if cfg.parakeet_update_offered:
            return
        from .model_helper import parakeet_update_needed
        device = parakeet_update_needed(cfg)
        if device is None:
            return
        updated = _offer_parakeet_update(device)
        if updated is None:
            return
        cfg = load_config()     # the download took minutes; settings may have changed
        cfg.parakeet_update_offered = True
        save_config(cfg)
        if updated:
            logger.info(f"Parakeet Ultra downloaded ({device}); restarting dictation onto it")
            restart(None)
    except Exception as e:
        logger.warning(f"Parakeet update offer skipped: {e}")
