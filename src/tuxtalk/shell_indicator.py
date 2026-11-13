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
from .recording_indicator import BASE_WIDTH, RecordingIndicator, _scale_for_size
from .ui_style import desktop_accent_rgb

logger = setup_logger(__name__)

BUS_NAME = "io.github.ronb1964.TalkType.Indicator"
OBJECT_PATH = "/io/github/ronb1964/TalkType/Indicator"
UPDATE_MS = 33
WAVE_HISTORY = 1024     # the same window the X11 indicator's waveform spans


def available():
    """Whether the extension's indicator is on the session bus."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", (BUS_NAME,)), GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE, 1000, None)
    except GLib.Error as e:
        logger.info(f"Can't ask the session bus for the Shell indicator: {e.message}")
        return False
    return reply.unpack()[0]


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

    def show_at_position(self):
        """Show the indicator; the Shell places it by the focused window."""
        GLib.idle_add(self._do_show)

    def _do_show(self):
        """Send the settings and start the updates; on the GTK thread."""
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
        GLib.idle_add(self._do_start)

    def _do_start(self):
        self._call("SetRecording", GLib.Variant("(b)", (True,)))
        return False

    def hide_indicator(self):
        """Hide the indicator and forget this dictation's sound."""
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

    def set_waveform(self, samples):
        """Append the latest audio block to the rolling window the waveform
        and radial styles draw from."""
        if self.style == "radial":
            self._radial_gain.update(samples, time.monotonic())
        self._wave_history.extend(samples)
        excess = len(self._wave_history) - WAVE_HISTORY
        if excess > 0:
            del self._wave_history[:excess]

    def set_spectrum(self, bands):
        """Latest band magnitudes (0..1), for the bars style."""
        self._spectrum = [float(v) for v in bands]

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
            return self._spectrum
        return []

    def _send_update(self):
        self._call("Update", GLib.Variant("(dad)", (self._level, self._shape())))
        return True

    def _call(self, method, params=None):
        self._bus.call(BUS_NAME, OBJECT_PATH, BUS_NAME, method, params, None,
                       Gio.DBusCallFlags.NO_AUTO_START, -1, None, self._on_reply, method)

    @staticmethod
    def _on_reply(bus, result, method):
        try:
            bus.call_finish(result)
        except GLib.Error as e:
            # The Shell disables extensions while the screen is locked.
            if method != "Update":
                logger.warning(f"The Shell indicator's {method} failed: {e.message}")
