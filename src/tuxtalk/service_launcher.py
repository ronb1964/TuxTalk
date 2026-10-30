"""The one place that knows how to start and stop the dictation service.

The service is launched from the tray (at startup and on "Restart Service")
and from Preferences (on Apply/OK). Both go through here so the service always
gets the same environment, in particular its own GDK_BACKEND: Preferences runs
on native Wayland, where gtk_window_move() is ignored and the recording
indicator could not be positioned.
"""

import logging
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

# XWayland first: the recording indicator positions itself with
# gtk_window_move(), which XWayland honours and native Wayland ignores.
# The fallback matters — pinned to bare "x11", GTK cannot initialise on a
# system without XWayland and the service dies on launch, costing dictation
# entirely to protect a cosmetic window position.
SERVICE_GDK_BACKEND = "x11,wayland"

# A dictation service asked to quit (SIGTERM) stops a recording (up to 2 s),
# types what it holds (service_tick.QUIT_DRAIN_S, 20 s), runs its exit hooks
# (the music resumed, the sounds turned back up, the desktop's hotkeys
# released: several seconds when KWin is slow) and is ended by the kernel
# QUIT_LIMIT_S after its handler runs, whatever it's doing then
# (app._handle_sigterm). Whoever needs it gone waits QUIT_WAIT_S for that,
# then kills it; a new service waits as long for its lock.
QUIT_LIMIT_S = 40
QUIT_WAIT_S = QUIT_LIMIT_S + 3.0

# Hotkey test mode (Preferences, Test Hotkeys). While this flag file is fresh,
# the service reports hotkey presses instead of recording. Preferences touches
# it every HOTKEY_TEST_REFRESH_S while its dialog is open and removes it on
# close. One older than HOTKEY_TEST_LEASE_S is ignored, so a Preferences that
# crashed or was killed can't leave dictation switched off.
HOTKEY_TEST_LEASE_S = 6.0
HOTKEY_TEST_REFRESH_S = 2


def ready_file_path() -> str:
    """Where the service writes its PID once its model is loaded, so a tray
    started after it (one that missed NotifyServiceReady) knows it's ready.
    Raises OSError when there's no private folder for it (callers catch it)."""
    from .single_instance import runtime_dir
    return os.path.join(runtime_dir(), "tuxtalk-service-ready")


def read_ready_pid():
    """The PID in the ready file, or None."""
    try:
        with open(ready_file_path()) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def write_ready_pid(pid) -> None:
    """Record *pid* as the ready service. Never raises: the D-Bus report is
    the main route, this only covers a tray that starts later."""
    try:
        path = ready_file_path()
        tmp = f"{path}.{pid}"
        with open(tmp, "w") as f:
            f.write(str(pid))
        os.replace(tmp, path)
    except OSError as e:
        logger.debug(f"Could not write the service's ready file: {e}")


def hotkey_test_flag_path() -> str:
    """Where Preferences keeps the hotkey test flag for the service to see."""
    from .config import get_data_dir
    return os.path.join(get_data_dir(), ".hotkey_test_mode")


# Python site-package paths to expose in dev mode, so the service can find the
# system PyGObject that the venv cannot see.
_DEV_SITE_PACKAGES = (
    "/usr/lib64/python3.14/site-packages",
    "/usr/lib/python3.14/site-packages",
    "/usr/lib64/python3.13/site-packages",
    "/usr/lib/python3.13/site-packages",
)


# Python's own flags TuxTalk could be started with, ahead of -m or the
# script; anything else there (-c, -, --) makes what follows mere data.
_PYTHON_FLAGS = re.compile(r"-[bBdEiIOPqsSuvx]+")


def interpreter_target(argv):
    """("module", name) for ``<python> -m name``, ("script", file name) for
    ``<python> path``, else None. Only the interpreter's own flags (and -X's
    or -W's value) may come first: ``python3 -c '...' -m tuxtalk.app`` runs
    '...', not TuxTalk."""
    argv = list(argv)
    if not argv or not os.path.basename(argv[0]).startswith("python"):
        return None
    args = argv[1:]
    while args and args[0] != "-m":
        if args[0] in ("-X", "-W"):
            args = args[2:]
        elif _PYTHON_FLAGS.fullmatch(args[0]):
            args = args[1:]
        else:
            break
    if args[:1] == ["-m"]:
        return ("module", args[1]) if len(args) > 1 else None
    if args and not args[0].startswith("-"):
        return ("script", os.path.basename(args[0]))
    return None


