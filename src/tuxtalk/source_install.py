"""What a source install is made of, and how a release goes into it.

The README sets one up as a Python venv in TuxTalk's data folder
(`~/.local/share/TuxTalk/venv`, made with the system's python3 and its
site-packages so PyGObject and dbus-python come from the distro), holding
TuxTalk and its pinned dependencies, with a link to its `tuxtalk` command in
`~/.local/bin`. A release goes into that venv with pip, from the release's
source archive and the constraints file that release ships. TuxTalk shows the
command and never runs it: pip replaces files under a running TuxTalk, so the
user quits first.
"""
import os
import shlex
import sys
from typing import Optional

VENV_NAME = "venv"
ARCHIVE_URL = "https://github.com/ronb1964/TuxTalk/archive/refs/tags/v{version}.tar.gz"
CONSTRAINTS_URL = ("https://raw.githubusercontent.com/ronb1964/TuxTalk/v{version}/"
                   "packaging/source-install-constraints.txt")


def command(prefix: Optional[str] = None) -> str:
    """The `tuxtalk` command of the venv at *prefix* (the running one by default)."""
    return f"{prefix or sys.prefix}/bin/tuxtalk"


def _shown(path: str, home: str) -> str:
    """*path* as a shell takes it: `~/...` when it is under *home* and nothing in
    it needs quoting, else quoted whole (a `~` inside quotes doesn't expand)."""
    if path.startswith(home + "/"):
        under_home = path[len(home):]
        if shlex.quote(under_home) == under_home:
            return "~" + under_home
    return shlex.quote(path)


def update_command(version: str, prefix: Optional[str] = None,
                   home: Optional[str] = None) -> str:
    """The pip command that installs release *version* into the venv at *prefix*
    (the running one by default); *home* is the one `~` stands for."""
    pip = _shown(f"{prefix or sys.prefix}/bin/pip", home or os.path.expanduser("~"))
    return (f"{pip} install -c {CONSTRAINTS_URL.format(version=version)} "
            f"{ARCHIVE_URL.format(version=version)}")
