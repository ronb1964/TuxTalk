"""One copy of each TuxTalk process: the dictation service and the tray each
hold a lock file in the user's runtime folder for as long as they run.

flock is atomic, and the kernel lets go of the lock when the process exits,
however it exits.
"""
import fcntl
import os
import stat
import time

from .logger import setup_logger

logger = setup_logger(__name__)


# A program with one window (claim_window) waits this long for an open one
# that holds the lock to write its pid, and for one that didn't answer (it's
# closing) to let go.
FRONT_WAIT_S = 2.0
CLOSING_WAIT_S = 10.0

# Where the private fallback folder goes: fixed, not TMPDIR, so every copy
# started from anywhere (a launcher, a terminal) meets the same locks.
FALLBACK_ROOT = "/tmp"


def runtime_dir() -> str:
    """The user's runtime folder, where the lock files go.

    Inside a Flatpak, XDG_RUNTIME_DIR (/run/user/N) is shared with the host,
    so a host install's lock would make the Flatpak's process exit as
    "already running" and never start. The per-app $XDG_RUNTIME_DIR/app/
    $FLATPAK_ID is private to the sandbox, so the locks go there.

    A session started without one (a desktop started by hand, without
    logind or elogind) gets a private folder in /tmp instead, shared by
    every copy of TuxTalk on this computer, as the runtime folder is: with
    no lock at all, two services typed every dictation twice and two GNOME
    extension installs could meet.
    """
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    fid = os.environ.get("FLATPAK_ID")
    if fid:
        app_dir = os.path.join(base, "app", fid)
        if _private(app_dir):
            return app_dir
    if _private(base):
        return base
    return _private_fallback()


def _private(path) -> bool:
    """Whether *path* is a folder of this user's that no one else can open
    (as the runtime folder is): a lock there can't be held or swapped by
    another user."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return (stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()
            and not st.st_mode & (stat.S_IRWXG | stat.S_IRWXO))


def _private_fallback() -> str:
    """/tmp/tuxtalk-<uid>, the same for every copy whatever its TMPDIR, made
    readable by this user only. Raises OSError when it's a link or not
    private. Lock files in it are never deleted (a waiter would lock a
    deleted file)."""
    path = os.path.join(FALLBACK_ROOT, f"tuxtalk-{os.getuid()}")
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    if os.path.islink(path) or not _private(path):
        raise OSError(f"{path} isn't a private folder of this user's own")
    return path


def open_lock(name: str):
    """The lock file *name* in runtime_dir(), open for claim(). Raises
    OSError when it can't be opened (no usable folder, no permission),
    which is not "already running".

    Opened without truncating, so the running copy's PID stays in it until
    this one holds the lock. Close-on-exec: an in-place restart (os.execv,
    the updater's Restart Now) would otherwise inherit the locked file, fail
    to lock it again and exit as "already running".
    """
    fd = os.open(os.path.join(runtime_dir(), name),
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        os.close(fd)
        raise OSError(f"{name} isn't a file of this user's own")
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
        fd = os.open(os.path.join(runtime_dir(), name), os.O_RDONLY | os.O_NOFOLLOW)
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


def claim_window(name, brought_forward, what):
    """For a program with one window (Preferences, Transcribe a File): (open
    it?, the lock *name* to hold while it's open). The lock is what keeps
    two quick launches from both finding no pid yet and both opening one:
    this one gives way once *brought_forward(wait_s)* reached the open window
    (waiting while it starts), and opens once one that is closing has let go.
    A lock that can't be used at all doesn't stop the window. *what* names
    it in the log."""
    lock = held = None
    try:
        lock = open_lock(name)
        held = not claim(lock)
    except OSError as e:     # no usable folder, or flock refused: open without it
        logger.warning(f"Could not take {what}'s lock: {e}")
    if brought_forward(FRONT_WAIT_S if held else 0):
        logger.info(f"{what} is already open; brought it to the front")
        return False, None
    if held:
        try:
            if not claim(lock, wait_s=CLOSING_WAIT_S):
                logger.warning(f"Another {what} window holds on and doesn't answer")
                return False, None
        except OSError as e:
            logger.warning(f"Could not wait for {what}'s lock: {e}")
    return True, lock
