"""The desktop's own accessibility zoom, turned on for the mouse grid's small
cells and put back exactly as it was.

KDE: KWin's Zoom effect, stepped by its global shortcuts view_zoom_in and
view_zoom_out, each multiplying the zoom by kwinrc's ZoomFactor. KWin has no
readable zoom level, so it is put back with as many out-steps as went in,
which returns any zoom the user had of their own. GNOME and Cinnamon: the
magnifier's settings (GNOME Shell dropped its D-Bus API); only
screen-magnifier-enabled and mag-factor are ever written, and only for a
full-screen magnifier, not a lens.

Before changing anything, what puts it back is written to
zoom-to-restore.json in TuxTalk's data folder and removed once it's put
back, so a service that stopped while zoomed puts it back at its next start
(restore_left_over): a user left at 3x who doesn't know the desktop's own
zoom keys is the worst outcome here.

parking_spot is the arithmetic: where the pointer waits so the desktop
shows a chosen part centred on its monitor, by how each desktop's view
follows the pointer (KWin's zoom.cpp, GNOME and Cinnamon's magnifier.js).
"""
import configparser
import json
import logging
import math
import os
import threading
from typing import NamedTuple

from gi.repository import Gio

from . import config, desktop_detect, kwin_desktop
from .mouse_grid_model import Rect

log = logging.getLogger(__name__)

ZOOM = 3.0
RESTORE_FILE = "zoom-to-restore.json"
# Each zoom step is a D-Bus call; a KWin that hangs must not hold up a
# restore from GTK's thread for long.
_DBUS_TIMEOUT_S = 1.0

_KWIN_STEP_DEFAULT = 1.2
# kwinrc [Effect-zoom] MouseTracking: 0 proportional (the default),
# 1 centered, 2 push, 3 disabled, 4 centered strict.
_KWIN_TRACKING = {0: "proportional", 1: "centered", 4: "centered"}

_ENABLED = "screen-magnifier-enabled"
_FACTOR = "mag-factor"
_TRACKING = "mouse-tracking"
_POSITION = "screen-position"
_LENS = "lens-mode"
# Cinnamon's magnifier is a fork of GNOME's, with the same keys.
_GSETTINGS_ROOTS = {"gnome": ("org.gnome.desktop.a11y", "GNOME magnifier"),
                    "cinnamon": ("org.cinnamon.desktop.a11y", "Cinnamon magnifier")}


class View(NamedTuple):
    """How the zoomed desktop shows: the *factor* it magnifies by, how the
    view follows the pointer (*tracking*: "proportional", "centered" or
    "other"), and whether the view keeps a margin from the canvas's edges
    (*padded*, GNOME and Cinnamon)."""

    factor: float
    tracking: str
    padded: bool


class Magnifier:
    """What the mouse grid drives, KWinZoom or GSettingsMagnifier: zoom in
    once, and put back. Before zooming it puts back whatever an earlier
    zoom still owes, its own or one left on disk, and refuses while that
    can't be done: zooming over it would take the half put back desktop
    for the user's own."""

    name: str       # for the log: "KDE zoom", "GNOME magnifier", ...
    view: View

    def __init__(self):
        self._lock = threading.Lock()
        self._zoomed = False

    def zoom_in(self) -> bool:
        """Zoom in to ZOOM; True when zoomed, also when it already was.
        False, logged, when it couldn't: then nothing is left changed,
        unless even undoing failed, and restore() (always safe to call),
        the next zoom_in or the next start puts that back."""
        with self._lock:
            if self._zoomed:
                return True
            self._put_back()
            if self._owes():
                log.warning(f"Desktop zoom: not zooming, the {self.name} isn't put back yet")
                return False
            if not _nothing_left_on_disk():
                return False
            self._zoomed = self._zoom()
            return self._zoomed

    def restore(self) -> None:
        """Put the zoom back as it was before zoom_in. Safe from any thread,
        and to call again: a second call does nothing. Never raises."""
        with self._lock:
            self._zoomed = False
            self._put_back()

    def _zoom(self) -> bool:
        """Save what puts it back, then zoom; on a failure, undo what was done."""
        raise NotImplementedError

    def _put_back(self) -> None:
        """Put back what's still owed, if anything; keep it owed on a failure."""
        raise NotImplementedError

    def _owes(self) -> bool:
        raise NotImplementedError


