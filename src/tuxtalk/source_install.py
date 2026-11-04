"""What a source install is made of, and how a release goes into it.

The README sets one up as a Python venv in TuxTalk's data folder
(`~/.local/share/TuxTalk/venv`, made with the system's python3 and its
site-packages so PyGObject and dbus-python come from the distro), holding
TuxTalk and its pinned dependencies, with a link to its `tuxtalk` command in
`~/.local/bin`. A release goes into that venv with pip, from the release's
source archive and the constraints file that release ships. TuxTalk shows the
command and never runs it: pip replaces files under a running TuxTalk, so the
user quits first.

A pip install outside a venv (`pip install --user`, or into /usr/local) is a
source install too, but its Python is the system's: sys.prefix is /usr, where
neither its `tuxtalk` nor a pip that may install there is. Only the user knows
how they installed it, so no update command is shown for it.
"""
import os
import shlex
import site
import sys
import sysconfig
from typing import Optional

VENV_NAME = "venv"
ARCHIVE_URL = "https://github.com/ronb1964/TuxTalk/archive/refs/tags/v{version}.tar.gz"
CONSTRAINTS_URL = ("https://raw.githubusercontent.com/ronb1964/TuxTalk/v{version}/"
                   "packaging/source-install-constraints.txt")


def in_venv() -> bool:
    """Whether TuxTalk runs from a venv rather than the system's own Python."""
    return sys.prefix != sys.base_prefix


def _scripts_folder() -> str:
    """Where pip put the scripts of this TuxTalk, installed outside a venv: the
    user scheme's folder (~/.local/bin) when its package is in the user's
    site-packages, else the prefix scheme's (/usr/local/bin on Fedora and
    Debian, whose Pythons send pip there)."""
    package = os.path.realpath(os.path.dirname(__file__))
    user_site = os.path.realpath(site.getusersitepackages())
    if package.startswith(user_site + os.sep):
        return sysconfig.get_path("scripts", sysconfig.get_preferred_scheme("user"))
    return sysconfig.get_path("scripts")


def command() -> Optional[str]:
    """The `tuxtalk` command of this source install: the running venv's, or
    outside a venv the one pip put beside TuxTalk's package; None when that
    one isn't there (TuxTalk run with python -m from where pip didn't put
    it), so nothing is pointed at a missing program."""
    folder = f"{sys.prefix}/bin" if in_venv() else _scripts_folder()
    script = os.path.join(folder, "tuxtalk")
    return script if os.path.isfile(script) else None


def shell_path(path: str, home: str) -> str:
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
    (by default the running one, when in_venv()); *home* is the one `~` stands for."""
    pip = shell_path(f"{prefix or sys.prefix}/bin/pip", home or os.path.expanduser("~"))
    return (f"{pip} install -c {CONSTRAINTS_URL.format(version=version)} "
            f"{ARCHIVE_URL.format(version=version)}")
