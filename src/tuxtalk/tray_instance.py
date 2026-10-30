"""One tray at a time: the tray's single-instance lock, what a second tray
does (give way to a TalkType tray from before the rename, show the running
one's menu, wait for one that's quitting, or say it runs), and the command
line's questions to the running tray (tuxtalk --toggle, --open, --quit)."""
import os
import signal
import sys

from . import single_instance
from .logger import setup_logger

logger = setup_logger(__name__)

# The single-instance lock's file, open while the tray runs.
_lockfile_handle = None
TRAY_LOCK = "talktype-tray.lock"
# How long an older TalkType tray gets to exit after SIGTERM. It has no handler
# for it, so it is normally gone at once.
_OLD_TRAY_EXIT_WAIT_S = 3.0
# A quitting tray outlives its service by up to SIGKILL's wait, unloading
# its KWin focus script and its own exit.
_TRAY_QUIT_EXTRA_S = 5.0


def acquire():
    """Take the tray's single-instance lock (single_instance.py), or give way
    to the tray that holds it (_give_way_or_exit)."""
    global _lockfile_handle
    try:
        lockfile = single_instance.open_lock(TRAY_LOCK)
        if not single_instance.claim(lockfile):
            _give_way_or_exit(lockfile)
    except OSError as e:        # no runtime folder, or it's full: not "already running"
        logger.warning(f"Could not take the tray's single-instance lock: {e}")
        return
    _lockfile_handle = lockfile    # closing it, even by garbage collection, lets go


def _give_way_or_exit(lockfile):
    """Another tray holds *lockfile*. A TalkType tray from before the rename
    is stopped, and this returns once its lock is taken. Another TuxTalk
    shows its menu and this one exits; one that won't may be quitting (it
    refuses to while it waits for its dictation service to finish typing),
    so its lock is waited for before saying it runs."""
    from .service_launcher import QUIT_WAIT_S, pre_rename_tray_pids
    old_trays = pre_rename_tray_pids()
    if old_trays:
        logger.info(f"Stopping the pre-0.15.0 tray (PID {old_trays}) so TuxTalk can start")
        for pid in old_trays:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass  # exited between the scan and the kill
        if single_instance.claim(lockfile, _OLD_TRAY_EXIT_WAIT_S):
            return
    else:
        # Opened again from the app list: the running TuxTalk's menu as a window.
        # Plain GNOME has no tray, so "look for the tray icon" was a dead end.
        from . import control
        try:
            if control.open_in_running("menu", tray_starting=lambda: True):
                sys.exit(0)
        except Exception as e:
            logger.info(f"Could not ask the running TuxTalk for its menu: {e}")
        logger.info("The running TuxTalk didn't answer; waiting in case it's quitting")
        if single_instance.claim(lockfile, QUIT_WAIT_S + _TRAY_QUIT_EXTRA_S):
            return
    logger.info("Another tray instance is already running. Exiting.")
    print("Another tray instance is already running. Exiting.", file=sys.stderr)
    _show_already_running(older_talktype=bool(pre_rename_tray_pids()))
    sys.exit(0)


def tray_starting():
    """Whether another tray holds the lock: one still starting up that
    hasn't taken its D-Bus name yet, for control's wait."""
    return single_instance.held_elsewhere(TRAY_LOCK)


def run_command_line(action):
    """tuxtalk --toggle/--start/--stop with TuxTalk running: exits. Returns
    only when nothing runs and this process should start TuxTalk itself."""
    from . import control
    try:
        status = control.ask_running(action, tray_starting=tray_starting)
    except Exception as e:
        logger.warning(f"Couldn't reach the running TuxTalk: {e}")
        print(f"Couldn't reach TuxTalk: {e}", file=sys.stderr)
        sys.exit(1)
    if status is not None:
        sys.exit(control.report(status))
    if action == "stop":
        sys.exit(control.report("idle"))
    print("Starting TuxTalk.")


def open_from_command_line(name):
    """tuxtalk --open NAME / --quit with TuxTalk running: asks it and exits.
    Returns only when nothing runs and this process should start TuxTalk
    (main() opens the window once it's up)."""
    from . import control
    try:
        status = control.open_in_running(name, tray_starting=tray_starting)
    except Exception as e:
        logger.warning(f"Couldn't reach the running TuxTalk: {e}")
        print(f"Couldn't reach TuxTalk: {e}", file=sys.stderr)
        sys.exit(1)
    if status is True:
        sys.exit(0)
    if status is False and name != "uninstall":     # that one this process can do
        print("The running TuxTalk can't do that. It may be an older version "
              "or still starting; try again in a moment.", file=sys.stderr)
        sys.exit(1)
    if name == "quit":
        print("TuxTalk isn't running.")
        sys.exit(0)
    if name == "uninstall":
        # Nothing to start first: this process does it on its own.
        from . import uninstall
        sys.exit(0 if uninstall.run() else 1)
    print("Starting TuxTalk.")


def already_running_message(older_talktype):
    """The (heading, explanation) shown when another tray holds the lock."""
    if older_talktype:
        return ("An older TalkType is still running",
                "TuxTalk is the new name for TalkType, and only one of them can "
                "run at a time. Click the TalkType microphone icon in the system "
                "tray, choose Quit TalkType, then start TuxTalk again.")
    return ("TuxTalk is already running",
            "Look for its microphone icon in the system tray. Its menu has "
            "Preferences, Help and Quit TuxTalk.")


def _show_already_running(older_talktype):
    """Tell the user why no second tray appears; started from a menu, nothing
    else would show."""
    from gi.repository import Gtk
    from .ui_style import message_dialog
    heading, body = already_running_message(older_talktype)
    logger.info(f"Not starting: {heading}")
    try:
        dialog = message_dialog(
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=heading,
        )
        dialog.format_secondary_text(body)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.set_keep_above(True)
        dialog.run()
        dialog.destroy()
    except Exception as e:      # no display: the stderr line is all there is
        logger.info(f"Could not show the already-running notice: {e}")
