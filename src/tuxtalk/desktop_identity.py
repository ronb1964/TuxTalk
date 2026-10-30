"""Tell the desktop which launcher TuxTalk's windows belong to.

The desktop finds a window's icon (title bar, taskbar, Alt+Tab) by matching
the program name, which becomes the Wayland app_id or the X11 WM_CLASS, to a
launcher (.desktop file). Without it every TuxTalk window reports itself as
"python3", and KDE shows its generic "W" icon instead of TuxTalk's.

Each install type names its launcher differently, so the name follows the
install:

  AppImage   ~/.local/share/applications/tuxtalk.desktop  (hidden until setup ends)
  AUR        /usr/share/applications/tuxtalk.desktop
  deb / rpm  /usr/share/applications/io.github.ronb1964.TuxTalk.desktop
  Flatpak    io.github.ronb1964.TuxTalk
  dev        ~/.local/share/applications/tuxtalk-dev.desktop

Call apply() at the start of every process that opens windows (the tray,
Preferences and the dictation service). It works even after Gtk has been
imported, as long as it runs before the first window is shown.
"""
from gi.repository import GLib

from .config import APP_ID  # noqa: E402  (one app ID, defined in config)
from .logger import setup_logger

logger = setup_logger(__name__)

_BY_INSTALL_TYPE = {
    "appimage": "tuxtalk",
    "aur": "tuxtalk",
    "dev": "tuxtalk-dev",
}


# Every name a TuxTalk window can report as its class, lower-cased: this
# release's launcher names and the TalkType ones still running until a restart.
_OWN_CLASSES = frozenset(name.lower() for name in (
    *_BY_INSTALL_TYPE.values(), APP_ID,
    "talktype", "talktype-dev", "io.github.ronb1964.TalkType",
))


def is_own_window_class(wm_class) -> bool:
    """Whether *wm_class* is one of TuxTalk's own windows. They get no paste
    shortcut, and focused_window marks them "own", so an automatic Enter
    never goes into one."""
    return bool(wm_class) and wm_class.lower() in _OWN_CLASSES


# wl-copy without a data-control protocol sets the clipboard from a window of
# its own, which holds the focus for a moment and hands it back. Taken for the
# focused window, it made a paste into Konsole pick Ctrl+V and type ^V.
_CLIPBOARD_TOOL_CLASSES = frozenset({"io.github.bugaevc.wl-clipboard", "wl-clipboard"})


def is_clipboard_tool_class(wm_class) -> bool:
    """Whether *wm_class* is wl-copy's brief window, which never receives a
    dictation."""
    return wm_class in _CLIPBOARD_TOOL_CLASSES


def desktop_id(install_type: str) -> str:
    """The launcher name (without .desktop) for this kind of install."""
    return _BY_INSTALL_TYPE.get(install_type, APP_ID)


def apply() -> None:
    """Set the program name to this install's launcher name. Never raises:
    a missing icon is cosmetic, a crash at startup is not."""
    try:
        from .installation import get_install_type
        # Every package type has the same launcher name, so the slow query
        # of the package database (rpm or deb?) is skipped at startup.
        GLib.set_prgname(desktop_id(get_install_type(pkg_query=lambda: "package")))
    except Exception as e:
        logger.info(f"Could not name TuxTalk's windows after its launcher: {e}")