def _nothing_left_on_disk() -> bool:
    """Whether no restore file is left from an earlier zoom (another
    magnifier's put-back that failed, or one the start couldn't do), after
    trying once more to put it back."""
    if os.path.exists(_restore_path()):
        restore_left_over()
    if os.path.exists(_restore_path()):
        log.warning("Desktop zoom: not zooming, a zoom left on earlier isn't put back yet")
        return False
    return True


def find() -> Magnifier | None:
    """This desktop's zoom, or None where there's none TuxTalk can drive:
    KDE with its Zoom effect off or KWin not answering, a GNOME or Cinnamon
    magnifier set to a lens or part of the screen, and every other desktop."""
    desktop = desktop_detect.get_desktop_environment()
    if desktop == "kde":
        return _kwin_zoom()
    if desktop in _GSETTINGS_ROOTS:
        return _gsettings_magnifier(desktop)
    return None


# KDE


class KWinZoom(Magnifier):
    """KWin's Zoom effect, stepped in and back out by its own shortcuts.

    view.factor is the zoom reached from 1x. KWin can't say its zoom
    level, so a user already zoomed in gets the part aimed less exactly
    (their zoom times this); the put-back, counted in steps, is exact."""

    name = "KDE zoom"

    def __init__(self, step: float, tracking: str):
        super().__init__()
        self._steps = max(1, round(math.log(ZOOM) / math.log(step)))
        self.view = View(step ** self._steps, tracking, False)
        self._owed: int | None = None     # out-steps owed while zoomed

    def _zoom(self) -> bool:
        if not _save_restore({"desktop": "kde", "steps": self._steps}):
            return False
        self._owed = self._steps - _kwin_steps("view_zoom_in", self._steps)
        if self._owed == self._steps:
            return True
        self._put_back()        # the steps that did go in
        return False

    def _put_back(self) -> None:
        if self._owed is None:
            return
        left = _kwin_steps("view_zoom_out", self._owed)
        if left:
            # Exactly what's still owed, for a retry or the next start: one
            # out-step too many would take away a zoom the user had.
            self._owed = left
            _save_restore({"desktop": "kde", "steps": left})
            return
        self._owed = None
        _remove_restore()

    def _owes(self) -> bool:
        return self._owed is not None


def _kwin_zoom() -> KWinZoom | None:
    try:
        loaded = kwin_desktop.effect_loaded("zoom", _DBUS_TIMEOUT_S)
    except Exception as e:      # dbus-python's errors when KWin doesn't answer
        log.warning(f"Desktop zoom: couldn't ask KWin about its Zoom effect: {e}")
        return None
    if not loaded:
        log.info("Desktop zoom: KDE's Zoom effect is off")
        return None
    step, tracking = _kwin_zoom_settings()
    if not 1.0 < step < math.inf:
        log.info(f"Desktop zoom: KDE's zoom steps by {step}x, which TuxTalk can't use")
        return None
    return KWinZoom(step, tracking)


def _kwin_zoom_settings() -> tuple[float, str]:
    """kwinrc's ZoomFactor and how the view follows the pointer, KWin's
    defaults where kwinrc says nothing or can't be read."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(os.path.join(desktop_detect.config_home(), "kwinrc"), encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError) as e:
        log.info(f"Desktop zoom: couldn't read kwinrc, using KWin's defaults: {e}")
    section = parser["Effect-zoom"] if parser.has_section("Effect-zoom") else {}
    try:
        step = float(section.get("ZoomFactor", _KWIN_STEP_DEFAULT))
    except ValueError:
        step = _KWIN_STEP_DEFAULT
    try:
        tracking = _KWIN_TRACKING.get(int(section.get("MouseTracking", 0)), "other")
    except ValueError:
        tracking = "proportional"
    return step, tracking


def _kwin_steps(shortcut: str, steps: int) -> int:
    """Invoke KWin's *shortcut* *steps* times; how many weren't done (0 when
    all were). A failure is logged and stops the rest. A best guess on a
    timeout: a busy KWin may still take that step after the call gave up."""
    for done in range(steps):
        try:
            kwin_desktop.invoke_shortcut(shortcut, _DBUS_TIMEOUT_S)
        except Exception as e:      # dbus-python's errors: kglobalaccel gone or hung
            log.warning(f"Desktop zoom: KDE's {shortcut} failed after {done} of {steps}: {e}")
            return steps - done
    return 0


# GNOME and Cinnamon


class GSettingsMagnifier(Magnifier):
    """GNOME's or Cinnamon's full-screen magnifier, turned on at ZOOM and
    put back to the user's own on/off and factor."""

    def __init__(self, desktop: str, tracking: str):
        super().__init__()
        self._desktop = desktop
        self._root, self.name = _GSETTINGS_ROOTS[desktop]
        self.view = View(ZOOM, tracking, True)
        self._before: tuple[bool, float] | None = None    # owed while zoomed

    def _zoom(self) -> bool:
        before = (_settings(self._root, "applications").get_boolean(_ENABLED),
                  _settings(self._root, "magnifier").get_double(_FACTOR))
        if not _save_restore({"desktop": self._desktop, "enabled": before[0],
                              "mag_factor": before[1]}):
            return False
        self._before = before
        if _set_magnifier(self._root, True, ZOOM):
            return True
        log.warning(f"Desktop zoom: the {self.name}'s settings can't be changed")
        self._put_back()
        return False

    def _put_back(self) -> None:
        if self._before is None:
            return
        if not _set_magnifier(self._root, *self._before):
            log.warning(f"Desktop zoom: couldn't put the {self.name} back")
            return
        self._before = None
        _remove_restore()

    def _owes(self) -> bool:
        return self._before is not None


