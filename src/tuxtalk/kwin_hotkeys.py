"""Stop the dictation hotkeys reaching the focused app on KDE.

The service reads the keyboard with evdev, underneath the desktop, so it sees F8
at the same moment KWin does and cannot stop KWin passing the press on. The
keyboard grab goes on only after the press, which is already on its way to the
focused window. Terminals turn F8 into an escape sequence ending in "~", and the
shell prints the "~". The toggle key (F9) is never grabbed at all, so it would
leak on press and release alike.

KWin hands a key to an app only if it isn't a global shortcut. So while the
service runs, a small KWin script registers the hotkeys as shortcuts that do
nothing. KWin swallows the key; evdev still sees the raw press and release, so
recording is unaffected.

Checked against Plasma 6.7.4 on Wayland, with a window logging every key it
received:
  * shortcut registered from a script loaded over D-Bus: the window got neither
    the press nor the release, and the next key arrived normally;
  * script unloaded: the key reached the window again. A shortcut left behind
    in kglobalshortcutsrc with no script behind it does not swallow anything.
  * Re-registering a name with a different key keeps the OLD key: kglobalaccel
    prefers what it saved over the new default. Hence the key is part of each
    shortcut's name, and release() unregisters them all, so a changed hotkey is
    always registered fresh.

The service owns this, not the tray: the auto-timeout makes the service exit,
and with dictation off F8 should be an ordinary key again. Every stop path goes
through service_launcher.stop_dictation_service(), which releases; the tray
releases too if the service dies without that (crash, SIGKILL).

Nothing here may be fatal. Failing to claim a key costs the "~" fix; it must
never cost dictation.
"""

import os
import time

from . import desktop_detect
from .kwin_focus import (KWIN_SCRIPTING_INTERFACE, KWIN_SCRIPTING_PATH,
                         KWIN_SERVICE, load_and_start)
from .logger import setup_logger

logger = setup_logger(__name__)

SCRIPT_PLUGIN_NAME = "tuxtalk-hotkeys"
# The script's name before the 0.15.0 rename; a TalkType run may have left it.
OLD_SCRIPT_PLUGIN_NAMES = ("talktype-hotkeys",)

# Every shortcut we register starts with this, which is how release() finds
# them, including ones left by an older run with a different hotkey.
SHORTCUT_PREFIX = "tuxtalk-"
# release() also removes these: shortcuts a TalkType (pre-0.15.0) run left.
# Kept, KDE would hold F8 twice, once under each name.
OLD_SHORTCUT_PREFIXES = ("talktype-",)

KGLOBALACCEL_SERVICE = "org.kde.kglobalaccel"
KGLOBALACCEL_PATH = "/kglobalaccel"
KGLOBALACCEL_INTERFACE = "org.kde.KGlobalAccel"
KWIN_COMPONENT_PATH = "/component/kwin"
KGLOBALACCEL_COMPONENT_INTERFACE = "org.kde.kglobalaccel.Component"

# KWin answers in milliseconds; this only bounds a wedged compositor.
_DBUS_TIMEOUT_S = 3.0

# TuxTalk's modifier names -> Qt's.
_QT_MODIFIERS = {"CTRL": "Ctrl", "SHIFT": "Shift", "ALT": "Alt", "SUPER": "Meta"}
_FKEYS = {f"F{i}" for i in range(1, 13)}
# The other dictation keys a desktop can hold back: TuxTalk's name (upper
# case) -> Qt's name and code. Right Ctrl and Right Alt aren't among them: a
# modifier on its own can't be a shortcut, and it types nothing into the app.
_QT_KEYS = {"SCROLL LOCK": ("ScrollLock", 0x01000026)}
_QT_KEY_CODES = dict(_QT_KEYS.values())
# Qt's names for those keys where the X keysym (GNOME, COSMIC) differs.
QT_KEYSYMS = {"ScrollLock": "Scroll_Lock"}


def qt_key_sequence(name):
    """TuxTalk's hotkey name ("F8", "Ctrl+Alt+V") as a Qt key sequence, or None.

    A bare key is only claimed if it's an F-key or Scroll Lock. Claiming a bare
    letter would stop the user typing that letter anywhere while the service runs.
    """
    if not name or not name.strip():
        return None
    parts = [p.strip().upper() for p in name.split("+")]
    key = parts[-1]
    bare = key in _FKEYS or key in _QT_KEYS
    if not (bare or (len(key) == 1 and "A" <= key <= "Z")):
        return None
    mods = []
    for part in parts[:-1]:
        if part not in _QT_MODIFIERS:
            return None
        mods.append(_QT_MODIFIERS[part])
    if not mods and not bare:
        return None
    return "+".join(mods + [_QT_KEYS[key][0] if key in _QT_KEYS else key])