def is_service_argv(argv) -> bool:
    """True if *argv* (a process's argument list) is the dictation service.

    The service only ever runs one of two ways:
      * ``<python> -m tuxtalk.app`` — dev checkouts, the AppImage, the .deb and
        .rpm (which repackage the AppImage) and the Flatpak.
      * the ``dictate`` console script from a pip install, which the kernel
        runs as ``<python> /path/bin/dictate`` (or ``/path/bin/dictate``).

    So this compares whole arguments where an interpreter reads them
    (interpreter_target), never substrings: ``bash -c "pgrep -f
    tuxtalk.app"`` or ``python3 -c '...' -m tuxtalk.app`` would be signalled
    (SIGUSR1 kills a process that doesn't handle it, and a quit that runs long
    ends in SIGKILL). "-m tuxtalk.tray" and bin/dictate-tray never match.
    talktype.app: the service from before the 0.15.0 rename, which a 0.14.x
    in-app update leaves running (it holds the shared lock).
    """
    argv = list(argv)
    if argv and os.path.basename(argv[0]) == "dictate":
        return True
    return interpreter_target(argv) in (("module", "tuxtalk.app"), ("module", "talktype.app"),
                                        ("script", "dictate"))


def is_pre_rename_service_argv(argv) -> bool:
    """True for a dictation service from before the 0.15.0 rename
    (``-m talktype.app``): old code that should give way to the new version."""
    return interpreter_target(argv) == ("module", "talktype.app")


def pre_rename_service_pids():
    """PIDs of running pre-rename (TalkType) dictation services."""
    return [pid for pid in find_service_pids()
            if is_pre_rename_service_argv(read_argv(pid) or [])]


def is_pre_rename_tray_argv(argv) -> bool:
    """True for a tray from before the 0.15.0 rename (``-m talktype.tray``).
    It shares TuxTalk's single-instance lock, so it keeps TuxTalk from starting."""
    return interpreter_target(argv) == ("module", "talktype.tray")


def pre_rename_tray_pids():
    """PIDs of this user's running pre-rename (TalkType) trays.

    Exact arguments, as for the service, so nothing but an old tray matches.
    """
    me = os.getpid()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    pids = []
    for entry in entries:
        if not entry.isdigit() or int(entry) == me:
            continue
        pid = int(entry)
        try:
            if os.stat(f"/proc/{pid}").st_uid != os.getuid():
                continue
        except OSError:
            continue
        if is_pre_rename_tray_argv(read_argv(pid) or []):
            pids.append(pid)
    return pids


# The Flatpak of TalkType before the rename. Its sandbox has its own runtime
# folder, so it never sees TuxTalk's lock and would type every dictation twice.
OLD_TALKTYPE_FLATPAK = "io.github.ronb1964.TalkType"


