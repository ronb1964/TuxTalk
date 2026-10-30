"""`tuxtalk --toggle`, `--start` and `--stop`: dictation from a desktop
shortcut or a script.

Every install runs `python -m tuxtalk.tray "$@"` (the AppImage's AppRun, the
.deb/.rpm and AUR launchers, run-dev.sh), so the tray's main() reads these.

With TuxTalk running, the command asks it over D-Bus (ControlDictation) and
exits. With nothing running, the command itself becomes TuxTalk: the AppImage
unmounts when its first process exits, so it can't start a tray in the
background and quit.

The dictation service throws away a toggle sent while its model loads (see
app.main), so a stray key press at login can't start a recording out of the
blue. A command is different: someone asked for it. So the tray keeps it here
and starts the recording once the service says it's ready (NotifyServiceReady).
"""

import argparse
import time

from .dbus_service import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH

ACTIONS = ("toggle", "start", "stop")

# How long a recording asked for while the model loads may still start. Parakeet
# loads in seconds; anything far later (a model download, a failed start) would
# be a recording out of the blue.
WAIT_LIMIT_S = 120

# How long the command waits for a tray that is starting up (it holds the
# single-instance lock but hasn't taken the D-Bus name yet), as at login.
TRAY_START_WAIT_S = 20

MESSAGES = {
    "started": "TuxTalk is recording.",
    "stopped": "TuxTalk stopped recording.",
    "already": "TuxTalk is already recording.",
    "idle": "TuxTalk isn't recording, so there's nothing to stop.",
    "waiting": "TuxTalk is loading its speech model. Recording starts as soon as it's ready.",
    "cancelled": "Cancelled the recording that was waiting for the speech model.",
    "setup": "TuxTalk's first-run setup is open. Finish it, then try again.",
    "starting": "TuxTalk is still starting. Try again in a moment.",
    "old": "Sent the toggle. This TuxTalk is an older version, so a toggle sent "
           "while it loads its speech model may be lost; restart it to update.",
    "unknown": "TuxTalk didn't understand that command.",
    "quitting": "TuxTalk is quitting. Open it again, then try again.",
}

# Statuses where nothing happened that the caller asked for.
_FAILED = {"setup", "starting", "unknown", "quitting"}


# tuxtalk --open NAME (and --quit): the running TuxTalk's D-Bus method for
# each. The launcher's right-click shortcuts use these.
OPEN_METHODS = {
    "menu": "ShowMenu",
    "preferences": "OpenPreferences",
    "transcribe": "TranscribeFile",
    "fix-word": "FixWordInDictation",
    "help": "ShowHelp",
    "quit": "Quit",
    "uninstall": "Uninstall",
}

# The OPEN_METHODS entries with their own option instead of --open NAME.
_OWN_OPTION = ("quit", "uninstall")


def _parse(argv):
    """The command line's options (--toggle, --start, --stop, --open, --quit,
    --uninstall); options it doesn't know are left for GTK."""
    ap = argparse.ArgumentParser(
        prog="tuxtalk",
        description="Voice dictation for Linux. With no options, starts TuxTalk.",
        epilog="If TuxTalk isn't running, --toggle and --start start it, and "
               "recording begins once the speech model has loaded. --open "
               "starts it too, then opens the window.")
    group = ap.add_mutually_exclusive_group()
    for action, text in (("toggle", "start recording, or stop it if it's recording"),
                         ("start", "start recording (nothing changes if it already is)"),
                         ("stop", "stop recording and type what was said")):
        group.add_argument(f"--{action}", dest="action", action="store_const",
                           const=action, help=text)
    group.add_argument("--open", dest="open", metavar="WINDOW",
                       choices=[name for name in OPEN_METHODS if name not in _OWN_OPTION],
                       help="open a window: menu, preferences, transcribe, fix-word or help")
    group.add_argument("--quit", dest="open", action="store_const", const="quit",
                       help="quit TuxTalk")
    group.add_argument("--uninstall", dest="open", action="store_const", const="uninstall",
                       help="remove TuxTalk and everything it set up (asks first)")
    args, _others = ap.parse_known_args(argv)
    return args


def parse_action(argv):
    """"toggle", "start", "stop" or None from the command line.

    Anything else is left alone: launchers pass their own arguments (the
    AppImage's desktop entry runs "AppRun tray").
    """
    return _parse(argv).action


def parse_open(argv):
    """The window --open names ("menu", "preferences"...), "quit" for
    --quit, "uninstall" for --uninstall, or None."""
    return _parse(argv).open


