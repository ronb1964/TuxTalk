"""Open apps by voice (TuxTalk Labs): "open Firefox" with Firefox already open
brings its window forward instead of starting another.

Each desktop does it its own way:
  KDE    a one-shot KWin script (kwin_activate);
  GNOME  TuxTalk's extension, through GNOME Shell's own bus connection: it
         serves ActivateApp there, and GNOME Shell already knows which
         windows belong to which menu entry;
  X11    the window manager's list of windows and a _NET_ACTIVE_WINDOW
         request (x11_activate), on Xfce, MATE, Cinnamon, i3 and the rest.
Elsewhere (COSMIC, Sway, Hyprland, niri) there's no way TuxTalk knows of, and
the app is started again; most then show their open window or a new one.

The dictation service asks GNOME Shell directly rather than through the
tray: the tray adds nothing to the answer, and the call works with no tray
running.

False always means "start it": no window, a desktop that can't, an error,
or no answer in time. It may wait up to the timeout, so it's called from the
dictation worker, never GTK's thread.
"""
import logging
import os

from . import desktop_detect, kwin_activate, x11_activate
from .app_finder import AppEntry

log = logging.getLogger(__name__)

GNOME_SHELL = "org.gnome.Shell"
EXTENSION_PATH = "/io/github/ronb1964/TuxTalk/Extension"
EXTENSION_INTERFACE = "io.github.ronb1964.TuxTalk.Extension"


def desktop_file(entry: AppEntry) -> str:
    """The menu entry's id without ".desktop": what KWin calls the window's
    desktop file ("org.kde.konsole")."""
    return entry.app_id.removesuffix(".desktop")


def window_names(entry: AppEntry, exact: bool = False) -> frozenset:
    """The window classes the app's windows may have, without case.

    Its menu entry's id ("org.kde.konsole": a Wayland app's id is that), and
    the class the entry names (StartupWMClass: "crx_..." for a Chrome web
    app). Without one, its program's name, X11's usual class ("konsole"), but
    only when the id agrees with it: a Steam game's entry runs "steam" and a
    web app's runs "google-chrome", and their windows are the launcher's own,
    not the game or web app. Interpreters ("python3") never agree either.

    *exact*: none. Another copy of the app is installed (or the user named
    one), and copies share their class: the distro's Firefox and Flathub's
    are both "firefox". Only the menu entry a window names tells them apart.
    """
    if exact:
        return frozenset()
    stem = desktop_file(entry).casefold()
    program = entry.program.casefold()
    if entry.wm_class:
        names = {stem, entry.wm_class.casefold()}
    elif program and (stem == program or stem.endswith("." + program)):
        names = {stem, program}
    else:
        names = {stem}
    return frozenset(n for n in names if n)


def call_extension(method: str, signature: str, args: tuple, timeout: float):
    """Call *method* of TuxTalk's GNOME extension, inside GNOME Shell, and
    return its answer. Raises a D-Bus error with no extension to answer, or
    one from before *method* (UnknownMethod)."""
    import dbus
    bus = dbus.SessionBus()
    return bus.call_blocking(GNOME_SHELL, EXTENSION_PATH, EXTENSION_INTERFACE, method,
                             signature, args, timeout=timeout)


def extension_running(timeout: float) -> bool:
    """Whether GNOME Shell serves TuxTalk's extension, of any version. A
    call to a method the extension lacks fails the same way with an
    extension from before that method as with none at all (UnknownMethod);
    the object's own description tells them apart. Raises a D-Bus error
    when GNOME Shell can't be asked."""
    import dbus
    bus = dbus.SessionBus()
    xml = bus.call_blocking(GNOME_SHELL, EXTENSION_PATH, "org.freedesktop.DBus.Introspectable",
                            "Introspect", "", (), timeout=timeout)
    return f'"{EXTENSION_INTERFACE}"' in str(xml)


def bring_forward(entry: AppEntry, timeout: float, exact: bool):
    """This desktop's answer (True or False), or None where nothing can be
    asked. Raises the desktop's error: focus_running takes any as "start it".
    GNOME Shell decides by itself which entry a window belongs to (a
    Flatpak's windows by their sandbox's id), so *exact* changes nothing there."""
    desktop = desktop_detect.get_desktop_environment()
    names = window_names(entry, exact)
    if desktop == "kde":
        return kwin_activate.activate(desktop_file(entry), names, timeout)
    if desktop == "gnome":
        return bool(call_extension("ActivateApp", "s", (entry.app_id,), timeout))
    if desktop_detect.is_x11_session(os.environ):
        return x11_activate.activate(desktop_file(entry), names)
    return None


def focus_running(entry: AppEntry, timeout: float = 1.0, exact: bool = False) -> bool:
    """Bring forward a window *entry*'s app already has. True only when one
    was brought forward; False means the caller starts the app. *exact*:
    only a window naming *entry*'s menu entry counts (window_names)."""
    try:
        found = bring_forward(entry, timeout, exact)
    except Exception as e:      # a desktop's many ways to fail all mean "start it"
        log.info("Couldn't bring %s forward, starting it instead: %s", entry.app_id, e)
        return False
    if found is None:
        log.info("This desktop can't bring a window forward; starting %s", entry.app_id)
    elif found:
        log.info("Brought %s forward: it was already open", entry.app_id)
    return bool(found)
