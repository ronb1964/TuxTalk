"""
Keeping TuxTalk's helper engines on a short leash.

TuxTalk runs its optional background engines as separate programs: the AI
cleanup engine (llama-server, ai_cleanup.py) and the Vulkan graphics engine
(whisper_vulkan.py, and parakeet_gpu.py for Parakeet). Each can hold a gigabyte
or more of memory, so none may outlive the dictation service that started it.
"""
import functools
import os
import shutil
import signal
import subprocess

from .logger import setup_logger

logger = setup_logger(__name__)


@functools.lru_cache(maxsize=None)
def _setpriv():
    """util-linux's setpriv, when it can set the parent-death signal
    (--pdeathsig, util-linux 2.33 from 2018), else None. Asked once: it runs
    itself through the option, which an older one refuses."""
    path = shutil.which("setpriv")
    if path:
        try:
            subprocess.run([path, "--pdeathsig", "KILL", path, "--version"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            return path
        except (OSError, subprocess.SubprocessError) as e:
            logger.info(f"setpriv can't set the parent-death signal here: {e}")
    logger.info("No setpriv with --pdeathsig: an engine left behind by a crash "
                "is cleaned up at the next start instead")
    return None


def with_parent_death(argv):
    """*argv*, started so that the kernel kills the engine when the thread
    that starts it exits, even if the dictation service is killed with
    SIGKILL: no engine may sit orphaned on a gigabyte of memory.

    setpriv sets the signal and then becomes the engine. Setting it from
    Python between fork and exec (preexec_fn) can deadlock in a process with
    threads, as the dictation service is, and makes subprocess copy the
    whole process instead of spawning. Without setpriv, kill_orphans at the
    next start is the backstop.

    SIGKILL, not SIGTERM: llama-server sometimes stalls in its graceful
    shutdown and then ignores SIGTERM, and no engine holds state worth saving.

    The signal fires when the thread that called Popen exits, not the
    process, so spawn from a thread that lives as long as the engine (see
    parakeet_gpu's _launcher)."""
    setpriv = _setpriv()
    return [setpriv, "--pdeathsig", "KILL", "--", *argv] if setpriv else list(argv)


def remove_other_versions(parent, keep):
    """Delete every engine version folder under *parent* except *keep*. Each
    version unpacks into its own folder, so without this an update leaves
    the old one behind for good. Never raises."""
    import shutil
    try:
        names = os.listdir(parent)
    except OSError:
        return
    for name in names:
        path = os.path.join(parent, name)
        if name != keep and os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
            logger.info(f"Removed an older engine version: {path}")


def processes_of(binary):
    """(pid, parent pid) of every running process started from *binary*."""
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                argv0 = f.read().split(b"\0", 1)[0].decode(errors="replace")
            if argv0 != binary:
                continue
            with open(f"/proc/{entry}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
            found.append((int(entry), ppid))
        except (OSError, ValueError, IndexError):
            continue
    return found


def is_talktype_process(pid) -> bool:
    """True when *pid* is our app, under its current name (TuxTalk) or the
    old one (TalkType: an older copy, or the dev folder ~/Projects/TalkType)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().lower()
        return b"tuxtalk" in cmdline or b"talktype" in cmdline
    except OSError:
        return False


def kill_orphans(processes, is_talktype, label) -> int:
    """Kill the engines in *processes* whose parent is no longer TuxTalk.
    A self-healing backstop for the parent-death signal. Returns how many."""
    killed = 0
    for pid, ppid in processes:
        if ppid == os.getpid() or is_talktype(ppid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
            logger.warning(f"Killed an orphaned {label} left by an earlier run (pid {pid})")
        except OSError:
            pass
    return killed
