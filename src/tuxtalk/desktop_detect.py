#!/usr/bin/env python3
"""
Desktop environment detection for TuxTalk
"""

import os
import subprocess
from typing import Optional

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


def tuxtalk_extension_active() -> bool:
    """Whether TuxTalk's GNOME extension is installed and enabled."""
    from .extension_helper import EXTENSION_UUID
    return is_extension_enabled(EXTENSION_UUID)


def menu_location() -> str:
    """Where this desktop shows TuxTalk's menu, for the welcome screen's and
    Setup Complete's tips: plain GNOME has no tray, so "the tray icon" sent
    people looking for nothing."""
    if tuxtalk_extension_active():
        return "its icon in the top bar"
    if tray_host_available():
        return "its tray icon"
    return "open TuxTalk again from your apps"


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


def get_gnome_shell_version() -> Optional[str]:
    """
    Get GNOME Shell version

    Returns:
        str: Version string (e.g., "48.4") or None if not GNOME
    """
    if not is_gnome():
        return None

    try:
        result = subprocess.run(
            ['gnome-shell', '--version'],
            capture_output=True,
            text=True, errors="replace",
            timeout=5
        )
        if result.returncode == 0:
            # Output is like "GNOME Shell 48.4"
            version = result.stdout.strip().split()[-1]
            return version
    except (subprocess.TimeoutExpired, FileNotFoundError, IndexError):
        pass

    return None


def is_wayland() -> bool:
    """Check if running on Wayland"""
    session_type = os.environ.get('XDG_SESSION_TYPE', '').lower()
    wayland_display = os.environ.get('WAYLAND_DISPLAY', '')

    return 'wayland' in session_type or bool(wayland_display)


def get_extension_dir() -> str:
    """Get GNOME extensions directory"""
    home = os.path.expanduser('~')
    return os.path.join(home, '.local', 'share', 'gnome-shell', 'extensions')


def is_extension_installed(uuid: str) -> bool:
    """
    Check if a GNOME extension is installed

    Args:
        uuid: Extension UUID (e.g., 'talktype@ronb1964.github.io')

    Returns:
        bool: True if extension is installed
    """
    ext_dir = get_extension_dir()
    ext_path = os.path.join(ext_dir, uuid)

    # Check if extension directory exists and has metadata.json
    metadata_path = os.path.join(ext_path, 'metadata.json')
    return os.path.isfile(metadata_path)


def is_extension_enabled(uuid: str) -> bool:
    """
    Check if a GNOME extension is enabled

    Args:
        uuid: Extension UUID

    Returns:
        bool: True if extension is enabled
    """
    try:
        result = subprocess.run(
            ['gnome-extensions', 'info', uuid],
            capture_output=True,
            text=True, errors="replace",
            timeout=5
        )
        if result.returncode == 0:
            # Look for "State: ENABLED" in output
            return 'State: ENABLED' in result.stdout or 'State: ACTIVE' in result.stdout
    except (subprocess.TimeoutExpired, OSError):
        pass        # no gnome-extensions here (or not runnable): ask the shell instead

    # Inside a Flatpak the gnome-extensions CLI isn't on PATH and the
    # org.gnome.shell gsettings schema isn't in the runtime, so both the CLI and
    # a gsettings read fail. Ask GNOME Shell directly over D-Bus instead —
    # without this the tray can't tell the extension is active and shows the GTK
    # icon alongside it (a duplicate tray menu).
    return _is_extension_enabled_via_dbus(uuid)


def _is_extension_enabled_via_dbus(uuid: str) -> bool:
    """Ask GNOME Shell whether an extension is enabled via its D-Bus interface.

    org.gnome.Shell.Extensions.GetExtensionInfo returns a dict whose 'state' is
    1.0 when ENABLED (GNOME's ExtensionState enum) plus, on newer shells, an
    explicit 'enabled' boolean. Returns False on any error (not GNOME, shell not
    running, name not reachable), which is the correct "don't hide the tray"
    default. Needs --talk-name=org.gnome.Shell in the Flatpak manifest.
    """
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        ret = bus.call_sync(
            "org.gnome.Shell", "/org/gnome/Shell",
            "org.gnome.Shell.Extensions", "GetExtensionInfo",
            GLib.Variant("(s)", (uuid,)),
            GLib.VariantType("(a{sv})"),
            Gio.DBusCallFlags.NONE, 2000, None)
        info = ret.unpack()[0]
        return bool(info.get("enabled")) or info.get("state") == 1.0
    except GLib.Error:      # not GNOME, or its shell isn't answering: off GNOME, always
        return False


