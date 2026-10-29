"""
Starts one engine so that it dies with the dictation service.

    python <this file> <service pid> -- <engine> [args...]

engine_process.parent_death_command builds that line. This process asks the
kernel to SIGKILL it when the thread that started it exits, checks that the
service is still its parent, then becomes the engine: the request survives
the exec. Doing it here, in a process of its own, keeps Python out of the
moment between fork and exec in the service, which has threads and could
deadlock there (preexec_fn), and it works with no other program installed.

It runs by its path, not as tuxtalk.engine_launcher, and imports only the
standard library: the package's own start-up moves old folders and opens the
log, and an engine must start even where that fails. (Run by its path, this
folder comes first on sys.path; no module here is named like a standard one.)

The environment is the engine's, except that LD_LIBRARY_PATH is still the
service's (Python itself may need it, as in the AppImage); the engine's
arrives in ENGINE_LIBRARY_PATH and is put in place just before the exec.
"""
import ctypes
import os
import signal
import sys

PR_SET_PDEATHSIG = 1
ENGINE_LIBRARY_PATH = "TUXTALK_ENGINE_LIBRARY_PATH"
# Exit codes, so a start that ends at once says why in the service's log.
PARENT_GONE = 70
NO_SIGNAL = 71
NO_ENGINE = 72


def main(argv):
    """Set the signal, check the parent, become the engine; or exit."""
    service_pid, separator, engine = int(argv[0]), argv[1], argv[2:]
    if separator != "--" or not engine:
        sys.exit("usage: engine_launcher <service pid> -- <engine> [args...]")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL) != 0:
        print(f"couldn't set the parent-death signal: {os.strerror(ctypes.get_errno())}",
              file=sys.stderr)
        return NO_SIGNAL
    # The service may have died before the signal was set: then it never
    # fires, and this process belongs to someone else already.
    if os.getppid() != service_pid:
        return PARENT_GONE
    libs = os.environ.pop(ENGINE_LIBRARY_PATH, None)
    if libs is not None:
        os.environ["LD_LIBRARY_PATH"] = libs
    # Python ignores these; an engine expects them as any program gets them
    # (subprocess's restore_signals did this when the service started it).
    for ignored in (signal.SIGPIPE, signal.SIGXFSZ):
        signal.signal(ignored, signal.SIG_DFL)
    try:
        os.execv(engine[0], engine)
    except OSError as e:
        print(f"couldn't start {engine[0]}: {e}", file=sys.stderr)
    return NO_ENGINE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