def _gsettings_magnifier(desktop: str) -> GSettingsMagnifier | None:
    root, name = _GSETTINGS_ROOTS[desktop]
    if not (_has_keys(f"{root}.applications", (_ENABLED,))
            and _has_keys(f"{root}.magnifier", (_FACTOR, _TRACKING, _POSITION, _LENS))):
        log.info(f"Desktop zoom: no {name} here")
        return None
    settings = _settings(root, "magnifier")
    if settings.get_boolean(_LENS) or settings.get_string(_POSITION) != "full-screen":
        log.info(f"Desktop zoom: the {name} is a lens or covers part of the screen; not used")
        return None
    tracking = settings.get_string(_TRACKING)
    return GSettingsMagnifier(desktop, tracking if tracking in ("proportional", "centered")
                              else "other")


def _has_keys(schema_id: str, keys: tuple[str, ...]) -> bool:
    """Whether *schema_id* is installed with every one of *keys*: GSettings
    ends the process on a schema or key that isn't there."""
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(schema_id, True) if source is not None else None
    return schema is not None and all(schema.has_key(key) for key in keys)


def _settings(root: str, schema: str):
    # A new object for each use: Gio.Settings belongs to the thread that
    # made it, and restore() runs on whichever thread closes the grid.
    return Gio.Settings.new(f"{root}.{schema}")


def _set_magnifier(root: str, enabled: bool, factor: float) -> bool:
    """Write the magnifier's on/off and factor; False when a key refused
    (locked down), every write tried all the same. Turning it on, the
    factor goes first, so it never shows at the old factor; turning it off,
    it goes off first, so the old factor never shows magnified."""
    switch = _settings(root, "applications")
    magnifier = _settings(root, "magnifier")
    if enabled:
        done = [_write_factor(magnifier, factor), _write_enabled(switch, enabled)]
    else:
        done = [_write_enabled(switch, enabled), _write_factor(magnifier, factor)]
    Gio.Settings.sync()
    return all(done)


# A key that already holds its value isn't written: a locked key that never
# changed has nothing to put back, and isn't a failure.


def _write_factor(magnifier, factor: float) -> bool:
    if magnifier.get_double(_FACTOR) == factor:
        return True
    return magnifier.set_double(_FACTOR, factor)


def _write_enabled(switch, enabled: bool) -> bool:
    if switch.get_boolean(_ENABLED) == enabled:
        return True
    return switch.set_boolean(_ENABLED, enabled)


# What to put back, kept on disk while zoomed


def _restore_path() -> str:
    return os.path.join(config.get_data_dir(), RESTORE_FILE)


def _save_restore(data: dict) -> bool:
    try:
        config.write_text_atomic(_restore_path(), json.dumps(data), keep_backup=False)
        return True
    except OSError as e:
        log.warning(f"Desktop zoom: couldn't save how to put the zoom back: {e}")
        return False


def _remove_restore() -> None:
    try:
        os.remove(_restore_path())
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning(f"Desktop zoom: couldn't remove {_restore_path()}: {e}")


