"""Stand-in commands for tests that run TuxTalk's shell scripts. They go on a
PATH of their own, so no real rm, systemctl or udevadm ever runs."""


def stub(folder, name, body):
    """A command *name* in *folder* that runs the sh *body*."""
    path = folder / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def recorders(folder, names, log, status=0):
    """Commands *names* in *folder* that each add their name and arguments
    to *log* and exit with *status*: one for all, or {name: status}."""
    for name in names:
        code = status.get(name, 0) if isinstance(status, dict) else status
        stub(folder, name, f'echo "{name} $*" >> {log}\nexit {code}')
