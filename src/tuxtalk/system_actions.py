"""System voice commands (TuxTalk Labs): lock the screen, "what can I say" and
screenshots (screenshot.py).

The lock goes through the desktop's own lock screen, so its password
applies and TuxTalk never handles one: through logind
(`loginctl lock-session`), or on Cinnamon, MATE and Xfce through their
screensaver, which locks those desktops and never tells logind it did.
Asking is not locking: a stopped or broken locker leaves the screen open.
So the lock is looked for a few seconds, in the session's LockedHint or in
the screensaver coming on, and only a lock seen there gets the ready beep;
any other gets a plain note to check the screen. Programs are run with a
timeout and no shell; the wait is on the dictation worker, never GTK's
thread."""
import grp
import os
import subprocess
import time
from collections.abc import Callable
from typing import NamedTuple

from gi.repository import Gio, GLib

from . import beeps, command_notices, desktop_detect, screenshot, tray_link
from .command_phrases import SystemRequest
from .logger import setup_logger

logger = setup_logger(__name__)

LOCK_CHECK_S = 3.0      # how long the locker gets to say it locked
_LOCK_LOOK_EVERY_S = 0.5
_LOCK_TIMEOUT_S = 5
_QUERY_TIMEOUT_S = 2

_KERNEL_COMMAND_LINE = "/proc/cmdline"
_PROCESS_COMMAND_LINE = "/proc/{pid}/cmdline"


def _started_with(bus_name: str, flag: str) -> bool | None:
    """Whether the program that owns *bus_name* was started with *flag*;
    None when that can't be read."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        pid = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                            "org.freedesktop.DBus", "GetConnectionUnixProcessID",
                            GLib.Variant("(s)", (bus_name,)), GLib.VariantType("(u)"),
                            Gio.DBusCallFlags.NONE, _QUERY_TIMEOUT_S * 1000, None).unpack()[0]
        with open(_PROCESS_COMMAND_LINE.format(pid=pid), "rb") as f:
            args = f.read().decode(errors="replace").split("\0")
    except (GLib.Error, OSError) as e:
        logger.warning(f"Couldn't read how {bus_name} was started: {e}")
        return None
    return flag in args


def _cinnamon_locks_this_user(saver) -> bool:
    """Whether cinnamon-screensaver locks for this user, as its
    user_can_lock() decides: not when started with --disable-locking, nor for
    a user in the nopasswdlogin group, nor in a live or guest session. It
    comes on for every Lock all the same, so its coming on shows the lock
    only where this holds. What can't be read counts against it."""
    user = GLib.get_user_name()
    try:
        if user in grp.getgrnam("nopasswdlogin").gr_mem:
            return False
    except KeyError:
        pass
    try:
        with open(_KERNEL_COMMAND_LINE, encoding="utf-8", errors="replace") as f:
            boot = f.read().split()
    except OSError as e:
        logger.warning(f"Couldn't read {_KERNEL_COMMAND_LINE}: {e}")
        return False
    if "boot=casper" in boot or "boot=live" in boot:
        return False
    if "/tmp/guest" in os.path.expanduser("~"):
        return False
    return _started_with(saver.name, "--disable-locking") is False


class _Screensaver(NamedTuple):
    """A desktop's screensaver: its bus name (also its interface), object
    path, Lock's arguments, and whether its coming on after Lock means the
    screen is locked."""
    name: str
    path: str
    lock_args: GLib.Variant | None
    on_means_locked: Callable[["_Screensaver"], bool]


# None of these sets logind's LockedHint; cinnamon-screensaver only listens to
# logind's lock and unlock. MATE's and Xfce's come on for Lock only when
# locking is allowed.
_SCREENSAVERS = {
    "cinnamon": _Screensaver("org.cinnamon.ScreenSaver", "/org/cinnamon/ScreenSaver",
                             GLib.Variant("(s)", ("",)), _cinnamon_locks_this_user),
    "mate": _Screensaver("org.mate.ScreenSaver", "/org/mate/ScreenSaver", None,
                         lambda _saver: True),
    "xfce": _Screensaver("org.xfce.ScreenSaver", "/org/xfce/ScreenSaver", None,
                         lambda _saver: True),
}


def session_id() -> str:
    """This login session's id, or "auto": loginctl then takes the session
    of the process asking."""
    return os.environ.get("XDG_SESSION_ID") or "auto"