def open_in_running(name, tray_starting=lambda: False, bus=None,
                    sleep=time.sleep, clock=time.monotonic):
    """Ask the running TuxTalk to open window *name* (or quit).

    True when it did, False when TuxTalk runs but can't (a version from
    before the menu window, or one still starting after TRAY_START_WAIT_S),
    None when no TuxTalk runs. Uses its own D-Bus connection (_session_bus).
    """
    import dbus
    own = bus is None
    if own:
        bus = _session_bus()
    try:
        deadline = clock() + TRAY_START_WAIT_S
        while not bus.name_has_owner(DBUS_NAME):
            if not tray_starting():
                return None
            if clock() >= deadline:
                return False
            sleep(0.2)
        obj = bus.get_object(DBUS_NAME, DBUS_PATH, introspect=False)
        try:
            result = getattr(obj, OPEN_METHODS[name])(dbus_interface=DBUS_INTERFACE)
        except dbus.DBusException as e:
            if e.get_dbus_name() == "org.freedesktop.DBus.Error.UnknownMethod":
                return False
            raise
        return result is None or bool(result)    # only ShowMenu answers
    finally:
        if own:
            bus.close()


def act_now(action, recording):
    """For a ready service: (send one toggle?, status)."""
    if action == "start":
        return (not recording, "already" if recording else "started")
    if action == "stop":
        return (recording, "stopped" if recording else "idle")
    return (True, "stopped" if recording else "started")


class WaitingStart:
    """Whether a recording should start once the dictation service is ready.

    Until then nothing can be recording, so every command comes down to this
    one yes or no: --start sets it, --stop clears it, --toggle flips it (two
    quick toggles while the model loads leave nothing waiting). It lapses after
    WAIT_LIMIT_S.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._until = None

    @property
    def pending(self):
        return self._until is not None and self._clock() < self._until

    def apply(self, action):
        """Record *action*; returns "waiting", "cancelled" or "idle"."""
        was = self.pending
        wanted = {"start": True, "stop": False}.get(action, not was)
        self._until = self._clock() + WAIT_LIMIT_S if wanted else None
        if wanted:
            return "waiting"
        return "cancelled" if was else "idle"

    def take(self):
        """True once if a recording is waiting to start; clears it."""
        pending = self.pending
        self._until = None
        return pending


def _session_bus():
    """A private connection to the session bus. Private: with nothing running
    this process becomes the tray, which a shared connection opened now
    (before its GLib main loop) would stop from exporting its D-Bus service.
    Tests replace this so they can never reach the user's real TuxTalk."""
    import dbus
    return dbus.SessionBus(private=True)


def ask_running(action, tray_starting=lambda: False, bus=None,
                sleep=time.sleep, clock=time.monotonic):
    """Send *action* to the running TuxTalk; its status, or None if none runs.

    A tray that holds its lock but hasn't taken the D-Bus name yet is waited
    for, so a shortcut pressed during login doesn't start a second one.
    """
    import dbus
    own = bus is None
    if own:
        bus = _session_bus()
    try:
        deadline = clock() + TRAY_START_WAIT_S
        while not bus.name_has_owner(DBUS_NAME):
            if not tray_starting():
                return None
            if clock() >= deadline:
                return "starting"
            sleep(0.2)
        obj = bus.get_object(DBUS_NAME, DBUS_PATH, introspect=False)
        try:
            return str(obj.ControlDictation(action, dbus_interface=DBUS_INTERFACE))
        except dbus.DBusException as e:
            if e.get_dbus_name() != "org.freedesktop.DBus.Error.UnknownMethod":
                raise
        # A TuxTalk from before this command: what its menus use.
        old = {"toggle": "ToggleRecording", "start": "StartRecording", "stop": "StopRecording"}
        getattr(obj, old[action])(dbus_interface=DBUS_INTERFACE)
        return "old"
    finally:
        if own:
            bus.close()


def shortcut_command(appimage_env=None):
    """The command Help suggests for a desktop shortcut on this install: the
    AppImage file for a self-managed AppImage, else the `tuxtalk` launcher
    (.deb, .rpm and AUR). The AppImage's full path, shell-quoted: GNOME runs
    a custom shortcut without a shell, so "~" isn't expanded there, and a
    folder with a space would split the path."""
    import os
    import shlex
    appimage = os.environ.get("APPIMAGE", "") if appimage_env is None else appimage_env
    if appimage and not appimage.startswith(("/opt/", "/usr/")):
        return f"{shlex.quote(appimage)} --toggle"
    return "tuxtalk --toggle"


def report(status):
    """Print what happened; the exit code for the command."""
    print(MESSAGES.get(status, status))
    return 1 if status in _FAILED else 0
