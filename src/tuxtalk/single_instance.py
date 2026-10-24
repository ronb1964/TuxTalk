"""One copy of each TuxTalk process: the dictation service and the tray each
hold a lock file in the user's runtime folder for as long as they run.

flock is atomic, and the kernel lets go of the lock when the process exits,
however it exits.
"""
import fcntl
import os
import time


def runtime_dir() -> str:
    """The user's runtime folder, where the lock files go.

    Inside a Flatpak, XDG_RUNTIME_DIR (/run/user/N) is shared with the host,
    so a host install's lock would make the Flatpak's process exit as
    "already running" and never start. The per-app $XDG_RUNTIME_DIR/app/
    $FLATPAK_ID is private to the sandbox, so the locks go there.
    """
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    fid = os.environ.get("FLATPAK_ID")
    if fid:
        app_dir = os.path.join(base, "app", fid)
        if os.path.isdir(app_dir):
            return app_dir
    return base


def open_lock(name: str):
    """The lock file *name* in runtime_dir(), open for claim(). Raises
    OSError when it can't be opened (no runtime folder, no permission),
    which is not "already running".

    Opened without truncating, so the running copy's PID stays in it until
    this one holds the lock. Close-on-exec: an in-place restart (os.execv,
    the updater's Restart Now) would otherwise inherit the locked file, fail
    to lock it again and exit as "already running".
    """
    fd = os.open(os.path.join(runtime_dir(), name), os.O_RDWR | os.O_CREAT, 0o600)
    lockfile = os.fdopen(fd, "r+")
    flags = fcntl.fcntl(lockfile.fileno(), fcntl.F_GETFD)
    fcntl.fcntl(lockfile.fileno(), fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
    return lockfile


def claim(lockfile, wait_s: float = 0.0) -> bool:
    """Lock *lockfile* for this process and write its PID in it (for people
    looking; the lock is the flock). False when another process still holds
    it after *wait_s* seconds. Keep the file open while the process runs:
    closing it lets go of the lock."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
    lockfile.truncate(0)
    lockfile.write(str(os.getpid()))
    lockfile.flush()
    return True


def held_elsewhere(name: str) -> bool:
    """Whether another process holds the lock file *name* now."""
    try:
        fd = os.open(os.path.join(runtime_dir(), name), os.O_RDONLY)
    except OSError:
        return False        # no lock file: nothing has ever started here
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False        # closing the file below lets go of it again
    except BlockingIOError:
        return True
    except OSError:
        return False
    finally:
        os.close(fd)