def locked(session: str) -> bool | None:
    """Whether logind says *session* is locked; None when it can't tell."""
    cmd = ["loginctl", "show-session", session, "-p", "LockedHint", "--value"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                timeout=_QUERY_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"loginctl show-session failed: {e}")
        return None
    answer = result.stdout.strip()
    if result.returncode != 0 or answer not in ("yes", "no"):
        return None
    return answer == "yes"


def _ask_to_lock(session: str) -> bool:
    """Whether logind took the request to lock *session*."""
    try:
        result = subprocess.run(["loginctl", "lock-session", session],
                                capture_output=True, timeout=_LOCK_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"loginctl lock-session failed: {e}")
        return False
    if result.returncode != 0:
        logger.warning(f"loginctl lock-session failed with exit code {result.returncode}")
        return False
    return True


def _screensaver_on(saver: _Screensaver) -> bool | None:
    """Whether the screensaver *saver* is on (GetActive); None when it can't
    be asked: it isn't running, or didn't answer in time. Never started for
    the question: installed beside another locker (an Xfce with
    light-locker), a second screensaver would run for the rest of the
    session."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(saver.name, saver.path, saver.name, "GetActive", None,
                              GLib.VariantType("(b)"), Gio.DBusCallFlags.NO_AUTO_START,
                              _QUERY_TIMEOUT_S * 1000, None)
    except GLib.Error as e:
        logger.info(f"{saver.name} can't be asked whether it is on: {e.message}")
        return None
    return bool(reply.unpack()[0])


def _send_lock(saver: _Screensaver):
    """Ask *saver* to lock, with no answer awaited: mate-screensaver never
    answers Lock, and whether it locked is seen in GetActive."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call(saver.name, saver.path, saver.name, "Lock", saver.lock_args, None,
                 Gio.DBusCallFlags.NO_AUTO_START, -1, None, None)
        bus.flush_sync(None)
    except GLib.Error as e:
        logger.warning(f"Couldn't ask {saver.name} to lock: {e.message}")


def _screensaver_lock(saver: _Screensaver, sleep) -> bool | None:
    """Lock through *saver*: whether its coming on showed the lock within
    LOCK_CHECK_S, or None when it isn't there to ask (an Xfce with
    light-locker, say), for logind to be asked instead."""
    was_on = _screensaver_on(saver)
    if was_on is None:
        return None
    _send_lock(saver)
    if was_on:
        logger.info("Lock screen: the screensaver was already on, so it can't show the lock")
        return False
    if not saver.on_means_locked(saver):
        logger.info("Lock screen: the screensaver only blanks the screen for this user")
        return False
    return _confirmed(lambda: _screensaver_on(saver), sleep)


def _confirmed(is_locked, sleep) -> bool:
    """Whether *is_locked* says the screen is locked within LOCK_CHECK_S. A
    state it can't read isn't asked again: that query could be slow each
    time."""
    for _ in range(round(LOCK_CHECK_S / _LOCK_LOOK_EVERY_S)):
        sleep(_LOCK_LOOK_EVERY_S)
        answer = is_locked()
        if answer is not False:
            return bool(answer)
    return False


def lock(beeps_on: bool, sleep=time.sleep) -> bool:
    """Lock the screen. False: logind refused, said with a note. True once
    it was asked; the ready beep only when the screensaver or the session
    then shows it locked, and otherwise a note saying the lock isn't
    confirmed."""
    saver = _SCREENSAVERS.get(desktop_detect.get_desktop_environment())
    confirmed = _screensaver_lock(saver, sleep) if saver else None
    who = "the screensaver"
    if confirmed is None:
        session = session_id()
        if not _ask_to_lock(session):
            command_notices.say("lock_failed", beeps_on)
            return False
        who, confirmed = "the session", _confirmed(lambda: locked(session), sleep)
    if confirmed:
        logger.info(f"Voice command: lock screen; {who} says it is locked")
        beeps.beep(beeps_on, *beeps.READY_BEEP)
    else:
        logger.info(f"Voice command: lock screen asked; {who} doesn't say it is locked")
        # Only the note sounds: a ready beep then a cancel beep would say both.
        command_notices.say("lock_unconfirmed", beeps_on)
    return True


def run(request: SystemRequest, beeps_on: bool) -> bool:
    """Do the system command *request* is (lock, commands or screenshot).
    True: done; False: refused with a note."""
    if request.action == "lock":
        return lock(beeps_on)
    if request.action == "commands":
        tray_link.show_voice_commands()
        beeps.beep(beeps_on, *beeps.READY_BEEP)
        logger.info("Voice command: what can I say")
        return True
    return screenshot.take(beeps_on)