# Qt's codes for a modifier and a key, as kglobalaccel reports shortcuts.
_QT_MODIFIER_CODES = {"Shift": 0x02000000, "Ctrl": 0x04000000, "Alt": 0x08000000,
                      "Meta": 0x10000000}
_QT_F1 = 0x01000030


def qt_key_code(sequence):
    """A Qt key sequence from qt_key_sequence ("Shift+F8") as the number
    kglobalaccel reports it in, or None."""
    sequence = qt_key_sequence(sequence)
    if sequence is None:
        return None
    *mods, key = sequence.split("+")
    if key in _FKEYS:
        code = _QT_F1 + int(key[1:]) - 1
    else:
        code = _QT_KEY_CODES.get(key) or ord(key)
    for mod in mods:
        code |= _QT_MODIFIER_CODES[mod]
    return code


def wanted_shortcuts(cfg):
    """[(shortcut name, description, Qt key)] for the hotkeys in *cfg*."""
    roles = (
        ("hold", "hold to talk", getattr(cfg, "hotkey", "")),
        ("toggle", "tap to start and stop", getattr(cfg, "toggle_hotkey", "")),
        ("voice-commands", "voice commands", getattr(cfg, "voice_commands_hotkey", "")),
        ("paste-last", "paste last dictation", getattr(cfg, "paste_last_hotkey", "")),
    )
    if getattr(cfg, "voice_editing", False):
        # Shift + a dictation key starts a voice edit (app._held_shift).
        roles += tuple((f"edit-{role}", "voice edit", f"Shift+{name}")
                       for role, _what, name in roles[:2] if name)
    out, seen = [], set()
    for role, what, name in roles:
        key = qt_key_sequence(name)
        if key and key not in seen:   # one key can only be registered once
            seen.add(key)
            out.append((f"{SHORTCUT_PREFIX}{role}-{key}",
                        f"TuxTalk: {what} ({key}), managed by TuxTalk", key))
    return out


def build_script(shortcuts) -> str:
    """The KWin script: register each hotkey with a callback that does nothing.

    The keys come from qt_key_sequence (F-keys, letters, modifier names) and
    the names/descriptions are built from them, so none can contain a quote.
    """
    lines = ["// Generated by TuxTalk. Keeps the dictation hotkeys out of the",
             "// focused app while the service runs. Rewritten at every start."]
    for name, text, key in shortcuts:
        lines.append(f'registerShortcut("{name}", "{text}", "{key}", function() {{}});')
    return "\n".join(lines) + "\n"


def script_path() -> str:
    """Where the generated KWin script is written."""
    from .config import get_data_dir

    return os.path.join(get_data_dir(), "kwin", "tuxtalk-hotkeys.js")


def should_claim() -> bool:
    """KDE outside a Flatpak. The Flatpak gets its keys from the GlobalShortcuts
    portal, where the desktop already owns them, and has no KWin to talk to."""
    if os.environ.get("FLATPAK_ID"):
        return False
    return desktop_detect.get_desktop_environment() == "kde"


def session_bus():
    """A .call(method, *args) shim over KWin scripting and kglobalaccel.

    Imported lazily so this module stays importable (and testable) without
    dbus-python.
    """
    import dbus

    bus = dbus.SessionBus()

    class _Bus:
        def call(self, method, *args):
            t = {"timeout": _DBUS_TIMEOUT_S}
            if method in ("loadScript", "unloadScript", "start"):
                obj = bus.get_object(KWIN_SERVICE, KWIN_SCRIPTING_PATH)
                # loadScript is overloaded; see kwin_focus.session_bus.
                sig = {"loadScript": "ss", "unloadScript": "s", "start": ""}[method]
                return getattr(obj, method)(*args, dbus_interface=KWIN_SCRIPTING_INTERFACE,
                                            signature=sig, **t)
            if method == "unregister":
                obj = bus.get_object(KGLOBALACCEL_SERVICE, KGLOBALACCEL_PATH)
                return obj.unregister(*args, dbus_interface=KGLOBALACCEL_INTERFACE, **t)
            if method == "allComponents":
                obj = bus.get_object(KGLOBALACCEL_SERVICE, KGLOBALACCEL_PATH)
                return obj.allComponents(dbus_interface=KGLOBALACCEL_INTERFACE, **t)
            if method in ("componentShortcuts", "componentIsActive", "componentCleanUp"):
                obj = bus.get_object(KGLOBALACCEL_SERVICE, args[0])
                name = {"componentShortcuts": "allShortcutInfos",
                        "componentIsActive": "isActive",
                        "componentCleanUp": "cleanUp"}[method]
                return getattr(obj, name)(dbus_interface=KGLOBALACCEL_COMPONENT_INTERFACE, **t)
            if method in ("shortcutNames", "allShortcutInfos"):
                obj = bus.get_object(KGLOBALACCEL_SERVICE, KWIN_COMPONENT_PATH)
                return getattr(obj, method)(dbus_interface=KGLOBALACCEL_COMPONENT_INTERFACE, **t)
            raise ValueError(method)

    return _Bus()


