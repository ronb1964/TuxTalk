"""Recording indicator for GNOME Shell, with an X11 fallback.

Mutter scales XWayland windows from their X11 resolution, blurring the
indicator on fractional monitors, and places them where it likes. The
TuxTalk extension draws it in the Shell at the monitor's own scale: the
service sends Show with the appearance settings, then Update with the
audio-driven shapes about 30 times a second. Without the extension, or an
older one, each dictation falls back to the X11 window.
"""

import threading
import time
from dataclasses import asdict, dataclass, replace

from gi.repository import Gio, GLib

from . import indicator_styles
from .desktop_detect import is_gnome
from .indicator_styles import LevelGain
from .logger import setup_logger
from .recording_indicator import (
    BASE_HEIGHT,
    BASE_WIDTH,
    RecordingIndicator,
    _scale_for_size,
    fixed_position,
    follow_position,
)
from .ui_style import desktop_accent_rgb
from .window_geometry import (
    Rect,
    active_window_rect,
    main_monitor_rect,
    monitor_rect_at,
    to_screen_rect,
)

logger = setup_logger(__name__)

BUS_NAME = "org.gnome.Shell"
OBJECT_PATH = "/io/github/ronb1964/TuxTalk/Indicator"
INTERFACE = "io.github.ronb1964.TuxTalk.Indicator"
UPDATE_MS = 33
# What a call gets from a Shell without the extension, or with one older than
# the indicator: a Hide failing so is no news, one per dictation.
NO_EXTENSION = frozenset({
    "org.freedesktop.DBus.Error.UnknownMethod",
    "org.freedesktop.DBus.Error.UnknownObject",
    "org.freedesktop.DBus.Error.UnknownInterface",
    "org.freedesktop.DBus.Error.ServiceUnknown",
})


def create_recording_indicator(**settings):
    if is_gnome():
        try:
            return ShellIndicator(**settings)
        except GLib.Error as error:
            logger.warning("Cannot reach the session bus for the Shell indicator: %s", error)
    return RecordingIndicator(**settings)


@dataclass(frozen=True, slots=True)
class _Settings:
    position: str
    offset_x: int
    offset_y: int
    size: str
    style: str
    color_mode: str
    custom_color: str
    sensitivity: float
    follow_window: bool

    @property
    def scale(self) -> float:
        return _scale_for_size(self.size)


