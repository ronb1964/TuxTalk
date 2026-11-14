"""The recording indicator drawn by TuxTalk's GNOME Shell extension.

The X11 indicator (recording_indicator.py) is scaled by Mutter from
XWayland's resolution, which blurs it on a fractionally scaled monitor, and it
knows where the focused window is only from what the extension reports. When
the extension's indicator is on the session bus, the service hands it the
drawing instead (shellIndicator.js): the settings when it shows, then the
audio level and the active style's shape about 30 times a second. The Shell
draws it at the monitor's own scale and keeps it by the focused window.
"""
import time

from gi.repository import Gio, GLib

from . import indicator_styles
from .indicator_styles import RadialGain
from .logger import setup_logger
from .recording_indicator import (BASE_HEIGHT, BASE_WIDTH, FOLLOW_MARGIN, RecordingIndicator, _scale_for_size,
                                  fixed_position, follow_position)
from .ui_style import desktop_accent_rgb
from .window_geometry import active_window_rect, main_monitor_rect, monitor_rect_at

logger = setup_logger(__name__)

BUS_NAME = "org.gnome.Shell"
OBJECT_PATH = "/io/github/ronb1964/TuxTalk/Indicator"
INTERFACE = "io.github.ronb1964.TuxTalk.Indicator"
UPDATE_MS = 33
WAVE_HISTORY = 1024     # the same window the X11 indicator's waveform spans


def available():
    """Whether the extension exports the indicator on GNOME Shell's bus."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = []
        loop = GLib.MainLoop()

        def answered(connection, reply):
            try:
                xml = connection.call_finish(reply).unpack()[0]
                result.append(f'<interface name="{INTERFACE}">' in xml)
            except GLib.Error:
                result.append(False)
            loop.quit()

        bus.call(BUS_NAME, OBJECT_PATH, "org.freedesktop.DBus.Introspectable",
                 "Introspect", None, GLib.VariantType("(s)"), Gio.DBusCallFlags.NO_AUTO_START,
                 1000, None, answered)
        loop.run()
    except GLib.Error as error:
        logger.info(f"Can't ask the session bus for the Shell indicator: {error.message}")
        return False
    return result[0]


def create_recording_indicator(**settings):
    """The Shell extension's indicator when it is on the bus, else the X11 window."""
    indicator_class = ShellIndicator if available() else RecordingIndicator
    logger.info(f"Recording indicator drawn by {indicator_class.__name__}")
    return indicator_class(**settings)


