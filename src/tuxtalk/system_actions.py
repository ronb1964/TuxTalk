"""System voice commands (TuxTalk Labs): lock the screen, "what can I say" and
screenshots (screenshot.py).

The lock goes through the desktop's own lock screen, so its password
applies and TuxTalk never handles one: through logind
(`loginctl lock-session`), or on Cinnamon, MATE and Xfce through their
screensaver, which locks those desktops and never tells logind it did.
Asking is not locking: a stopped or broken locker leaves the screen open.
So the lock is looked for a few seconds, and only one seen gets the ready
beep; any other gets a plain note to check the screen. logind's
LockedHint says the lock screen is up. These screensavers can only say
they are on, so the lock is taken as seen when one that was off comes on
after the request, where it locks when asked. That can't rule out a
screensaver started by hand with --disable-locking, nor a session bus
whose policy refuses Lock while the screen blanks for idleness at that
moment. Programs are run with a timeout and no shell; the wait is on the
dictation worker, never GTK's thread."""
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
# Xfconf's answers for a setting never changed: the screensaver's default.
_XFCONF_UNSET = ("org.xfce.Xfconf.Error.PropertyNotFound",
                 "org.xfce.Xfconf.Error.ChannelNotFound")


def _session_bus() -> Gio.DBusConnection:
    """The session bus TuxTalk shares; raises GLib.Error when there is none."""
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _cinnamon_locks_this_user() -> bool:
    """Whether cinnamon-screensaver locks for this user, as its
    user_can_lock() decides: not for a user in the nopasswdlogin group, nor
    in a live or guest session. It comes on for every Lock all the same.
    Its --disable-locking can't be seen (it renames its process at start),
    and nothing in Cinnamon or Mint starts it so. What can't be read counts
    against it."""
    try:
        if GLib.get_user_name() in grp.getgrnam("nopasswdlogin").gr_mem:
            return False
    except KeyError:
        pass
    try:
        with open(_KERNEL_COMMAND_LINE, encoding="utf-8", errors="replace") as f:
            boot = f.read()
    except OSError as e:
        logger.warning(f"Couldn't read {_KERNEL_COMMAND_LINE}: {e}")
        return False
    if "boot=casper" in boot or "boot=live" in boot:
        return False
    return "/tmp/guest" not in os.path.expanduser("~")


def _mate_locks() -> bool:
    """Whether mate-screensaver locks: it ignores Lock while the lockdown
    setting disable-lock-screen is on. What can't be read counts against it."""
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup("org.mate.lockdown", True) if source else None
    if schema is None or not schema.has_key("disable-lock-screen"):
        logger.warning("MATE's lockdown settings can't be read")
        return False
    return not Gio.Settings.new_full(schema, None, None).get_boolean("disable-lock-screen")


def _xfce_locks() -> bool:
    """Whether xfce4-screensaver locks: it ignores Lock while its
    /lock/enabled (Lock Screen in its settings) is off; on unless changed.
    What can't be read counts against it."""
    try:
        reply = _session_bus().call_sync(
            "org.xfce.Xfconf", "/org/xfce/Xfconf", "org.xfce.Xfconf", "GetProperty",
            GLib.Variant("(ss)", ("xfce4-screensaver", "/lock/enabled")),
            GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, _QUERY_TIMEOUT_S * 1000, None)
    except GLib.Error as e:
        if Gio.DBusError.get_remote_error(e) in _XFCONF_UNSET:
            return True
        logger.warning(f"Xfce's screensaver settings can't be read: {e.message}")
        return False
    return reply.unpack()[0] is True


class _Screensaver(NamedTuple):
    """A desktop's screensaver: its bus name (also its interface), object
    path, Lock's arguments, and whether it locks when asked: where it
    doesn't, it still comes on when the computer is idle, and that must not
    be taken for the lock."""
    name: str
    path: str
    lock_args: GLib.Variant | None
    locks_when_asked: Callable[[], bool]