class ShellIndicator:
    """The input thread requests visibility. GTK owns the active backend."""

    def __init__(
        self,
        position: str = "center",
        offset_x: int = 0,
        offset_y: int = 0,
        size: str = "medium",
        style: str = "orb",
        color_mode: str = "custom",
        custom_color: str = "#4db3ff",
        sensitivity: float = 1.0,
        follow_window: bool = False,
    ) -> None:
        """The settings as RecordingIndicator takes them. Nothing is shown
        until show_at_position; the X11 window is made on the first fallback
        and kept for later ones."""
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._settings = self._shown = _Settings(
            position,
            offset_x,
            offset_y,
            size,
            style,
            color_mode,
            custom_color,
            sensitivity,
            follow_window,
        )
        self._x11 = self._fallback = None
        self._geometry_id = None
        self._geometry = None
        self._session = 0
        self._visible = False
        self._recording = False
        # The first switch to X11 since the Shell last drew is a warning, the
        # rest debug: without the extension it would be one per dictation.
        self._fallback_warned = False
        # Guards _wave_history, the radial gain's updates and the handover
        # of both to the X11 window: the audio thread feeds them while GTK's
        # switches to X11, and a block between the two would be lost.
        self._audio_lock = threading.Lock()
        self._update_id = None
        self._level = 0.0
        self._wave_history = []
        self._spectrum = []
        self._radial_gain = LevelGain()
        self._orb_gain = LevelGain()

    def apply_settings(
        self,
        position,
        size,
        offset_x,
        offset_y,
        style=None,
        color_mode=None,
        custom_color=None,
        sensitivity=None,
        follow_window=None,
    ):
        """Keep appearance changes for the next dictation so audio matches the visible style."""
        previous = self._settings
        self._settings = replace(
            previous,
            position=position,
            size=size,
            offset_x=offset_x,
            offset_y=offset_y,
            style=previous.style if style is None else style,
            color_mode=previous.color_mode if color_mode is None else color_mode,
            custom_color=previous.custom_color if custom_color is None else custom_color,
            sensitivity=previous.sensitivity if sensitivity is None else sensitivity,
            follow_window=previous.follow_window if follow_window is None else follow_window,
        )

    def show_at_position(self):
        self._session += 1
        self._shown = self._settings
        self._visible = True
        GLib.idle_add(self._do_show, self._session)

    def _do_show(self, session: int) -> bool:
        """Each dictation tries Shell, including the first one after the extension returns."""
        if session != self._session:
            return False
        self._stop_updates()
        shown = self._shown
        accent = desktop_accent_rgb() if shown.color_mode == "system" else None
        rgb = indicator_styles.resolve_color(shown.color_mode, shown.custom_color, accent)
        # The crisp orb goes as the orb with "crisp": an extension from
        # before it (loaded until the user logs out) refuses a style it doesn't
        # know, and ignores a key it doesn't, so it draws the glowing orb.
        crisp = shown.style == "crisp_orb"
        settings = {
            "style": GLib.Variant("s", "orb" if crisp else shown.style),
            "crisp": GLib.Variant("b", crisp),
            "rgb": GLib.Variant("ad", rgb),
            "scale": GLib.Variant("d", shown.scale),
            "position": GLib.Variant("s", shown.position.lower()),
            "follow": GLib.Variant("b", shown.follow_window),
            "offset_x": GLib.Variant("i", int(shown.offset_x)),
            "offset_y": GLib.Variant("i", int(shown.offset_y)),
        }
        self._geometry_id = self._bus.signal_subscribe(
            BUS_NAME,
            INTERFACE,
            "GeometryChanged",
            OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_geometry,
            session,
        )
        self._call("Show", GLib.Variant("(a{sv})", (settings,)), session)
        self._update_id = GLib.timeout_add(UPDATE_MS, self._send_update, session)
        return False

    def _on_geometry(self, _bus, _sender, _path, _interface, _signal, params, session):
        if session == self._session:
            bounds, primary = params.unpack()
            self._geometry = Rect(*bounds), Rect(*primary)

    def _call(self, method: str, params: GLib.Variant | None, session: int) -> None:
        """Keep the session with the reply so an old failure cannot switch the new backend."""
        self._bus.call(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE,
            method,
            params,
            None,
            Gio.DBusCallFlags.NO_AUTO_START,
            -1,
            None,
            self._on_reply,
            method,
            session,
        )

    def _on_reply(self, bus, result, method, session):
        """A failed Show or Update of this dictation switches it to X11; a
        failed Hide is only logged, unless the extension just isn't there. A
        Show that worked, for any dictation, means the Shell draws again, so
        the next switch to X11 warns again."""
        try:
            bus.call_finish(result)
        except GLib.Error as error:
            if method == "Hide":
                if Gio.DBusError.get_remote_error(error) not in NO_EXTENSION:
                    logger.warning("The Shell didn't hide the recording indicator: %s",
                                   error.message)
            elif session == self._session and self._fallback is None:
                log = logger.debug if self._fallback_warned else logger.warning
                log("Switching to X11 after Shell indicator %s failed: %s", method, error.message)
                self._fallback_warned = True
                self._activate_fallback()
            return
        if method == "Show":
            self._fallback_warned = False

    def _activate_fallback(self):
        """Reuse the GTK window across dictations while Shell is unavailable."""
        self._stop_updates()
        self._call("Hide", None, self._session)
        settings = asdict(self._shown)
        try:
            if self._x11 is None:
                self._x11 = RecordingIndicator(**settings)
            else:
                self._x11.apply_settings(**settings)
            # What the orb has learned of this mic goes with it: a fresh gain
            # reads the first level as the room's noise and holds the orb still.
            self._x11.orb_gain = self._orb_gain
            # The level only: the gain has followed it, and the audio thread
            # may be following the next one until _fallback is set below.
            self._x11.audio_level = self._level
            self._x11.set_spectrum(self._spectrum)
            self._x11.show_at_position()
            if self._recording:
                self._x11.start_recording()
            with self._audio_lock:
                self._x11.set_waveform(self._wave_history)
                self._fallback = self._x11
                self._wave_history = []
            if self.style == "radial" and (line := self._radial_gain.summary()):
                logger.info(line)
        except Exception:
            # Keep a GTK failure from interrupting dictation.
            logger.exception("Could not show the X11 recording indicator")
            if self._x11 is not None:
                self._x11.hide_indicator()

    def _stop_updates(self):
        if self._geometry_id is not None:
            self._bus.signal_unsubscribe(self._geometry_id)
            self._geometry_id = None
        self._geometry = None
        if self._update_id is not None:
            GLib.source_remove(self._update_id)
            self._update_id = None

    def _send_update(self, session: int) -> bool:
        if session == self._session:
            level = (
                indicator_styles.orb_level(self._orb_gain, self._level, self._shown.sensitivity)
                if self.style in indicator_styles.ORB_STYLES
                else self._level
            )
            self._call("Update", GLib.Variant("(dad)", (level, self._shape())), session)
        return True

    @property
    def style(self) -> str:
        return self._shown.style

    def _shape(self):
        sensitivity = self._shown.sensitivity
        if self.style == "waveform":
            return indicator_styles.waveform_points(
                self._wave_history.copy(), BASE_WIDTH * self._shown.scale * 0.60, sensitivity
            )
        if self.style == "radial":
            return indicator_styles.radial_offsets(
                self._wave_history.copy(), sensitivity, gain=self._radial_gain
            )
        if self.style == "bars":
            return [min(1.0, value * sensitivity) for value in self._spectrum]
        return []

    def start_recording(self):
        GLib.idle_add(self._do_start, self._session)

    def _do_start(self, session: int) -> bool:
        if self._visible and session == self._session:
            self._recording = True
            if self._fallback is not None:
                self._fallback.start_recording()
            else:
                self._call("SetRecording", GLib.Variant("(b)", (True,)), session)
        return False

    def hide_indicator(self):
        self._visible = False
        self._session += 1
        self._level = 0.0
        with self._audio_lock:
            self._wave_history = []
        self._spectrum = []
        GLib.idle_add(self._do_hide, self.style)

    def _do_hide(self, style: str) -> bool:
        """Queue the X11 hide after any show that was building the fallback window."""
        if self._fallback is not None:
            self._fallback.hide_indicator()
            self._fallback = None
        self._stop_updates()
        self._recording = False
        self._call("Hide", None, self._session)
        if style == "radial" and (line := self._radial_gain.summary()):
            logger.info(line)
        return False

    def set_audio_level(self, level: float):
        self._level = max(0.0, min(1.0, level))
        # One writer: the fallback window follows the same gain itself.
        if (fallback := self._fallback) is not None:
            fallback.set_audio_level(level)
        else:
            self._orb_gain.follow(self._level, time.monotonic())

    def set_waveform(self, samples):
        with self._audio_lock:
            if (fallback := self._fallback) is not None:
                fallback.set_waveform(samples)
                return
            if self.style == "radial":
                self._radial_gain.update(samples, time.monotonic())
            self._wave_history.extend(samples)
            del self._wave_history[:-1024]

    def set_spectrum(self, bands):
        self._spectrum = [float(v) for v in bands]
        if (fallback := self._fallback) is not None:
            fallback.set_spectrum(bands)

    def get_visible(self) -> bool:
        return self._visible and (self._fallback is None or self._fallback.get_visible())

    def get_position(self) -> tuple[int, int]:
        if self._fallback is not None:
            return self._fallback.get_position()
        if self._geometry is not None:
            return self._screen_bounds()[:2]
        width, height = self.get_size()
        shown = self._shown
        if shown.follow_window and (window := active_window_rect()) is not None:
            monitor = monitor_rect_at(window.x + window.w // 2, window.y + window.h // 2)
            return follow_position(window, width, height, monitor, shown.position)
        return fixed_position(
            shown.position, width, height, main_monitor_rect(), shown.offset_x, shown.offset_y
        )

    def _screen_bounds(self) -> Rect:
        # XWayland has one coordinate scale across the desktop. Both APIs identify the primary.
        bounds, primary = self._geometry
        return to_screen_rect(bounds, "", primary, [("", main_monitor_rect())])

    def get_size(self) -> tuple[int, int]:
        if self._fallback is not None:
            return self._fallback.get_size()
        if self._geometry is not None:
            return self._screen_bounds()[2:]
        scale = self._shown.scale
        return round(BASE_WIDTH * scale), round(BASE_HEIGHT * scale)
