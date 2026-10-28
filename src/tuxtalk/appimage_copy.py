"""First run putting TuxTalk in place: the AppImage copied into ~/AppImages
in the background (it's 300 MB and more, and the Ready screen shows
meanwhile), the menu launcher and start at login; and Setup Complete's
"Restart computer now", which waits for the copy: a reboot mid-copy leaves
the menu launcher and autostart pointing at a copy that never arrived."""
import threading
import time

from gi.repository import GLib, Gtk

from .logger import setup_logger

logger = setup_logger(__name__)

# How long Restart computer now waits for the copy; a disk slower than that
# gets a message instead of a reboot.
WAIT_S = 120.0

_thread = None


def put_in_place():
    """First run, from an AppImage: the menu launcher, start at login (on
    for new users: a dictation tool is only useful when it's already
    running), and the copy into ~/AppImages, which both point at. Returns
    whether TuxTalk is (or will be) in ~/AppImages and whether a launcher
    was made, for the Ready screen."""
    from .update_checker import (create_desktop_launcher, desktop_launcher_exists,
                                 ensure_appimage_in_standard_location, is_running_from_appimage)
    installed = launcher_created = False
    copy_from = None
    try:
        if is_running_from_appimage():
            installed, current_path = ensure_appimage_in_standard_location()
            if not installed and current_path:
                copy_from, installed = current_path, True       # it will be, once copied
            if not desktop_launcher_exists():
                launcher_created, msg = create_desktop_launcher()
                logger.info("Desktop launcher created" if launcher_created
                            else f"Could not create the desktop launcher: {msg}")
    except Exception as e:
        logger.warning(f"Could not set up AppImage/launcher: {e}")
    _start_at_login(installed)
    if copy_from:
        # Not before: a copy that fails at once (no room) points autostart
        # back at the running AppImage, and written after that, autostart
        # pointed at a copy that never arrived.
        start(copy_from)
    return installed, launcher_created


def _start_at_login(installed) -> None:
    """Turn on launch at login, pointing at the copy in ~/AppImages when
    *installed*, not at the file running (usually in Downloads). Recorded
    as on only if its file was written, or the Ready screen would promise
    a start that never happens."""
    from . import autostart
    from .config import load_config, save_config
    try:
        if not autostart.set_autostart(True, prefer_installed_appimage=installed):
            logger.warning("Autostart file could not be written; leaving launch-at-login off")
            return
        cfg = load_config()
        if not getattr(cfg, "launch_at_login", False):
            cfg.launch_at_login = True
            save_config(cfg)
    except Exception as e:
        logger.warning(f"Could not enable launch-at-login during onboarding: {e}")


def start(current_path) -> None:
    """Copy the running AppImage, at *current_path*, into ~/AppImages. Not a
    daemon thread: quitting TuxTalk waits for the copy instead of cutting it
    off. A failed copy points the menu launcher and autostart back at
    *current_path*, on the same thread, so a restart that waits for the copy
    (after) waits for that too."""
    global _thread
    from .update_checker import (APPIMAGE_PATH, copy_appimage_to_standard_location,
                                 point_at_running_appimage)

    def copy():
        try:
            success, msg = copy_appimage_to_standard_location()
        except Exception as e:
            logger.error(f"Copying the AppImage failed: {e}", exc_info=True)
            success = False
        else:
            if not success:
                logger.warning(f"AppImage install failed: {msg}")
        if success:
            logger.info(f"AppImage installed to standard location: {APPIMAGE_PATH}")
        else:
            point_at_running_appimage(current_path)
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
