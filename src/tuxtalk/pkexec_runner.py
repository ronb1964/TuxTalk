"""Running a command as root through pkexec, the one place TuxTalk waits
for the password prompt and what follows it.

The command is a fixed argv, never a shell string, so no shell ever parses
it. A root script goes to bash on stdin, never through a file: any program
running as this user could rewrite a file while the prompt is open, and it
would then run as root. Every run has a time limit, which includes the time
at the prompt. Nothing here logs: the callers know what is safe to say about
their command and its output.
"""
import subprocess
import time
from typing import NamedTuple

# Time limits, in seconds, counting the time at the password prompt. An
# install also downloads its packages.
SCRIPT_TIMEOUT_S = 300
INSTALL_TIMEOUT_S = 900

# Outcome.status
DONE = "done"            # ran as root and exited 0
DECLINED = "declined"    # the password prompt was cancelled or refused
FAILED = "failed"        # ran, and failed
TIMED_OUT = "timed out"  # stopped at the time limit; nothing ran as root
UNKNOWN = "unknown"      # still running as root at the limit, so waited for; output lost
MISSING = "missing"      # pkexec isn't installed


class Outcome(NamedTuple):
    """How a run went: *status* (one of the names above), the command's
    *stdout* when it ran, and for FAILED a *detail* (its stderr, or its
    stdout when it wrote nothing there), stripped."""
    status: str
    stdout: str = ""
    detail: str = ""


def was_declined(returncode, stderr) -> bool:
    """True when pkexec never ran the command because the password prompt was
    cancelled or the password wasn't accepted.

    pkexec exits 126 when the polkit agent reports the prompt as dismissed, but
    several agents report a cancel as a refusal instead: exit 127 with "Not
    authorized ... This incident has been reported." Both mean the user said no.
    """
    if returncode == 126:
        return True
    return returncode == 127 and "Not authorized" in (stderr or "")


def run(argv, timeout, script=None) -> Outcome:
    """Run *argv* (a list, without "pkexec") as root, with *script* on its
    stdin when given, for at most *timeout* seconds. Raises PermissionError
    only when pkexec itself can't be started."""
    if not isinstance(argv, list):
        raise TypeError("pkexec_runner.run takes an argv list, never a shell string")
    started = time.monotonic()
    try:
        result = subprocess.run(["pkexec", *argv], input=script, text=True, errors="replace",
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Outcome(TIMED_OUT)
    except PermissionError:
        if time.monotonic() - started < timeout:
            raise
        # Killing pkexec at the time limit fails once it runs the command as
        # root; subprocess then waits for the command to end.
        return Outcome(UNKNOWN)
    except FileNotFoundError:
        return Outcome(MISSING)
    if result.returncode == 0:
        return Outcome(DONE, result.stdout or "")
    if was_declined(result.returncode, result.stderr):
        return Outcome(DECLINED)
    return Outcome(FAILED, result.stdout or "",
                   (result.stderr or result.stdout or "").strip())
