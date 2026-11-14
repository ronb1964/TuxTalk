"""The desktop's accessibility switch, the one screen readers turn on
(org.a11y.Status IsEnabled on the session's org.a11y.Bus): with it on, KDE
apps and Firefox, and Chromium and Electron apps started after it, tell the
accessibility bus the text around the cursor, so a dictation can fit the
sentence it goes into (sentence_fit).

TuxTalk turns it on only after the user says yes (startup_notices), keeps it
on at each start until they turn it off in Preferences, and turns it off
only if TuxTalk was the one that turned it on: never under a screen reader.
The desktop saves it (on GNOME's settings, which KDE uses too), so it
outlasts a logout; where a desktop forgets it, keep_on turns it on again.

The user's answer is kept in a file of its own, not config.toml: a settings
backup moved to another computer mustn't turn it on there unasked, and a
change from Preferences takes effect at once, outside Apply."""
import json
import os

from gi.repository import Gio, GLib

from . import config
from .logger import setup_logger

logger = setup_logger(__name__)

CALL_TIMEOUT_MS = 1000
# Beside config.toml (uninstall.CONFIG_NAMES).
FILE_NAME = "a11y-switch.json"


def _call(method: str, args: GLib.Variant | None) -> tuple:
    """Properties.*method* on the accessibility bus launcher's /org/a11y/bus."""
    session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return session.call_sync("org.a11y.Bus", "/org/a11y/bus", "org.freedesktop.DBus.Properties",
                             method, args, None, Gio.DBusCallFlags.NO_AUTO_START,
                             CALL_TIMEOUT_MS, None).unpack()


def is_on() -> bool | None:
    """Whether the switch is on; None when there's no accessibility bus."""
    return _status("IsEnabled")


def _status(name: str) -> bool | None:
    """org.a11y.Status's *name*; None when there's no accessibility bus."""
    try:
        return bool(_call("Get", GLib.Variant("(ss)", ("org.a11y.Status", name)))[0])
    except GLib.Error as e:
        logger.info(f"Could not read the desktop's accessibility {name}: {e.message}")
        return None


def _set(on: bool) -> bool:
    """Turn the switch *on* or off: whether that worked."""
    try:
        _call("Set", GLib.Variant("(ssv)", ("org.a11y.Status", "IsEnabled",
                                            GLib.Variant("b", on))))
    except GLib.Error as e:
        logger.info(f"Could not turn the desktop's accessibility {'on' if on else 'off'}: "
                    f"{e.message}")
        return False
    logger.info(f"Turned the desktop's accessibility {'on' if on else 'off'}")
    return True


def state_path() -> str:
    """Where TuxTalk remembers the user's answer, beside config.toml."""
    return os.path.join(os.path.dirname(config.CONFIG_PATH), FILE_NAME)


def _load() -> dict:
    """{"answer": "" | "on" | "not_now", "ours": TuxTalk turned it on};
    not asked yet when there's no file or it can't be read."""
    try:
        with open(state_path(), encoding="utf-8") as f:
            saved = json.load(f)
        return {"answer": str(saved.get("answer", "")), "ours": bool(saved.get("ours"))}
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError) as e:
        logger.info(f"Could not read {state_path()}, so it counts as not asked: {e!r}")
    return {"answer": "", "ours": False}


def _save(state: dict) -> None:
    config.write_text_atomic(state_path(), json.dumps(state), keep_backup=False)


def answer() -> str:
    """"" not asked yet, "on" the user said yes, "not_now" they said no."""
    return _load()["answer"]


def allow() -> bool:
    """The user said yes: the switch on, and remembered, with whether
    TuxTalk turned it on. Whether it's on now. A yes that can't be saved
    leaves the switch as it was: nothing would remember it was TuxTalk's."""
    was_on = is_on()
    if was_on is None:
        return False
    ours = not was_on and _set(True)
    try:
        _save({"answer": "on", "ours": ours or _load()["ours"]})
    except OSError as e:
        logger.warning(f"Could not save {state_path()}, so accessibility stays as it was: {e}")
        if ours:
            _set(False)
        return False
    return was_on or ours


def decline() -> bool:
    """The user said no, or turned it off in Preferences: remembered, and
    the switch off again if TuxTalk turned it on, unless a screen reader
    is running now. False when it was TuxTalk's to turn off and didn't go."""
    state = _load()
    still_ours = state["ours"] and not _turn_off()
    try:
        _save({"answer": "not_now", "ours": still_ours})
    except OSError as e:
        logger.warning(f"Could not save {state_path()}; the question may come again: {e}")
    return not still_ours


def _turn_off() -> bool:
    """The switch off, unless a screen reader started since TuxTalk turned
    it on: it's that reader's now. Whether it's no longer TuxTalk's."""
    if _status("ScreenReaderEnabled"):
        logger.info("A screen reader is running: the desktop's accessibility stays on")
        return True
    return _set(False)


def turned_on_by_tuxtalk() -> bool:
    """Whether TuxTalk turned the switch on, so it may turn it off again."""
    return _load()["ours"]


def give_back() -> bool:
    """Uninstalling: the switch off again if TuxTalk turned it on. False
    when it was TuxTalk's and wouldn't go off."""
    return not turned_on_by_tuxtalk() or _turn_off()


def keep_on() -> None:
    """At start: on again after a yes, where something turned it off (a
    desktop that doesn't keep it). It stays on until the user turns it off
    in TuxTalk's Preferences."""
    state = _load()
    if state["answer"] == "on" and is_on() is False and _set(True):
        _save({"answer": "on", "ours": True})
