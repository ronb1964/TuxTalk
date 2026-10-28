"""
The open Preferences window's pidfile, and asking that window to come to the
front, or to close the way its title bar's close does (asking about changes
not applied: CLOSE, before an uninstall).

Preferences runs as its own process and allows one instance (pidfile). A
second "open" (tray menu, GNOME extension via the tray, or a second launch)
signals the running one instead of doing nothing: SIGUSR1 = come to the
front. To also switch tab (the tray's "Your Stats...", or the update
notification's Updates tab), the tab name is written to a small request file
first, which Preferences reads when the signal arrives.

Preferences installs its handlers before it writes the pidfile, so a pid read
from the pidfile is always ready for the signal; SIGUSR1's default action is
to terminate, so signalling a process that is still starting would kill it.
"""
import os
import signal

from .logger import setup_logger

logger = setup_logger(__name__)

FRONT = signal.SIGUSR1
CLOSE = signal.SIGUSR2


def pidfile_path():
    """Where the open Preferences window keeps its pid: the private runtime
    folder, or its fallback (single_instance). Raises OSError when there's
    neither; callers treat that as no pidfile."""
    from .single_instance import runtime_dir
    return os.path.join(runtime_dir(), "talktype-prefs.pid")


def _tab_request_path():
    return pidfile_path() + ".tab"


def take_tab_request():
    """The tab the last front request asked for (then forgotten), or None."""
    try:
        with open(_tab_request_path()) as f:
            tab = f.read().strip()
        os.remove(_tab_request_path())
        return tab or None
    except OSError:
        return None


def is_prefs_argv(argv) -> bool:
    """True if *argv* (a process's argument list) is a Preferences window:
    ``<python> -m tuxtalk.prefs`` (talktype.prefs before the 0.15.0 rename,
    which shares this pidfile) or the ``dictate-prefs`` console script, read
    where an interpreter reads them (service_launcher.interpreter_target):
    a pidfile left by a crashed Preferences can name a PID another program
    has since, and FRONT's and CLOSE's default action ends a process."""
    from .service_launcher import interpreter_target
    argv = list(argv)
    if argv and os.path.basename(argv[0]) == "dictate-prefs":
        return True
    return interpreter_target(argv) in (("module", "tuxtalk.prefs"),
                                        ("module", "talktype.prefs"),
                                        ("script", "dictate-prefs"))


def _process_argv(pid):
    """The argument list of process *pid*; raises OSError if it is gone."""
    with open(f"/proc/{pid}/cmdline", "rb") as f:
        raw = f.read()
    return [arg.decode(errors="ignore") for arg in raw.split(b"\0") if arg]


def is_prefs_pid(pid) -> bool:
    """Whether *pid* is a running Preferences window.

    A pidfile left by a Preferences that crashed can name a pid the system
    has since given to another program, and FRONT's default action ends a
    process, so the pid's own command line has to say it is Preferences.
    """
    if pid <= 0:
        return False
    try:
        return is_prefs_argv(_process_argv(pid))
    except OSError:
        return False     # gone (or not ours to inspect): never signal it


# Written after the PID by a Preferences that closes on CLOSE, asking about
# changes not applied; one from before has no handler, and CLOSE ends it.
_CAN_CLOSE = "close"


def _read_pidfile():
    """(pid, whether it closes on CLOSE) from the pidfile, or (None, False)."""
    try:
        with open(pidfile_path()) as f:
            words = f.read().split()
        return int(words[0]), _CAN_CLOSE in words[1:]
    except (OSError, ValueError, IndexError):
        return None, False


def open_windows():
    """PIDs of every Preferences window of this user's running now but this
    process's own, found in /proc whatever the pidfile says (one from an
    older version, or one whose pidfile couldn't be written)."""
    me, uid = os.getpid(), os.getuid()
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            if os.stat(f"/proc/{entry}").st_uid != uid:
                continue
        except OSError:
            continue
        if is_prefs_pid(int(entry)):
            pids.append(int(entry))
    return pids


def running_pid():
    """Pid of the open Preferences window, or None."""
    pid, _can_close = _read_pidfile()
    return pid if pid is not None and is_prefs_pid(pid) else None


def claim_pidfile():
    """Write this process's pid as the open Preferences window's. Raises
    OSError when it can't be written."""
    with open(pidfile_path(), "w") as f:
        f.write(f"{os.getpid()} {_CAN_CLOSE}")


def release_pidfile():
    """Remove the pidfile if it names this process."""
    if _read_pidfile()[0] == os.getpid():
        try:
            os.remove(pidfile_path())
        except OSError:
            pass        # gone already


def bring_to_front(pid, tab=None):
    """Ask Preferences (*pid*) to come to the front, on *tab* if given.
    Returns True if the request was sent."""
    try:
        if tab:
            # Whole or not at all: the window reads it the moment the signal
            # comes, and a second request may be writing it just then.
            from .config import write_text_atomic
            write_text_atomic(_tab_request_path(), tab, keep_backup=False)
        else:
            os.remove(_tab_request_path())        # don't act on a stale request
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.info(f"Couldn't pass Preferences the tab to open: {e}")
    try:
        os.kill(pid, FRONT)
        return True
    except OSError:
        return False


def ask_to_close(wait_s) -> bool:
    """Ask the open Preferences window to close as its title bar's close
    does (it asks about changes not applied), and wait up to *wait_s* while
    the user answers; True when none is open by then. False for one from
    before CLOSE was handled (it would end without asking): the user closes
    it. Never this process's own window. It sleeps: never on a GTK main
    thread."""
    import time
    from .service_launcher import HeldProcess
    pid, can_close = _read_pidfile()
    if pid is None or pid == os.getpid():
        return True
    window = HeldProcess(pid)
    try:
        if not window.running() or not is_prefs_pid(pid):
            return True
        if not can_close:
            return False
        window.send(CLOSE)
        deadline = time.monotonic() + wait_s
        while window.running():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)
        return True
    finally:
        window.close()