def _unregister_ours(bus) -> None:
    """Remove every tuxtalk-* (and leftover talktype-*) shortcut from KWin's
    component."""
    try:
        names = [str(n) for n in bus.call("shortcutNames")]
    except Exception as e:
        logger.debug(f"Could not list KWin shortcuts: {e}")
        return
    for name in names:
        if name.startswith((SHORTCUT_PREFIX,) + OLD_SHORTCUT_PREFIXES):
            try:
                bus.call("unregister", "kwin", name)
            except Exception as e:
                logger.debug(f"Could not unregister KWin shortcut {name}: {e}")


# Pauses before reading KWin's shortcuts again, about a second in all. KWin
# registers the script's shortcuts a moment after it starts, so right after a
# quick re-claim (a service restart) the first read can come back empty for
# keys KWin is about to hold. Only keys still missing after these are refused.
_READBACK_WAITS = (0.1, 0.2, 0.3, 0.4)


def result_path() -> str:
    """Where the last claim's outcome is saved for Preferences, next to the
    settings (so the dev and AppImage builds keep their own)."""
    from .config import CONFIG_PATH
    return os.path.join(os.path.dirname(CONFIG_PATH), "kde-hotkeys.json")


def _save_result(refused, apps=None) -> None:
    """Record which keys KDE refused, and when, for Preferences to report
    after a hotkey change. *apps*: {key: app} for the keys that open an app."""
    import json
    try:
        with open(result_path(), "w") as f:
            json.dump({"checked": time.time(), "refused": refused, "apps": apps or {}}, f)
    except OSError as e:
        logger.debug(f"Could not save the KDE hotkey result: {e}")