# None of these sets logind's LockedHint; cinnamon-screensaver only listens to
# logind's lock and unlock.
_SCREENSAVERS = {
    "cinnamon": _Screensaver("org.cinnamon.ScreenSaver", "/org/cinnamon/ScreenSaver",
                             GLib.Variant("(s)", ("",)), _cinnamon_locks_this_user),
    "mate": _Screensaver("org.mate.ScreenSaver", "/org/mate/ScreenSaver", None, _mate_locks),
    "xfce": _Screensaver("org.xfce.ScreenSaver", "/org/xfce/ScreenSaver", None, _xfce_locks),
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
        logger.warning(f"loginctl show-session gave no LockedHint (exit code "
                       f"{result.returncode}, {result.stderr.strip() or answer!r})")
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


def _screensaver_on(bus: Gio.DBusConnection, saver: _Screensaver) -> bool | None:
    """Whether the screensaver *saver* is on (GetActive); None when it can't
    be asked: it isn't running, or didn't answer in time. Never started for
    the question: installed beside another locker (an Xfce with
    light-locker), a second screensaver would run for the rest of the
    session."""
    try:
        reply = bus.call_sync(
            saver.name, saver.path, saver.name, "GetActive", None, GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NO_AUTO_START, _QUERY_TIMEOUT_S * 1000, None)
    except GLib.Error as e:
        logger.info(f"{saver.name} can't be asked whether it is on: {e.message}")
        return None
    return bool(reply.unpack()[0])


def _send_lock(bus: Gio.DBusConnection, saver: _Screensaver):
    """Ask *saver* to lock, with no answer awaited: mate-screensaver never
    answers Lock. The bus daemon's answer to a ping sent after it shows the
    request has gone out: a message still queued when the last hold on the
    shared connection goes may be dropped."""
    message = Gio.DBusMessage.new_method_call(saver.name, saver.path, saver.name, "Lock")
    if saver.lock_args is not None:
        message.set_body(saver.lock_args)
    message.set_flags(Gio.DBusMessageFlags.NO_REPLY_EXPECTED
                      | Gio.DBusMessageFlags.NO_AUTO_START)
    try:
        bus.send_message(message, Gio.DBusSendMessageFlags.NONE)
        bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.Peer",
                      "Ping", None, None, Gio.DBusCallFlags.NONE, _QUERY_TIMEOUT_S * 1000, None)
    except GLib.Error as e:
        logger.warning(f"Couldn't ask {saver.name} to lock: {e.message}")


def _screensaver_lock(saver: _Screensaver, sleep) -> bool | None:
    """Lock through *saver*: whether it came on within LOCK_CHECK_S, having
    been off and locking when asked, or None when it isn't there to ask (an
    Xfce with light-locker, say), for logind to be asked instead."""
    try:
        bus = _session_bus()
    except GLib.Error as e:
        logger.info(f"Locking through logind: no session bus ({e.message})")
        return None
    was_on = _screensaver_on(bus, saver)
    if was_on is None:
        return None
    _send_lock(bus, saver)
    if was_on:
        logger.info("Lock screen: the screensaver was already on, so it can't show the lock")
        return False
    if not saver.locks_when_asked():
        logger.info("Lock screen: the screensaver doesn't lock here, so it can't show the lock")
        return False
    return _confirmed(lambda: _screensaver_on(bus, saver), sleep)


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
    seen = "the screensaver came on for it"
    if confirmed is None:
        session = session_id()
        if not _ask_to_lock(session):
            command_notices.say("lock_failed", beeps_on)
            return False
        seen, confirmed = "the session says it is locked", _confirmed(
            lambda: locked(session), sleep)
    if confirmed:
        logger.info(f"Voice command: lock screen; {seen}")
        beeps.beep(beeps_on, *beeps.READY_BEEP)
    else:
        logger.info("Voice command: lock screen asked; the lock wasn't seen")
        # Only the note sounds: a ready beep then a cancel beep would say both.
        command_notices.say("lock_unconfirmed", beeps_on)
    return True


def run(request: SystemRequest, beeps_on: bool) -> bool:
    """Do the system command *request* is (lock, commands, or a screenshot of
    the screen or the window). True: done; False: refused with a note."""
    if request.action == "lock":
        return lock(beeps_on)
    if request.action == "commands":
        tray_link.show_voice_commands()
        beeps.beep(beeps_on, *beeps.READY_BEEP)
        logger.info("Voice command: what can I say")
        return True
    if request.action in ("screenshot", "window screenshot"):
        return screenshot.take(beeps_on, window=request.action == "window screenshot")
    raise ValueError(f"Unknown system command: {request.action}")
