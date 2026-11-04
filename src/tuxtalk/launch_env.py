"""The environment for programs TuxTalk starts for the user (an app opened by
voice, the browser for a release page).

TuxTalk runs with its own Python and libraries (the AppImage's AppRun exports
them), the service's GDK_BACKEND and a hidden GPU when it has no CUDA. A
program started with those inherited breaks or runs wrongly, so it gets the
environment the user's desktop would have given it.
"""
from collections.abc import Mapping

from .service_launcher import USERS_GDK_BACKEND

# Set by TuxTalk or its launchers, never by the user's desktop.
_TUXTALK_VARIABLES = (
    "PYTHONHOME", "PYTHONPATH", "GI_TYPELIB_PATH",
    "APPDIR", "APPIMAGE", "ARGV0", "OWD",
    "HF_HUB_DISABLE_XET", "DEV_MODE",
)
# cuda_preload.py sets CUDA_VISIBLE_DEVICES='' and this beside it, so that a
# value the user set themselves is told apart from TuxTalk's.
CUDA_HIDDEN_BY_TUXTALK = "TUXTALK_SET_CUDA_VISIBLE_DEVICES"


def _without_appimage(paths: str, appdir: str) -> str:
    """The entries of a colon-separated list that are not under the AppImage mount."""
    return ":".join(p for p in paths.split(":")
                    if p and p != appdir and not p.startswith(appdir + "/"))


def clean(environ: Mapping[str, str]) -> dict[str, str]:
    """A copy of `environ` for a program started on the user's behalf."""
    env = {k: v for k, v in environ.items()
           if k not in _TUXTALK_VARIABLES and not k.startswith("TUXTALK_")}
    appdir = environ.get("APPDIR", "").rstrip("/")
    if appdir:
        for name in ("LD_LIBRARY_PATH", "PATH"):
            if name in env:
                env[name] = _without_appimage(env[name], appdir)
    if not env.get("LD_LIBRARY_PATH"):
        env.pop("LD_LIBRARY_PATH", None)
    if environ.get(CUDA_HIDDEN_BY_TUXTALK):
        env.pop("CUDA_VISIBLE_DEVICES", None)
    if USERS_GDK_BACKEND in environ:
        env.pop("GDK_BACKEND", None)
        if environ[USERS_GDK_BACKEND]:
            env["GDK_BACKEND"] = environ[USERS_GDK_BACKEND]
    return env