def stop_old_talktype_flatpak(run=subprocess.run) -> bool:
    """Kill a running old TalkType Flatpak; True only if one was stopped.

    Any error asking Flatpak (not installed, hung, refused) counts as "not
    running", and both calls are short so startup is never held up.
    """
    if os.environ.get("FLATPAK_ID"):
        return False    # inside a sandbox: no flatpak command, nothing of ours to stop
    try:
        ps = run(["flatpak", "ps", "--columns=application"],
                 capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return False
    if OLD_TALKTYPE_FLATPAK not in (ps.stdout or "").split():
        return False
    try:
        result = run(["flatpak", "kill", OLD_TALKTYPE_FLATPAK],
                     capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Could not stop the old {OLD_TALKTYPE_FLATPAK} Flatpak: {e}")
        return False
    if result.returncode != 0:
        logger.warning(f"Could not stop the old {OLD_TALKTYPE_FLATPAK} Flatpak: "
                       f"{(result.stderr or '').strip()}")
        return False
    logger.info(f"Stopped the old {OLD_TALKTYPE_FLATPAK} Flatpak "
                "so dictation isn't typed twice")
    return True


def read_argv(pid):
    """A process's argument list from /proc, or None if it can't be read."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except OSError:
        return None  # gone already, or not ours to read
    # cmdline is NUL-separated with a trailing NUL; kernel threads have none.
    return [a.decode(errors="ignore") for a in raw.split(b"\0") if a]


def is_service_pid(pid) -> bool:
    """True if *pid* is a running dictation service owned by this user."""
    try:
        if os.stat(f"/proc/{pid}").st_uid != os.getuid():
            return False
    except OSError:
        return False
    argv = read_argv(pid)
    return bool(argv) and is_service_argv(argv)


def find_service_pids():
    """PIDs of every dictation service this user is running, read from /proc.

    No subprocess: the tray polls this, and pgrep/pkill only offer substring
    matching on the joined command line, which is too loose (see
    is_service_argv).
    """
    me = os.getpid()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    return [
        int(e) for e in entries
        if e.isdigit() and int(e) != me and is_service_pid(int(e))
    ]


def stop_dictation_service(force: bool = False) -> None:
    """Signal the dictation service, and only the dictation service, without
    waiting: the pre-0.15.0 service at start. *force* sends SIGKILL instead
    of SIGTERM: the welcome dialog, for a service started during setup.

    Never raises: every caller is a cleanup path, where a service left
    running is better than an exception. Waiting, and killing past the
    time, is end_dictation_service's.
    """
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        pids = find_service_pids()
    except Exception as e:
        logger.warning(f"Could not look for the dictation service: {e}")
        return
    for pid in pids:
        _signal(pid, sig)
    # SIGKILL skips the service's exit hooks, and SIGTERM's run only once it
    # has typed what it holds (service_tick._quit): hand its hotkeys back to
    # the desktop from here, now.
    release_desktop_hotkeys()


# A process still running after its wait gets SIGKILL, and this long to go.
KILL_WAIT_S = 2.0


def end_dictation_service(wait_s=QUIT_WAIT_S, pids=None) -> bool:
    """Stop the dictation services running now and wait until they've gone;
    False when one survives even SIGKILL (stuck in the kernel). SIGTERM
    first, so it types what it holds; SIGKILL to what's left after *wait_s*:
    an older service with no deadline of its own, or one whose deadline was
    never set (native code held its main thread). Only *pids*, the services
    found when the caller decided (found here if None): one started since
    (Dictation turned on again) is left alone.
    The desktop gets its hotkeys back at once: the service's own exit hooks
    run only after it has typed. It sleeps: never on a GTK main thread."""
    return end_processes(find_service_pids() if pids is None else pids, wait_s,
                         still_ours=is_service_pid, after_term=release_desktop_hotkeys)


def end_in_background(then, pids) -> None:
    """end_dictation_service for *pids* (found on GTK's thread when the user
    decided, so a service started after can't be among them) on a thread of
    its own, then *then*(whether they've gone) on GTK's main loop: the tray's
    menu mustn't wait."""
    from gi.repository import GLib

    def run():
        try:
            ended = end_dictation_service(pids=pids)
        except Exception as e:
            logger.error(f"Could not stop the dictation service: {e}", exc_info=True)
            ended = False
        GLib.idle_add(then, ended)
    threading.Thread(target=run, daemon=True, name="end-service").start()


def end_processes(pids, wait_s, still_ours=None, after_term=None) -> bool:
    """SIGTERM *pids*, wait up to *wait_s* for them to exit, then SIGKILL
    what's left and wait KILL_WAIT_S; False when one is still there. Each is
    held first (HeldProcess) and checked again with *still_ours* once held, so
    a PID another program took meanwhile is never signalled. *after_term*
    runs once they've had SIGTERM. It sleeps: never on a GTK main thread."""
    procs = [HeldProcess(pid) for pid in pids]
    try:
        for proc in procs:
            if still_ours is not None and not still_ours(proc.pid):
                proc.gone = True        # its PID belongs to another program now
        for proc in procs:
            proc.send(signal.SIGTERM)
        if after_term is not None:
            after_term()
        if _wait_for_exit(procs, wait_s):
            return True
        logger.warning("A TuxTalk process didn't quit in time; ending it")
        for proc in procs:
            proc.send(signal.SIGKILL)
        if _wait_for_exit(procs, KILL_WAIT_S):
            return True
        logger.error("A TuxTalk process is still running after SIGKILL")
        return False
    finally:
        for proc in procs:
            proc.close()


def _wait_for_exit(procs, wait_s) -> bool:
    """Whether every one of *procs* has exited within *wait_s* seconds."""
    deadline = time.monotonic() + wait_s
    while any(proc.running() for proc in procs):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


class HeldProcess:
    """A process to end, held by a pidfd where the kernel has them (5.3 on):
    signals reach that process even if its PID is reused. Where there are
    none (an old kernel, a sandbox that blocks them), by PID and its start
    time, checked before each signal: a PID reused meanwhile is someone
    else's."""

    def __init__(self, pid):
        self.pid = pid
        self.fd = None
        self.gone = False
        self.started = None
        try:
            self.fd = os.pidfd_open(pid)
        except ProcessLookupError:
            self.gone = True
        except OSError as e:
            logger.debug(f"No pidfd for {pid}, signalling it by PID: {e}")
            self.started = _start_time(pid)
            self.gone = self.started is None

    def send(self, sig) -> None:
        """Send *sig*, unless the process has gone."""
        if not self.running():
            return
        try:
            if self.fd is None:
                os.kill(self.pid, sig)
            else:
                signal.pidfd_send_signal(self.fd, sig)
        except ProcessLookupError:
            self.gone = True
        except OSError as e:
            logger.warning(f"Could not signal process {self.pid}: {e}")

    def running(self) -> bool:
        """Whether it hasn't exited. A child that exited is a zombie until
        reaped, and still takes signals: the pidfd turns readable at exit."""
        if self.gone:
            return False
        if self.fd is not None:
            self.gone = bool(select.select([self.fd], [], [], 0)[0])
        else:
            self.gone = _start_time(self.pid) != self.started
        return not self.gone

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def _start_time(pid):
    """When *pid* started (clock ticks after boot, /proc/<pid>/stat), or None
    when it's gone or a zombie: with the PID, which process it is."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    return None if fields[0] == "Z" else fields[19]


def _signal(pid, sig) -> None:
    """Send *sig* to *pid*, which may have exited since it was found."""
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass
    except OSError as e:
        logger.warning(f"Could not signal process {pid}: {e}")


def release_desktop_hotkeys() -> None:
    """Hand back the hotkeys hotkeys._claim_desktop_hotkeys had the desktop hold,
    or KWin or COSMIC would keep swallowing F8 with dictation off. The X
    server lets go by itself when the service's connection closes, and
    GNOME's extension when the tray reports the service stopped. Never
    raises: quit path."""
    try:
        from . import cosmic_hotkeys, kwin_hotkeys
        kwin_hotkeys.release()
        cosmic_hotkeys.release()
    except Exception as e:      # the quit goes on; the next start claims the keys afresh
        logger.info(f"Could not release the desktop's hotkeys: {e}")


# The GDK_BACKEND the service's own (SERVICE_GDK_BACKEND) took the place of,
# "" for none, kept in the service's environment.
_USERS_GDK_BACKEND = "TUXTALK_USERS_GDK_BACKEND"


def build_service_env(base_env=None, dev_pythonpath=None):
    """Return the environment the dictation service must be started with.

    Always overrides GDK_BACKEND rather than deferring to what the caller
    happens to have: Preferences runs on native Wayland by design, and the
    service must not inherit that.
    """
    env = dict(os.environ if base_env is None else base_env)
    # The user's own, for Preferences the service opens (launch_preferences).
    env[_USERS_GDK_BACKEND] = env.get("GDK_BACKEND", "")
    env["GDK_BACKEND"] = SERVICE_GDK_BACKEND
    if dev_pythonpath:
        env["PYTHONPATH"] = dev_pythonpath
    return env


def dev_pythonpath():
    """PYTHONPATH for running from a source checkout, or None for the AppImage."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    src_dir = os.path.join(project_root, "src")
    if not os.path.exists(src_dir):
        return None
    return ":".join([os.path.abspath(src_dir), *_DEV_SITE_PACKAGES])


def launch_dictation_service():
    """Start the dictation service: its Popen object. Raises OSError when it
    can't be started.

    Callers must not spawn it themselves, so every path starts it the same way
    (tests/test_service_launch_parity.py checks this).
    """
    # Every install runs the module. The AppImage, .deb and .rpm child inherits
    # PYTHONPATH and the library paths from dictate-tray; in a source checkout
    # dev_pythonpath() adds src/ and the system PyGObject.
    env = build_service_env(dev_pythonpath=dev_pythonpath())
    proc = subprocess.Popen([sys.executable, "-m", "tuxtalk.app"], env=env)
    logger.info(f"Started dictation service via python -m tuxtalk.app (PID {proc.pid})")
    return proc


def launch_transcribe_window():
    """Start the Transcribe a File window as its own process, like
    Preferences (and keeping the caller's GDK_BACKEND for the same reason).
    A second one brings the open window to the front and quits."""
    env = os.environ.copy()
    pythonpath = dev_pythonpath()
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    return subprocess.Popen([sys.executable, "-m", "tuxtalk.transcribe_window"], env=env)


def launch_preferences(tab=None):
    """Start the Preferences window as its own process; returns the Popen.

    Shared by the tray, the first-run screens and the dictation service.
    Unlike the service it keeps the caller's GDK_BACKEND: Preferences runs
    natively, so its dropdowns work on Wayland. Opened by the service (the
    GNOME extension's Preferences with no tray running), it gets the user's
    GDK_BACKEND back in place of the service's: under XWayland a dropdown
    doesn't open on a click.
    """
    env = os.environ.copy()
    if _USERS_GDK_BACKEND in env:
        users = env.pop(_USERS_GDK_BACKEND)
        if users:
            env["GDK_BACKEND"] = users
        else:
            env.pop("GDK_BACKEND", None)
    pythonpath = dev_pythonpath()
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    cmd = [sys.executable, "-m", "tuxtalk.prefs"]
    if tab:
        cmd.append(f"--tab={tab}")
    return subprocess.Popen(cmd, env=env)
