"""
The open Preferences window's pidfile, and asking that window to come to the
front.

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

FRONT = signal.SIGUSR1


def pidfile_path():
    """Where the open Preferences window keeps its pid."""
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return os.path.join(base, "talktype-prefs.pid")


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
    """True if *argv* (a process's argument list) is a Preferences window.

    Whole arguments are compared, never substrings, the same way as
    service_launcher.is_service_argv: Preferences runs as ``<python> -m
    tuxtalk.prefs`` or as the ``dictate-prefs`` console script.
    """
    argv = list(argv)
    for flag, module in zip(argv, argv[1:]):
        # talktype.prefs: Preferences from before the 0.15.0 rename, which
        # shares this pidfile.
        if flag == "-m" and module in ("tuxtalk.prefs", "talktype.prefs"):
            return True
    if not argv:
        return False
    if os.path.basename(argv[0]) == "dictate-prefs":
        return True
    return (len(argv) > 1 and os.path.basename(argv[0]).startswith("python")
            and os.path.basename(argv[1]) == "dictate-prefs")


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


def running_pid():
    """Pid of the open Preferences window, or None."""
    try:
        with open(pidfile_path()) as f:
            pid = int(f.read().strip() or "0")
    except (OSError, ValueError):
        return None
    return pid if is_prefs_pid(pid) else None


def claim_pidfile():
    """Write this process's pid as the open Preferences window's. Raises
    OSError when it can't be written."""
    with open(pidfile_path(), "w") as f:
        f.write(str(os.getpid()))


def release_pidfile():
    """Remove the pidfile if it names this process."""
    try:
        with open(pidfile_path()) as f:
            pid = int(f.read().strip() or "0")
        if pid == os.getpid():
            os.remove(pidfile_path())
    except (OSError, ValueError):
        pass        # gone already, never written, or not ours to remove


def bring_to_front(pid, tab=None):
    """Ask Preferences (*pid*) to come to the front, on *tab* if given.
    Returns True if the request was sent."""
    try:
        if tab:
            with open(_tab_request_path(), "w") as f:
                f.write(tab)
        else:
            os.remove(_tab_request_path())        # don't act on a stale request
    except OSError:
        pass
    try:
        os.kill(pid, FRONT)
        return True
    except OSError:
        return False