def refused_since(since):
    """Keys KDE refused at the last claim, if that was after *since* (a
    time.time()); None when there's no such result yet."""
    import json
    try:
        with open(result_path()) as f:
            data = json.load(f)
        if float(data["checked"]) < since:
            return None
        return [str(k) for k in data["refused"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def app_shortcuts_since(since):
    """{key: app} for the keys that open an app in KDE, from the last claim
    if that was after *since*; {} when unknown."""
    import json
    try:
        with open(result_path()) as f:
            data = json.load(f)
        if float(data["checked"]) < since:
            return {}
        return {str(k): str(v) for k, v in data.get("apps", {}).items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


# kglobalaccel components that are TuxTalk's own: TalkType's portal
# component (io.github.ronb1964.TalkType) can linger with F3/F4 in it.
_OUR_COMPONENTS = ("tuxtalk", "talktype", "io.github.ronb1964.")


def forget_our_components(bus=None) -> bool:
    """Uninstalling: drop TuxTalk's own kglobalaccel components, so System
    Settings -> Shortcuts lists nothing of it. cleanUp() removes a
    component's shortcuts once nothing runs that registered them, which is
    why this comes after TuxTalk has stopped. Never raises; False when one
    may be left."""
    if not should_claim():
        return True
    try:
        bus = bus or session_bus()
        paths = [str(p) for p in bus.call("allComponents") or []]
    except Exception as e:
        logger.warning(f"Could not list KDE's shortcut components: {e}")
        return False
    all_gone = True
    for path in paths:
        if path == KWIN_COMPONENT_PATH:
            continue
        try:
            infos = bus.call("componentShortcuts", path) or []
            if any(str(info[2]).lower().startswith(_OUR_COMPONENTS) for info in infos):
                bus.call("componentCleanUp", path)
                logger.info(f"Removed TuxTalk's KDE shortcut component {path}")
        except Exception as e:
            logger.warning(f"Could not clean up KDE shortcut component {path}: {e}")
            all_gone = False
    return all_gone


def _desktop_file_exists(name) -> bool:
    """Whether an app's .desktop file (*name*, "org.kde.foo.desktop") is
    installed, so its launch shortcut still works."""
    dirs = [os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")]
    dirs += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    return any(os.path.isfile(os.path.join(d, "applications", name)) for d in dirs if d)


def _app_shortcut_clashes(bus, shortcuts) -> dict:
    """{key: app} for our keys another kglobalaccel component holds.

    kglobalaccel turns a KWin-script shortcut away only when another one is
    on the same key in the same way; an app's launch shortcut (KDE's Emoji
    Selector on F6) lives in its own component and isn't weighed against
    ours, so both fire. A launch shortcut counts while its app is installed
    (leftovers of removed apps stay listed), any other component while it
    runs.
    """
    wanted = {qt_key_code(key): key for _name, _text, key in shortcuts}
    wanted.pop(None, None)
    clashes = {}
    try:
        paths = [str(p) for p in bus.call("allComponents") or []]
    except Exception as e:
        logger.debug(f"Could not list KDE's shortcut components: {e}")
        return clashes
    for path in paths:
        if path == KWIN_COMPONENT_PATH:
            continue
        try:
            infos = bus.call("componentShortcuts", path) or []
        except Exception as e:
            logger.debug(f"Could not read KDE shortcuts of {path}: {e}")
            continue
        for info in infos:
            unique, friendly = str(info[2]), str(info[3]) or str(info[1])
            if unique.lower().startswith(_OUR_COMPONENTS):
                break
            hits = [wanted[int(c)] for c in info[6] if int(c) in wanted]
            if not hits:
                continue
            try:
                live = (_desktop_file_exists(unique) if unique.endswith(".desktop")
                        else bool(bus.call("componentIsActive", path)))
            except Exception:
                live = False
            if live:
                for key in hits:
                    clashes.setdefault(key, friendly)
    return clashes


def _log_refused(bus, shortcuts) -> list:
    """Say which hotkeys KDE would not give us (already another shortcut's),
    and return them."""
    waits = list(_READBACK_WAITS)
    while True:
        try:
            infos = bus.call("allShortcutInfos")
            # (name, text, component, component name, context, context name,
            #  keys, default keys), as read back from Plasma 6.7.4.
            got = {str(i[0]): list(i[6]) for i in infos}
        except Exception as e:
            logger.debug(f"Could not read back KWin shortcuts: {e}")
            return []
        missing = [s for s in shortcuts if not got.get(s[0])]
        if not missing or not waits:
            break
        time.sleep(waits.pop(0))
    for _name, _text, key in missing:
        logger.warning(
            f"KDE already uses {key} for another shortcut, so it will still "
            f"reach the focused app as well as starting dictation")
    return [key for _name, _text, key in missing]


def claim(cfg, bus=None) -> bool:
    """Register *cfg*'s hotkeys with KWin. True if the script is running.

    Safe to call again after a hotkey change: it releases first, so the new
    keys are registered fresh rather than kglobalaccel keeping the old ones.
    """
    if not should_claim():
        return False
    shortcuts = wanted_shortcuts(cfg)
    try:
        bus = bus or session_bus()
    except Exception as e:
        logger.debug(f"No session bus for KWin hotkeys: {e}")
        return False
    release(bus)
    if not shortcuts:
        return False
    try:
        path = script_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(build_script(shortcuts))
        load_and_start(bus, path, SCRIPT_PLUGIN_NAME)
    except Exception as e:
        logger.warning(f"Could not register the hotkeys with KWin: {e}")
        return False
    refused = _log_refused(bus, shortcuts)
    apps = _app_shortcut_clashes(bus, shortcuts)
    for key, app in apps.items():
        logger.warning(f"KDE opens {app} with {key}, so pressing {key} will do that "
                       f"as well as dictating")
    _save_result(refused + [k for k in apps if k not in refused], apps)
    logger.info("KWin now holds the hotkeys back from the focused app: "
                + ", ".join(k for _n, _t, k in shortcuts))
    return True


def release(bus=None) -> None:
    """Unload the script and forget our shortcuts. Never raises: quit path."""
    if not should_claim():
        return
    try:
        bus = bus or session_bus()
    except Exception as e:
        logger.debug(f"No session bus for KWin hotkeys: {e}")
        return
    for plugin in (SCRIPT_PLUGIN_NAME,) + OLD_SCRIPT_PLUGIN_NAMES:
        try:
            bus.call("unloadScript", plugin)
        except Exception as e:
            logger.debug(f"Could not unload the KWin hotkey script {plugin}: {e}")
    _unregister_ours(bus)
