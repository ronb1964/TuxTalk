"""First run's copy of the AppImage into ~/AppImages, in the background (it's
300 MB and more, and the Ready screen shows meanwhile), and Setup Complete's
"Restart computer now", which waits for it: a reboot mid-copy leaves the menu
launcher and autostart pointing at a copy that never arrived."""
import threading
import time

from gi.repository import GLib, Gtk

from .logger import setup_logger

logger = setup_logger(__name__)

# How long Restart computer now waits for the copy; a disk slower than that
# gets a message instead of a reboot.
WAIT_S = 120.0

_thread = None


def start(current_path) -> None:
    """Copy the running AppImage, at *current_path*, into ~/AppImages. Not a
    daemon thread: quitting TuxTalk waits for the copy instead of cutting it
    off. A failed copy points the menu launcher back at *current_path*."""
    global _thread
    from .update_checker import (APPIMAGE_PATH, copy_appimage_to_standard_location,
                                 point_at_running_appimage)

    def copy():
        try:
            success, msg = copy_appimage_to_standard_location()
        except Exception as e:
            logger.error(f"Copying the AppImage failed: {e}", exc_info=True)
            GLib.idle_add(point_at_running_appimage, current_path)
            return
        if success:
            logger.info(f"AppImage installed to standard location: {APPIMAGE_PATH}")
        else:
            logger.warning(f"AppImage install failed: {msg}")
            GLib.idle_add(point_at_running_appimage, current_path)
    _thread = threading.Thread(target=copy, name="appimage-copy", daemon=False)
    _thread.start()


def copying() -> bool:
    """Whether the copy is still going."""
    return _thread is not None and _thread.is_alive()


def after(callback, still_copying, timeout=WAIT_S) -> None:
    """Call *callback* once the copy has finished (or none is running),
    without blocking the GTK thread; still copying after *timeout* seconds
    (a very slow disk), *still_copying* instead."""
    if not copying():
        callback()
        return
    logger.info("Waiting for the AppImage copy to finish before restarting")
    deadline = time.monotonic() + timeout

    def poll():
        if not copying():
            callback()
        elif time.monotonic() < deadline:
            return True
        else:
            still_copying()
        return False
    GLib.timeout_add(200, poll)


def reboot_when_copied(parent, button) -> None:
    """Restart computer now, once the copy is done: *button* (the one
    clicked) says so meanwhile; *parent* is the window messages go over."""
    from .ui_style import show_message
    from .uinput_helper import reboot_system
    if copying():
        button.set_label("Finishing install…")
        button.set_sensitive(False)

    def reboot():
        if not reboot_system():
            show_message(parent, Gtk.MessageType.WARNING, "Couldn't restart automatically",
                         "Please restart your computer yourself to finish setup.")
        # On success the machine is going down; nothing more to do here.

    def too_slow():
        logger.warning("The AppImage copy is still going after two minutes; not restarting")
        button.set_sensitive(True)
        show_message(parent, Gtk.MessageType.WARNING, "TuxTalk is still being copied",
                     "Your disk is taking a while. Wait a minute, then restart your "
                     "computer yourself to finish setup.")
    after(reboot, too_slow)
