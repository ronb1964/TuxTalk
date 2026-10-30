"""Which desktop TuxTalk runs on: its name (KDE, GNOME, COSMIC...), whether
the session is X11, and whether it shows tray icons. Many modules ask, since
keys, typing and windows work differently on each.
"""

import os

from .logger import setup_logger

logger = setup_logger(__name__)


def config_home() -> str:
    """The user's config folder, where the desktops keep their settings.
    Not TuxTalk's own (config.py), which differs inside a Flatpak."""
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def get_desktop_environment() -> str:
    """
    Detect the current desktop environment

    Returns:
        str: 'gnome', 'kde', 'xfce', 'cinnamon', 'mate', 'cosmic' or 'unknown'
    """
    # Check environment variables
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
    session = os.environ.get('DESKTOP_SESSION', '').lower()

    if _gnome_without_its_shell(desktop, session):
        return 'unknown'

    # GNOME
    if 'gnome' in desktop or 'gnome' in session:
        return 'gnome'

    # KDE Plasma
    if 'kde' in desktop or 'plasma' in desktop or 'kde' in session:
        return 'kde'

    # XFCE
    if 'xfce' in desktop or 'xfce' in session:
        return 'xfce'

    # Cinnamon
    if 'cinnamon' in desktop or 'cinnamon' in session:
        return 'cinnamon'

    # MATE
    if 'mate' in desktop or 'mate' in session:
        return 'mate'

    # COSMIC (System76; Pop!_OS since 24.04)
    if 'cosmic' in desktop:
        return 'cosmic'

    return 'unknown'


def _gnome_without_its_shell(desktop: str, session: str) -> bool:
    """Budgie and GNOME Flashback name GNOME as well ("Budgie:GNOME") but run
    no GNOME Shell: TuxTalk's extension can't load there, and on X11 the
    hotkeys are TuxTalk's own grab to make (x11_hotkeys.should_claim)."""
    return (any(part in ('budgie', 'gnome-flashback') for part in desktop.split(':'))
            or session.startswith(('budgie', 'gnome-flashback')))


def flatpak_hotkey_hint() -> str:
    """One-line, desktop-aware instruction for changing the dictation keys on the
    Flatpak, where they're owned by the global-shortcuts portal rather than
    TuxTalk's own config. Preferences shows this instead of an editable combo so
    the user is sent to the place the keys actually live."""
    de = get_desktop_environment()
    if de == 'kde':
        return ("Open System Settings → Keyboard → Shortcuts, find TuxTalk under "
                "Applications, and set “Hold to talk” and “Toggle dictation”.")
    if de == 'gnome':
        return ("Open Settings → Keyboard → View and Customize Shortcuts, find "
                "TuxTalk, and set “Hold to talk” and “Toggle dictation”.")
    return ("Open your desktop's global keyboard shortcut settings, find TuxTalk, "
            "and set “Hold to talk” and “Toggle dictation”.")


def is_gnome() -> bool:
    """Check if running GNOME desktop"""
    return get_desktop_environment() == 'gnome'


def tray_host_available(bus=None) -> bool:
    """True when the desktop shows tray icons (a StatusNotifierWatcher runs).

    KDE, Cinnamon, XFCE and Ubuntu's GNOME have one. Plain GNOME (Fedora,
    Silverblue, Debian, Arch) has none unless the AppIndicator extension is
    added, so there TuxTalk's tray icon, and with it the whole menu, never
    appears. Our GNOME extension draws its own menu and doesn't count here.
    Unknown (no session bus) counts as available, so nothing nags by mistake.
    """
    try:
        import dbus
        if bus is None:
            # Private: a shared connection opened before the tray sets up its
            # GLib main loop couldn't export the tray's D-Bus service later.
            bus = dbus.SessionBus(private=True)
            try:
                return bool(bus.name_has_owner("org.kde.StatusNotifierWatcher"))
            finally:
                bus.close()
        return bool(bus.name_has_owner("org.kde.StatusNotifierWatcher"))
    except Exception as e:      # no session bus, or it failed: unknown
        logger.info(f"Could not ask the session bus for a system tray: {e}")
        return True


def is_x11_session(env):
    """Whether the session is X11: XDG_SESSION_TYPE when it is set; otherwise
    (startx sets none) an X DISPLAY with no Wayland one.

    The session type wins over WAYLAND_DISPLAY: a Wayland compositor nested in
    an X11 session (a KWin started for testing) sets WAYLAND_DISPLAY for
    its own windows, while the keyboard and the hotkeys stay the X server's.
    """
    session = (env.get("XDG_SESSION_TYPE") or "").lower()
    if session:
        return session == "x11"
    return bool(env.get("DISPLAY")) and not env.get("WAYLAND_DISPLAY")