def restore_left_over() -> None:
    """Put back a zoom a service that stopped while zoomed left on, from the
    restore file. A file that makes no sense is deleted; one that can't be
    read or acted on now is kept for later. Never raises."""
    try:
        with open(_restore_path(), "rb") as f:
            text = f.read()
    except FileNotFoundError:
        return
    except OSError as e:
        log.warning(f"Desktop zoom: couldn't read the restore file, kept for later: {e}")
        return
    try:
        put_back = _left_over_put_back(json.loads(text))
    except ValueError as e:
        log.warning(f"Desktop zoom: deleted a restore file that made no sense: {e}")
        _remove_restore()
        return
    if put_back is None:
        log.info("Desktop zoom: a KDE zoom was left on; it's put back at a start on KDE")
        return
    if put_back():
        _remove_restore()
        log.info("Desktop zoom: put back a zoom TuxTalk left on")


def _left_over_put_back(data):
    """What puts back the zoom *data* (the restore file) describes, as a
    function answering whether it did; None for KDE's zoom off KDE, where
    KWin isn't there to take it. Raises ValueError when *data* isn't a
    restore file."""
    desktop = data.get("desktop") if isinstance(data, dict) else None
    if not isinstance(desktop, str):
        raise ValueError(f"desktop {desktop!r}")
    if desktop == "kde":
        steps = data.get("steps")
        if type(steps) is not int or steps < 1:
            raise ValueError(f"steps {steps!r}")
        if desktop_detect.get_desktop_environment() != "kde":
            return None
        return lambda: _kwin_steps("view_zoom_out", steps) == 0
    if desktop in _GSETTINGS_ROOTS:
        enabled, factor = data.get("enabled"), data.get("mag_factor")
        if type(enabled) is not bool or type(factor) not in (int, float):
            raise ValueError(f"enabled {enabled!r}, mag_factor {factor!r}")
        return lambda: _gsettings_put_back(desktop, enabled, float(factor))
    raise ValueError(f"desktop {desktop!r}")


def _gsettings_put_back(desktop: str, enabled: bool, factor: float) -> bool:
    root, name = _GSETTINGS_ROOTS[desktop]
    if not (_has_keys(f"{root}.applications", (_ENABLED,))
            and _has_keys(f"{root}.magnifier", (_FACTOR,))):
        log.warning(f"Desktop zoom: a zoom was left on, but there's no {name} here")
        return False
    if not _set_magnifier(root, enabled, factor):
        log.warning(f"Desktop zoom: couldn't put the {name} back")
        return False
    return True


# Where the pointer waits


def parking_spot(part: Rect, monitor: Rect, canvas: Rect, view: View) -> tuple[float, float]:
    """Where the pointer goes so the zoomed desktop shows *part* centred on
    *monitor*, *canvas* being all monitors' bounding box (both compositors
    magnify that as one). Kept on *monitor*, so the pointer never leaves it.

    Proportional tracking shows, per axis, the point
    roi(c) = c * (1 - k) + k * W / 2 at the monitor's centre (c the
    pointer and W the canvas, from its origin); k = 1 / z on KDE, and less
    by 2 * pad / W on GNOME and Cinnamon, which keep the view
    pad = min(W, H) / 5 / z from the edges. The roi wanted puts the part's
    centre m at that monitor's centre s: m - (s - W / 2) / z. Centered and
    the other modes keep the pointer in view: the part's centre."""
    x, y = part.center
    if view.tracking == "proportional":
        pad = min(canvas.width, canvas.height) / 5 / view.factor if view.padded else 0.0
        mid_x, mid_y = monitor.center
        x = _proportional(x, mid_x, canvas.x, canvas.width, view.factor, pad)
        y = _proportional(y, mid_y, canvas.y, canvas.height, view.factor, pad)
    return (_clamp(x, monitor.x, monitor.width), _clamp(y, monitor.y, monitor.height))


def _proportional(middle: float, screen_middle: float, origin: float, size: float,
                  zoom: float, pad: float) -> float:
    """One axis of parking_spot's proportional case, solved for the pointer."""
    k = 1 / zoom - 2 * pad / size
    wanted = (middle - origin) - (screen_middle - origin - size / 2) / zoom
    return origin + (wanted - k * size / 2) / (1 - k)


def _clamp(value: float, start: float, length: float) -> float:
    return min(max(value, start), start + length - 1)