def waveform_points(samples, width, sensitivity):
    """The waveform as the extension draws it: about one point per pixel of
    its *width*-wide line, scaled by sensitivity and kept within -1..1."""
    count = max(1, int(width))
    step = max(1, len(samples) // count)
    return [max(-1.0, min(1.0, float(v) * 3.0 * sensitivity)) for v in samples[::step][:count]]


class ShellIndicator:
    """RecordingIndicator's calls, forwarded to the Shell extension.

    The audio thread feeds the level and samples; a GLib timer on the GTK
    thread sends them on, so the bus carries 30 updates a second however
    often the sound card delivers.
    """

    def __init__(self, position="center", offset_x=0, offset_y=0, size="medium",
                 style="orb", color_mode="custom", custom_color="#4db3ff",
                 sensitivity=1.0, follow_window=False):
        """Takes the same settings as RecordingIndicator; nothing shows yet."""
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.style = style
        self.color_mode = color_mode
        self.custom_color = custom_color
        self.sensitivity = sensitivity
        self.follow_window = follow_window
        self._fallback = None
        self._visible = False
        self._recording = False
        self.apply_settings(position, size, offset_x, offset_y)
        self._update_id = None
        self._level = 0.0
        self._wave_history = []
        self._spectrum = []
        self._radial_gain = RadialGain()

    def apply_settings(self, position, size, offset_x, offset_y,
                       style=None, color_mode=None, custom_color=None,
                       sensitivity=None, follow_window=None):
        """Adopt new appearance settings; they apply from the next show.
        Keyword arguments left as None keep their current value."""
        self.position = str(position).lower()
        self.size = size
        self.scale = _scale_for_size(size)
        self.offset_x = offset_x
        self.offset_y = offset_y
        if style is not None:
            self.style = style
        if color_mode is not None:
            self.color_mode = color_mode
        if custom_color is not None:
            self.custom_color = custom_color
        if sensitivity is not None:
            self.sensitivity = sensitivity
        if follow_window is not None:
            self.follow_window = follow_window
        if self._fallback is not None:
            self._fallback.apply_settings(position, size, offset_x, offset_y, style,
                                          color_mode, custom_color, sensitivity, follow_window)

    def show_at_position(self):
        """Show the indicator; the Shell places it by the focused window."""
        self._visible = True
        if self._fallback is not None:
            self._fallback.show_at_position()
            return
        GLib.idle_add(self._do_show)

    def _do_show(self):
        """Send the settings and start the updates; on the GTK thread."""
        if not self._visible or self._fallback is not None:
            return False
        accent = desktop_accent_rgb() if self.color_mode == "system" else None
        rgb = indicator_styles.resolve_color(self.color_mode, self.custom_color, accent)
        settings = {
            "style": GLib.Variant("s", self.style),
            "rgb": GLib.Variant("ad", rgb),
            "scale": GLib.Variant("d", self.scale),
            "position": GLib.Variant("s", self.position),
            "follow": GLib.Variant("b", self.follow_window),
            "offset_x": GLib.Variant("i", int(self.offset_x)),
            "offset_y": GLib.Variant("i", int(self.offset_y)),
        }
        self._call("Show", GLib.Variant("(a{sv})", (settings,)))
        if self._update_id is None:
            self._update_id = GLib.timeout_add(UPDATE_MS, self._send_update)
        return False

    def start_recording(self):
        """Turn the indicator to recording; queued behind a pending show."""
        self._recording = True
        if self._fallback is not None:
            self._fallback.start_recording()
            return
        GLib.idle_add(self._do_start)

    def _do_start(self):
        if not self._visible or self._fallback is not None:
            return False
        self._call("SetRecording", GLib.Variant("(b)", (True,)))
        return False

    def hide_indicator(self):
        """Hide the indicator and forget this dictation's sound."""
        self._visible = False
        self._recording = False
        if self._fallback is not None:
            self._fallback.hide_indicator()
        GLib.idle_add(self._do_hide)

    def _do_hide(self):
        if self._update_id is not None:
            GLib.source_remove(self._update_id)
            self._update_id = None
        self._call("Hide")
        self._level = 0.0
        self._wave_history = []
        self._spectrum = []
        if self.style == "radial" and (line := self._radial_gain.summary()):
            logger.info(line)
        return False

    def set_audio_level(self, level: float):
        """Latest audio level (0..1), for the orb and the color brightness."""
        self._level = max(0.0, min(1.0, level))
        if self._fallback is not None:
            self._fallback.set_audio_level(level)

    def set_waveform(self, samples):
        """Append the latest audio block to the rolling window the waveform
        and radial styles draw from."""
        if self.style == "radial":
            self._radial_gain.update(samples, time.monotonic())
        self._wave_history.extend(samples)
        excess = len(self._wave_history) - WAVE_HISTORY
        if excess > 0:
            del self._wave_history[:excess]
        if self._fallback is not None:
            self._fallback.set_waveform(samples)

    def set_spectrum(self, bands):
        """Latest band magnitudes (0..1), for the bars style."""
        self._spectrum = [float(v) for v in bands]
        if self._fallback is not None:
            self._fallback.set_spectrum(bands)

    def get_visible(self):
        """Whether the active indicator backend is visible to live text."""
        return self._fallback.get_visible() if self._fallback is not None else self._visible

    def get_position(self):
        """Indicator position in the GTK coordinate space used by live text."""
        if self._fallback is not None:
            return self._fallback.get_position()
        width, height = self.get_size()
        window = active_window_rect() if self.follow_window else None
        if window is not None:
            monitor = monitor_rect_at(window.x + window.w // 2, window.y + window.h // 2)
            left, top = follow_position(window, width, height, monitor, self.position)
            if self.position.startswith("bottom"):
                # Match Shell's 20% bottom gap when anchoring live text.
                top = max(monitor.y, min(round(window.y + window.h - height -
                                               max(FOLLOW_MARGIN, window.h * 0.2)),
                                         monitor.y + monitor.h - height))
            return left, top
        return fixed_position(self.position, width, height, main_monitor_rect(),
                              self.offset_x, self.offset_y)

    def get_size(self):
        """Indicator size in the GTK coordinate space used by live text."""
        if self._fallback is not None:
            return self._fallback.get_size()
        return round(BASE_WIDTH * self.scale), round(BASE_HEIGHT * self.scale)

    def _shape(self):
        """The active style's shape, in the form shellIndicator.js draws it."""
        # A copy: the audio thread extends and trims the live list meanwhile.
        samples = list(self._wave_history)
        if self.style == "waveform":
            return waveform_points(samples, BASE_WIDTH * self.scale * 0.60, self.sensitivity)
        if self.style == "radial":
            return indicator_styles.radial_offsets(samples, self.sensitivity,
                                                   gain=self._radial_gain)
        if self.style == "bars":
            return [min(1.0, value * self.sensitivity) for value in self._spectrum]
        return []

    def _send_update(self):
        if self._fallback is not None:
            self._update_id = None
            return False
        level = min(1.0, self._level * self.sensitivity) if self.style == "orb" else self._level
        self._call("Update", GLib.Variant("(dad)", (level, self._shape())))
        return True

    def _call(self, method, params=None):
        self._bus.call(BUS_NAME, OBJECT_PATH, INTERFACE, method, params, None,
                       Gio.DBusCallFlags.NO_AUTO_START, -1, None, self._on_reply, method)

    def _on_reply(self, bus, result, method):
        try:
            bus.call_finish(result)
        except GLib.Error as error:
            # The Shell disables extensions while the screen is locked.
            if method not in ("Update", "Hide"):
                logger.warning(f"The Shell indicator's {method} failed: {error.message}")
            if method != "Hide" and self._visible and self._fallback is None:
                GLib.idle_add(self._activate_fallback)

    def _activate_fallback(self):
        if not self._visible or self._fallback is not None:
            return False
        fallback = None
        try:
            fallback = RecordingIndicator(
                position=self.position, offset_x=self.offset_x, offset_y=self.offset_y,
                size=self.size, style=self.style, color_mode=self.color_mode,
                custom_color=self.custom_color, sensitivity=self.sensitivity,
                follow_window=self.follow_window)
            fallback.set_audio_level(self._level)
            fallback.set_waveform(self._wave_history)
            fallback.set_spectrum(self._spectrum)
            fallback.show_at_position()
            if self._recording:
                fallback.start_recording()
            self._fallback = fallback
        except Exception as error:
            # Keep a broken X11 backend out of the GTK event loop.
            logger.error(f"Could not switch to the X11 indicator: {error}", exc_info=True)
            if fallback is not None:
                fallback.hide_indicator()
            return False
        if self._update_id is not None:
            GLib.source_remove(self._update_id)
            self._update_id = None
        self._call("Hide")
        logger.info("Shell indicator unavailable; using the X11 indicator")
        return False
