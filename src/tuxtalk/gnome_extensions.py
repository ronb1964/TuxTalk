"""GNOME Shell's extensions: where they are installed, whether one is
enabled, and turning one on or forgetting it in GNOME's settings
(org.gnome.shell enabled-extensions and disabled-extensions).

extension_helper installs TuxTalk's own extension through these. Every
write to those lists reads them first and leaves them alone when the read
fails, so the user's other extensions are never switched off.
"""
import ast
import os
import subprocess

from .logger import setup_logger

logger = setup_logger(__name__)


def get_extension_dir() -> str:
    """The folder this user's GNOME Shell extensions are installed in."""
    return os.path.join(os.path.expanduser('~'), '.local', 'share', 'gnome-shell', 'extensions')


def extension_folder(uuid: str) -> str:
    """Where the extension *uuid* is installed, or would be."""
    return os.path.join(get_extension_dir(), uuid)


def is_extension_installed(uuid: str) -> bool:
    """Whether the extension *uuid* is installed: GNOME Shell loads a folder
    only with its metadata.json."""
    return os.path.isfile(os.path.join(extension_folder(uuid), 'metadata.json'))


def is_extension_enabled(uuid: str) -> bool:
    """Whether GNOME Shell has the extension *uuid* enabled."""
    try:
        result = subprocess.run(
            ['gnome-extensions', 'info', uuid],
            capture_output=True,
            text=True, errors="replace",
            timeout=5
        )
        if result.returncode == 0:
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
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Could not change GNOME's {key}: {e}")
        return False
    if result.returncode != 0:
        # A key an administrator locked is refused here.
        logger.warning(f"GNOME refused to change {key}: {result.stderr.strip()}")
    return result.returncode == 0


def forget_extension(uuid: str, keys=("disabled-extensions",)) -> bool:
    """Take *uuid* out of org.gnome.shell's extension lists named by *keys*.
    False when it is still on one: GNOME refused the change.

    `gnome-extensions disable` puts an extension on disabled-extensions,
    which outlasts an uninstall and would keep a later reinstall from
    starting on the next login. Each list is read first and left alone if
    the read fails, so the user's other extensions are never lost."""
    cleared = True
    for key in keys:
        current = _read_extension_list(key)
        if current is not None and uuid in current:
            cleared &= _write_extension_list(key, [u for u in current if u != uuid])
    return cleared


def enable_extension(uuid: str) -> bool:
    """Enable the extension *uuid* now when the shell can, and for the next
    shell start in any case. True when either worked and disabled-extensions
    doesn't hold it back.

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

    # An earlier uninstall's "disable" may have left it on disabled-extensions,
    # where GNOME won't start it, whatever enabled-extensions says.
    if not forget_extension(uuid):
        return False

    # Success if it is enabled now, or will be on the next shell start.
    final = _read_enabled_extensions()
    return live_enabled or (final is not None and uuid in final)