def _read_enabled_extensions():
    return _read_extension_list("enabled-extensions")


def _write_enabled_extensions(uuids: list) -> bool:
    return _write_extension_list("enabled-extensions", uuids)


def _read_extension_list(key):
    """Return org.gnome.shell *key* (enabled-extensions or disabled-extensions)
    as a list of UUIDs, or None.

    gsettings prints a GVariant array — either an empty typed array `@as []`
    or a populated `['a@x', 'b@y']`. Both parse cleanly once the `@as` prefix
    is stripped, because GVariant string syntax matches Python literals.

    Returns None (NOT []) when the value can't be read or parsed. That distinction
    matters: the caller read-modify-writes this list, so treating a transient read
    failure as "empty" would overwrite the user's entire enabled-extensions list
    with only TuxTalk — silently disabling every other GNOME extension they have.
    None means "don't touch it".
    """
    try:
        result = subprocess.run(
            ['gsettings', 'get', 'org.gnome.shell', key],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        if raw.startswith('@as'):
            raw = raw[len('@as'):].strip()
        import ast
        value = ast.literal_eval(raw)
        return list(value) if isinstance(value, (list, tuple)) else None
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, SyntaxError):
        return None


def _write_extension_list(key, uuids: list) -> bool:
    """Overwrite org.gnome.shell *key* with *uuids*.

    repr() of a list of UUIDs is a valid GVariant array literal (single-quoted),
    and UUIDs never contain quotes, so no escaping is needed.
    """
    try:
        result = subprocess.run(
            ['gsettings', 'set', 'org.gnome.shell', key, repr(uuids)],
            capture_output=True, text=True, errors="replace", timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def forget_extension(uuid: str, keys=("disabled-extensions",)) -> None:
    """Take *uuid* out of org.gnome.shell's extension lists named by *keys*.

    `gnome-extensions disable` puts an extension on disabled-extensions,
    which outlasts an uninstall and would keep a later reinstall from
    starting on the next login. Each list is read first and left alone if
    the read fails, so the user's other extensions are never lost."""
    for key in keys:
        current = _read_extension_list(key)
        if current is not None and uuid in current:
            _write_extension_list(key, [u for u in current if u != uuid])


def enable_extension(uuid: str) -> bool:
    """
    Enable a GNOME extension so it activates on the next shell start.

    Two mechanisms, because neither alone is reliable on a fresh install:

      1. `gnome-extensions enable` activates it live — but ONLY if the running
         shell already knows the extension. On Wayland a just-installed
         extension is unknown to the running shell, so this fails until a
         restart, and on its own would leave a fresh install with no tray icon
         after the reboot.

      2. Writing the UUID directly into org.gnome.shell enabled-extensions
         persists regardless. The shell reads that key on its next start — the
         reboot onboarding already prescribes — and activates the extension.

    The gsettings write is what makes a first-run install work; the CLI call is
    the fast path that lights the icon up immediately when the shell can.
    """
    live_enabled = False
    try:
        result = subprocess.run(
            ['gnome-extensions', 'enable', uuid],
            capture_output=True, text=True, errors="replace", timeout=10,
        )
        live_enabled = result.returncode == 0
        if not live_enabled and result.stderr:
            # Expected on Wayland for a freshly-installed extension.
            logger.info(f"gnome-extensions enable deferred to gsettings: {result.stderr.strip()}")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.info(f"gnome-extensions enable unavailable, using gsettings: {e}")

    # Persist into the enabled list so it survives to the next shell start even
    # when the live enable above could not take effect. Only touch the setting if
    # we could actually READ it — a None means the read failed, and writing then
    # would clobber the user's other enabled extensions with just TuxTalk.
    enabled = _read_enabled_extensions()
    if enabled is not None and uuid not in enabled:
        enabled.append(uuid)
        if _write_enabled_extensions(enabled):
            logger.info(f"Added {uuid} to enabled-extensions (activates on next login)")

    # An earlier uninstall's "disable" may have left it on disabled-extensions.
    forget_extension(uuid)

    # Success if it is enabled now, or will be on the next shell start.
    final = _read_enabled_extensions()
    return live_enabled or (final is not None and uuid in final)
