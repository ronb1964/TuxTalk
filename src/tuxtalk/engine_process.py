"""
Keeping TuxTalk's helper engines on a short leash.

TuxTalk runs its optional background engines as separate programs: the AI
cleanup engine (llama-server, ai_cleanup.py) and the Vulkan graphics engine
(whisper_vulkan.py, and parakeet_gpu.py for Parakeet). Each can hold a gigabyte
or more of memory, so none may outlive the dictation service that started it.
"""
import codecs
import collections
import os
import shutil
import signal
import sys
import threading
import urllib.request

from . import engine_launcher
from .logger import setup_logger

logger = setup_logger(__name__)


def parent_death_command(argv, env=None):
    """The command line and environment that start the engine *argv*, with
    the environment *env* (default: the service's), through engine_launcher:
    the kernel then kills it when the thread that starts it exits, even if
    the dictation service is killed with SIGKILL, so no engine sits orphaned
    on a gigabyte of memory.

    SIGKILL, not SIGTERM: llama-server sometimes stalls in its graceful
    shutdown and then ignores SIGTERM, and no engine holds state worth saving.

    The signal fires when the thread that called Popen exits, not the
    process, so spawn from a thread that lives as long as the engine (see
    parakeet_gpu's _launcher)."""
    env = dict(os.environ if env is None else env)
    own_libs = os.environ.get("LD_LIBRARY_PATH")
    if env.get("LD_LIBRARY_PATH") != own_libs:
        # The launcher is Python: it starts with the service's libraries
        # and hands the engine its own.
        env[engine_launcher.ENGINE_LIBRARY_PATH] = env.pop("LD_LIBRARY_PATH", "")
        if own_libs is not None:
            env["LD_LIBRARY_PATH"] = own_libs
    command = [sys.executable, engine_launcher.__file__, str(os.getpid()), "--", *argv]
    return command, env


def launch_failure(returncode, output=None):
    """", because ..." for an engine that ended while starting: what the
    launcher's exit code says, else the exit code and the last lines it
    printed (*output*, an OutputTail), which also explain a launcher that
    Python couldn't run at all (a broken PYTHONPATH)."""
    why = {engine_launcher.NO_SIGNAL: "the parent-death signal couldn't be set",
           engine_launcher.NO_ENGINE: "its program couldn't be started"}.get(returncode)
    said = output.text() if output is not None else ""
    if why:
        return f", because {why}{': ' + said if said else ''}"
    if output is None:
        return ""                       # the caller shows the exit code itself
    return f" ({_how_it_ended(returncode)}{': ' + said if said else ''})"


def _how_it_ended(returncode):
    """"exit code 3", "killed by SIGSEGV", or "killed by signal 40" for a
    real-time signal, which has no name of its own."""
    if returncode >= 0:
        return f"exit code {returncode}"
    try:
        return f"killed by {signal.Signals(-returncode).name}"
    except ValueError:
        return f"killed by signal {-returncode}"


class OutputTail:
    """The last lines a server writes to *stream* (its stderr, a pipe), read
    on a thread of their own, to explain one that ends while starting. Not a
    file, which would grow all session (a server logs every request), and
    always read, so the pipe never fills and stalls the server. Only shown
    for a server that never got a request, so no dictated text is in it."""

    LINE_BYTES = 500

    def __init__(self, stream, lines=5):
        self._lines = collections.deque(maxlen=lines)
        self._reader = threading.Thread(target=self._read, args=(stream,), daemon=True,
                                        name="engine-output")
        self._reader.start()

    def _read(self, stream):
        # At most LINE_BYTES a read: a server writing one endless line can't
        # grow the service's memory, its pieces just push each other out. The
        # decoder carries a character split between two reads over to the next.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        with stream:
            for line in iter(lambda: stream.readline(self.LINE_BYTES), b""):
                text = decoder.decode(line).strip()
                if text:            # a blank line mustn't push out a real one
                    self._lines.append(text)

    def text(self, wait=0.5):
        """The lines kept, joined; waits up to *wait* seconds for the last
        ones of a server that has just ended."""
        self._reader.join(wait)
        return " | ".join(list(self._lines))


# Every engine listens on 127.0.0.1, yet urllib would send even that through
# a proxy set in http_proxy, handing it dictated text, audio and the AI
# engine's key. Python's urllib makes no exception for this computer.
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def open_local(request, timeout):
    """urlopen for an engine on this computer: never through a proxy."""
    return _LOCAL.open(request, timeout=timeout)


def remove_other_versions(parent, keep):
    """Delete every engine version folder under *parent* except *keep*. Each
    version unpacks into its own folder, so without this an update leaves
    the old one behind for good. Never raises."""
    try:
        names = os.listdir(parent)
    except OSError:
        return
    for name in names:
        path = os.path.join(parent, name)
        if name != keep and os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
            if os.path.exists(path):
                logger.warning(f"Couldn't remove an older engine version: {path}")
            else:
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
        # Pid 1 never started an engine: it is init, or in the Flatpak the
        # sandbox's bwrap, whose command line names TuxTalk.
        if ppid == os.getpid() or (ppid != 1 and is_talktype(ppid)):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
            logger.warning(f"Killed an orphaned {label} left by an earlier run (pid {pid})")
        except OSError:
            pass
    return killed
