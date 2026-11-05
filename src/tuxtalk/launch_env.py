"""The environment for programs TuxTalk starts for the user (an app opened by
voice, the browser for a release page).

TuxTalk runs with its own Python and libraries (the AppImage's AppRun exports
them), the service's GDK_BACKEND and a hidden GPU when it has no CUDA. A
program started with those inherited breaks or runs wrongly, so it gets the
environment the user's desktop would have given it.
"""
import os
import sys
from collections.abc import Mapping

from .service_launcher import USERS_GDK_BACKEND

# Set by TuxTalk or its launchers, never by the user's desktop.
_TUXTALK_VARIABLES = (
    "PYTHONHOME", "PYTHONPATH", "GI_TYPELIB_PATH",
    "APPDIR", "APPIMAGE", "ARGV0", "OWD",
    "HF_HUB_DISABLE_XET", "DEV_MODE",
    "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT",
)
# cuda_preload.py sets CUDA_VISIBLE_DEVICES='' and this beside it, so that a
# value the user set themselves is told apart from TuxTalk's.
CUDA_HIDDEN_BY_TUXTALK = "TUXTALK_SET_CUDA_VISIBLE_DEVICES"


def _running_venv() -> str:
    """The virtual environment TuxTalk runs from (a source install or dev), or ""."""
    return sys.prefix if sys.prefix != sys.base_prefix else ""


def _apprun_folder(environ: Mapping[str, str]) -> str:
    """The folder AppRun runs TuxTalk from, or "". AppRun sets PYTHONHOME to
    its usr folder, in the AppImage's mount and in /opt/talktype alike; the
    .deb and .rpm run it without the AppImage runtime, so APPDIR is unset
    there. A PYTHONHOME of the user's own has no AppRun beside it."""
    home = environ.get("PYTHONHOME", "").rstrip("/")
    folder = os.path.dirname(home)
    return folder if home and os.path.isfile(os.path.join(folder, "AppRun")) else ""


def _without_under(paths: str, root: str) -> str:
    """The entries of a colon-separated list that are not inside the folder `root`."""
    return ":".join(p for p in paths.split(":")
                    if p and p != root and not p.startswith(root + "/"))


def clean(environ: Mapping[str, str], venv: str | None = None) -> dict[str, str]:
    """A copy of `environ` for a program started on the user's behalf.

    Entries of LD_LIBRARY_PATH and PATH inside the AppImage's mount (APPDIR),
    AppRun's folder (the mount again, or the packages' /opt/talktype) and
    the virtual environment TuxTalk runs from are dropped, the user's others
    kept in order. `venv` is that environment's folder; by
    default the one this process runs in, "" for none. run-dev.sh puts the
    venv's ctranslate2 libraries on LD_LIBRARY_PATH without setting VIRTUAL_ENV.
    """
    venv = _running_venv() if venv is None else venv
    env = {k: v for k, v in environ.items()
           if k not in _TUXTALK_VARIABLES and not k.startswith("TUXTALK_")}
    ours = [root.rstrip("/") for root in (environ.get("APPDIR", ""), _apprun_folder(environ),
                                          venv) if root]
    for name in ("LD_LIBRARY_PATH", "PATH"):
        for root in ours:
            if name in env:
                env[name] = _without_under(env[name], root)
    if not env.get("LD_LIBRARY_PATH"):
        env.pop("LD_LIBRARY_PATH", None)
    if environ.get(CUDA_HIDDEN_BY_TUXTALK):
        env.pop("CUDA_VISIBLE_DEVICES", None)
    if USERS_GDK_BACKEND in environ:
        env.pop("GDK_BACKEND", None)
        if environ[USERS_GDK_BACKEND]:
            env["GDK_BACKEND"] = environ[USERS_GDK_BACKEND]
    return env
