#!/usr/bin/env python3
"""
Visual recording indicator for TuxTalk
Shows an animated orb with satellites near the cursor during dictation
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
try:
    # Try to use Cairo from GObject Introspection (needed for AppImage)
    gi.require_foreign('cairo')
except (ValueError, ImportError):
    pass  # Already available or not needed

from gi.repository import Gtk, Gdk, GLib
import cairo
import math
import os
import time

from . import desktop_detect, indicator_styles, ui_style
from .indicator_styles import RadialGain
from .logger import setup_logger

logger = setup_logger(__name__)


# Unscaled window size. show_at_position() positions the window as
# BASE_WIDTH * scale, so the window itself must be created at that size or
# right- and bottom-anchored positions land off by the difference.
BASE_WIDTH = 200
BASE_HEIGHT = 180

SIZE_SCALES = {"small": 0.6, "medium": 1.0, "large": 1.4}


def _scale_for_size(size):
    """Scale factor for a configured size name, defaulting to medium."""
    return SIZE_SCALES.get(str(size).lower(), 1.0)


def positioning_available():
    """Whether the indicator can actually honour indicator_position.

    Positioning is done with gtk_window_move(), which XWayland honours and
    native Wayland ignores (it centres the window instead). The tray launches
    the dictation service with GDK_BACKEND=x11,wayland, so the indicator runs
    under XWayland whenever an X display exists — on any desktop, and with no
    GNOME extension involved.

    Preferences uses this to decide whether to offer the position control, so
    it must describe reality rather than the session type: a KDE Plasma Wayland
    session with XWayland present positions the indicator correctly.
    """
    return bool(os.environ.get("DISPLAY"))


# TuxTalk Labs, "follow the active window": how far inside the window being
# dictated into the indicator sits. More room at the top, to clear the title
# bar (part of the frame), and at the bottom, to clear a status bar.
FOLLOW_MARGIN = 40      # above the bottom edge
FOLLOW_TOP = 50         # below the top edge
FOLLOW_SIDE = 20        # in from the left and right edges


def follow_position(window, width, height, monitor, position="bottom-center"):
    """Top-left corner for a *width* x *height* indicator at *position* (the
    Audio tab's choice: "bottom-center", "top-left", "center"...) within
    *window*, kept on *monitor* (window_geometry.Rect values). The Audio
    tab's offset is for the whole screen, so it doesn't apply here."""
    position = (position or "").lower()
    if "left" in position:
        x = window.x + FOLLOW_SIDE
    elif "right" in position:
        x = window.x + window.w - width - FOLLOW_SIDE
    else:
        x = window.x + (window.w - width) // 2
    if position.startswith("top"):
        y = window.y + FOLLOW_TOP
    elif position.startswith("bottom"):
        y = window.y + window.h - height - FOLLOW_MARGIN
    else:
        y = window.y + (window.h - height) // 2
    x = max(monitor.x, min(x, monitor.x + monitor.w - width))
    y = max(monitor.y, min(y, monitor.y + monitor.h - height))
    return x, y


# How far the fixed positions (top-left, bottom-center...) keep from the
# monitor's edges.
EDGE_MARGIN = 20


def fixed_position(position, width, height, monitor, offset_x=0, offset_y=0):
    """Top-left corner for a *width* x *height* indicator at *position*
    ("center", "top-left", "bottom-center", "right-center"...) on *monitor*
    (a window_geometry.Rect), moved by the Audio tab's offset and kept on that
    monitor. Unknown names mean the centre."""
    position = (position or "").lower()
    if "left" in position:
        x = EDGE_MARGIN
    elif "right" in position:
        x = monitor.w - width - EDGE_MARGIN
    else:
        x = (monitor.w - width) // 2
    if position.startswith("top"):
        y = EDGE_MARGIN
    elif position.startswith("bottom"):
        y = monitor.h - height - EDGE_MARGIN
    else:
        y = (monitor.h - height) // 2
    x = monitor.x + x + offset_x
    y = monitor.y + y + offset_y
    x = max(monitor.x, min(x, monitor.x + monitor.w - width))
    y = max(monitor.y, min(y, monitor.y + monitor.h - height))
    return x, y


def _main_monitor_rect():
    """The primary monitor (or the first one), as a window_geometry.Rect.
    The screen's own size spans every monitor, which would put "center" on
    the seam between two side-by-side screens."""
    from .window_geometry import Rect
    display = Gdk.Display.get_default()
    g = (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()
    return Rect(g.x, g.y, g.width, g.height)


def _monitor_rect_at(x, y):
    """The monitor holding (x, y), as a window_geometry.Rect."""
    from .window_geometry import Rect
    display = Gdk.Display.get_default()
    g = (display.get_monitor_at_point(x, y) or display.get_monitor(0)).get_geometry()
    return Rect(g.x, g.y, g.width, g.height)


def settings_in(cfg) -> dict:
    """The indicator's settings in *cfg* (config.Settings), as
    RecordingIndicator and its apply_settings take them."""
    return {"position": cfg.indicator_position, "size": cfg.indicator_size,
            "offset_x": cfg.indicator_offset_x, "offset_y": cfg.indicator_offset_y,
            "style": cfg.indicator_style, "color_mode": cfg.indicator_color_mode,
            "custom_color": cfg.indicator_color, "sensitivity": cfg.indicator_sensitivity,
            "follow_window": cfg.indicator_follow_window}


def window_type():
    """A popup on COSMIC, an ordinary window elsewhere.

    COSMIC's X11 window manager focuses the indicator when it appears, even
    though it asks for no focus below (Pop!_OS VM, 2026-10-21): the app
    being dictated into lost the keyboard for the whole recording, and a
    space typed during it went nowhere. A popup (override-redirect) is never
    managed, so never focused, as the floating labels in screen_message are.
    The ordinary window stays where it was proven, on GNOME, KDE and X11.
    """
    if desktop_detect.get_desktop_environment() == "cosmic":
        return Gtk.WindowType.POPUP
    return Gtk.WindowType.TOPLEVEL


class RecordingIndicator(Gtk.Window):
    """Floating recording indicator window with configurable position"""

    def __init__(self, position="center", offset_x=0, offset_y=0, size="medium",
                 style="orb", color_mode="custom", custom_color="#4db3ff",
                 sensitivity=1.0, follow_window=False):
        """Build the indicator window, not shown yet: transparent, above
        everything and never taking the focus, drawn in *style* at *size*,
        placed at *position* plus the offsets, or near the window being
        dictated into with *follow_window*."""
        super().__init__(title="TuxTalk Recording", type=window_type())
        # TuxTalk Labs: show near the bottom of the window being dictated
        # into, instead of the fixed position (_follow_target).
        self.follow_window = follow_window

        # Style + appearance. The orb keeps its own draw path; the other styles
        # are drawn by indicator_styles from the data fed below. color_mode
        # (system/custom) governs every style, including the orb.
        self.style = style
        self.color_mode = color_mode
        self.custom_color = custom_color
        self.sensitivity = sensitivity
        self.waveform = None   # rolling recent samples for waveform/radial
        self._wave_history = []          # accumulated rolling window (see set_waveform)
        self._wave_history_max = 1024    # ~23 ms at 44.1 kHz; spans the display
        self.spectrum = None   # band magnitudes for bars
        self._radial_gain = RadialGain()   # learns this mic's range for the radial
        # ((color_mode, custom_color), rgb, orb palette), worked out once
        # rather than on every frame; cleared at each show so a theme
        # accent change is picked up by the next dictation.
        self._color_cache = None

        # Position configuration
        self.position = position.lower()
        self.offset_x = offset_x
        self.offset_y = offset_y

        self.scale = _scale_for_size(size)

        # Window setup - transparent, always on top, no decorations
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_can_focus(False)
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)  # stays above, never takes focus
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        # Room for the orb, its satellites at full extension and the timer
        self.set_default_size(int(BASE_WIDTH * self.scale), int(BASE_HEIGHT * self.scale))

        # Try to allow positioning (may not work on all compositors)
        self.set_position(Gtk.WindowPosition.NONE)

        # Make window support transparency
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        # Animation state
        self.start_time = time.time()
        self.pulse_phase = 0.0  # For pulsing animation
        self.base_size = int(15 * self.scale)  # Base orb radius (scaled)
        self.particle_pulse_phase = 0.0  # For individual particle pulsing
        self.is_recording = False
        self.audio_level = 0.0  # Real-time audio level (0-1)

        # Drawing area
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.connect('draw', self.on_draw)
        self.add(self.drawing_area)

        # Animation timer (60 FPS)
        self.animation_timeout_id = None

        # Stop the animation timer if the window is ever destroyed directly
        # (not via hide_indicator) — otherwise the 60fps callback keeps firing
        # queue_draw() on a destroyed widget, spamming GLib-GObject-CRITICALs.
        self.connect('destroy', self._on_destroy)

        self.set_app_paintable(True)

        # An empty input region lets every click fall through to the window
        # underneath. None would remove the shape instead, and then the whole
        # transparent rectangle takes clicks.
        self.connect("realize", lambda w: w.input_shape_combine_region(cairo.Region()))

    def _on_destroy(self, _widget):
        if self.animation_timeout_id is not None:
            GLib.source_remove(self.animation_timeout_id)
            self.animation_timeout_id = None

    def show_at_position(self):
        """Show the indicator at the configured position"""
        # Must be called from GTK main thread
        def _do_show():
            """Place the window, show it without taking the focus and start the
            animation, on GTK's thread."""
            try:
                # Window dimensions — same expression used to size the window,
                # so the maths and the real window cannot disagree.
                window_width = int(BASE_WIDTH * self.scale)
                window_height = int(BASE_HEIGHT * self.scale)

                follow = self._follow_target(window_width, window_height)
                if follow:
                    # TuxTalk Labs: near the bottom of the window being
                    # dictated into; the configured offset doesn't apply.
                    window_x, window_y = follow
                else:
                    window_x, window_y = fixed_position(
                        self.position, window_width, window_height,
                        _main_monitor_rect(), self.offset_x, self.offset_y)

                self.show_all()
                # Don't call present() - it might steal focus from the text entry field

                # Move AFTER showing (Wayland may ignore this, but try anyway)
                self.move(window_x, window_y)

                # Try moving again after a tiny delay to work around compositor repositioning
                def try_move_again():
                    self.move(window_x, window_y)
                    return False
                GLib.timeout_add(50, try_move_again)

                # Start animation
                self._color_cache = None
                self.start_time = time.time()
                if self.animation_timeout_id is None:
                    self.animation_timeout_id = GLib.timeout_add(16, self.update_animation)
            except Exception:
                logger.exception("Error showing indicator")
            return False  # Don't repeat

        GLib.idle_add(_do_show)

    def _follow_target(self, width, height):
        """Where to show when following the active window, or None to use the
        configured position (following is off, or nothing said where the
        window is)."""
        if not self.follow_window:
            return None
        from . import window_geometry
        rect = window_geometry.active_window_rect()
        if not rect:
            return None
        monitor = _monitor_rect_at(rect.x + rect.w // 2, rect.y + rect.h // 2)
        return follow_position(rect, width, height, monitor, self.position)

    def hide_indicator(self):
        """Hide the indicator and stop animation"""
        def _do_hide():
            """Hide the window, stop its animation and clear the recording's
            sound, on GTK's thread."""
            self.hide()
            if self.animation_timeout_id is not None:
                GLib.source_remove(self.animation_timeout_id)
                self.animation_timeout_id = None
            # The app calls only hide_indicator(), so clear the recording state
            # and buffers here, or the last dictation's sound could flash up
            # when the next one begins.
            self.stop_recording()
            # One line per dictation of what this mic delivered, for tuning
            # the radial.
            if self.style == "radial":
                line = self._radial_gain.summary()
                if line:
                    logger.info(line)
            return False

        GLib.idle_add(_do_hide)

    def start_recording(self):
        """Start recording mode.

        Queued on the GTK loop like show and hide: set straight away from the
        recording thread, a hide still pending from the previous dictation
        would run after it and clear it again."""
        def _do_start():
            self.is_recording = True
            self.start_time = time.time()
            return False

        GLib.idle_add(_do_start)

    def stop_recording(self):
        """Stop recording mode"""
        self.is_recording = False
        self.audio_level = 0.0
        # Clear the reactive buffers so nothing lingers on screen after the key
        # is released — draw_waveform/draw_bars early-out on empty data, where
        # a bright dash of the last block would otherwise stay for seconds.
        self._wave_history = []
        self.waveform = None
        self.spectrum = None

    def apply_settings(self, position, size, offset_x, offset_y,
                       style=None, color_mode=None, custom_color=None,
                       sensitivity=None, follow_window=None):
        """Adopt new appearance settings without rebuilding the indicator.

        show_at_position() reads position, scale and the offsets at show time,
        so updating them is enough: the change takes effect the next time the
        indicator is shown. A visible indicator is never moved: the orb
        should not jump mid-dictation. Keyword arguments left as None keep
        their current value.

        Called from the service's config watcher on the input thread, so
        Preferences can change these without a service restart. The change
        is made on GTK's thread, all of it at once: a frame drawn there
        between a new scale and the old orb size or window would mix them.
        """
        GLib.idle_add(lambda: self._apply_settings(
            position, size, offset_x, offset_y, style, color_mode, custom_color,
            sensitivity, follow_window))

    def _apply_settings(self, position, size, offset_x, offset_y, style, color_mode,
                        custom_color, sensitivity, follow_window):
        """apply_settings' change, on GTK's thread."""
        self.position = str(position).lower()
        self.offset_x = offset_x
        self.offset_y = offset_y
        if follow_window is not None:
            self.follow_window = follow_window
        new_scale = _scale_for_size(size)
        if new_scale != self.scale:
            self.scale = new_scale
            self.base_size = int(15 * new_scale)     # the orb, with its window
            # The window must match the size the positioning maths assumes.
            width, height = int(BASE_WIDTH * new_scale), int(BASE_HEIGHT * new_scale)
            self.set_default_size(width, height)
            self.resize(width, height)
        if style is not None:
            self.style = style
        if color_mode is not None:
            self.color_mode = color_mode
        if custom_color is not None:
            self.custom_color = custom_color
        if sensitivity is not None:
            self.sensitivity = sensitivity
        return False

    @property
    def _orb_level(self):
        """The audio level as the orb shows it, scaled by Animation response
        (sensitivity) as the other styles' levels are."""
        return min(1.0, self.audio_level * self.sensitivity)

    def set_audio_level(self, level: float):
        """Update the audio level for satellite animation (0.0 - 1.0)"""
        self.audio_level = max(0.0, min(1.0, level))

    def set_waveform(self, samples):
        """Append the latest audio block to a rolling window and expose that as
        the waveform, so the display is independent of the driver's block size.

        The Flatpak's bundled PortAudio delivers uniform 384-frame blocks
        ~115x/sec and the system PortAudio smaller, varied blocks ~193x/sec, so
        drawing only the latest block would look different on each."""
        if self.style == "radial":
            self._radial_gain.update(samples, time.monotonic())
        self._wave_history.extend(samples)
        excess = len(self._wave_history) - self._wave_history_max
        if excess > 0:
            del self._wave_history[:excess]
        self.waveform = self._wave_history

    def set_spectrum(self, bands):
        """Latest band magnitudes (0..1), for the bars style."""
        self.spectrum = bands

    def _resolved_colors(self):
        """(rgb, orb palette) for the current color settings, cached: the
        accent lookup builds a widget, and this runs on every frame."""
        key = (self.color_mode, self.custom_color)
        cache = self._color_cache
        if cache is None or cache[0] != key:
            accent = ui_style.desktop_accent_rgb() if self.color_mode == "system" else None
            rgb = indicator_styles.resolve_color(self.color_mode, self.custom_color, accent)
            cache = (key, rgb, indicator_styles.orb_palette_for(rgb))
            self._color_cache = cache
        return cache[1], cache[2]

    def _resolved_color(self):
        return self._resolved_colors()[0]

    def update_animation(self):
        """Update animation state"""
        if self.is_recording:
            # When recording: orb is frozen, satellites move with audio
            pass  # audio_level is updated externally
        else:
            # At rest mode: orb pulsates, particles stay stationary
            self.pulse_phase = (self.pulse_phase + 0.05) % (2 * math.pi)
            self.particle_pulse_phase = (self.particle_pulse_phase + 0.03) % (2 * math.pi)

        # Trigger redraw
        self.drawing_area.queue_draw()
        return True

    def on_draw(self, widget, cr):
        """Draw the indicator"""
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()

        # Clear with full transparency
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Non-orb styles are drawn by indicator_styles from the fed data. The
        # orb keeps its own draw path below.
        if self.style != "orb":
            st = indicator_styles
            color = self._resolved_color()
            # A copy: the audio thread extends and trims the live list while
            # this draws.
            wave = list(self.waveform) if self.waveform is not None else []
            if self.style == "waveform":
                st.draw_waveform(cr, width, height, wave,
                                 self.audio_level, color, self.sensitivity)
            elif self.style == "bars":
                st.draw_bars(cr, width, height,
                             self.spectrum if self.spectrum is not None else [],
                             color, self.sensitivity)
            elif self.style == "radial":
                st.draw_radial(cr, width, height, wave,
                               self.audio_level, color, self.sensitivity,
                               gain=self._radial_gain)
            return

        # Calculate orb position (center-left area)
        orb_x = width * 0.3
        orb_y = height * 0.5

        # Draw pill-shaped background container
        self.draw_pill_background(cr, orb_x, orb_y)

        # Draw the animated orb. For the classic cyan the orb uses its
        # hand-tuned gradient (palette=None); any other color recolors it via a
        # palette that keeps the same structure and only changes the hue.
        orb_palette = self._resolved_colors()[1]
        self.draw_orb(cr, orb_x, orb_y, orb_palette)

        # Draw timer aligned with orb center (scaled position)
        elapsed = int(time.time() - self.start_time)
        self.draw_timer(cr, orb_x + 32 * self.scale, orb_y, elapsed)

    def draw_pill_background(self, cr, orb_x, orb_y):
        """Draw semi-transparent pill-shaped background"""
        # Pill dimensions - scaled
        pill_width = 130 * self.scale
        pill_height = 55 * self.scale
        pill_radius = pill_height / 2
        pill_x = orb_x - 32 * self.scale
        pill_y = orb_y - pill_height / 2

        # The centres of the pill's four rounded corners.
        left, right = pill_x + pill_radius, pill_x + pill_width - pill_radius
        top, bottom = pill_y + pill_radius, pill_y + pill_height - pill_radius

        def pill_path():
            cr.new_sub_path()
            cr.arc(left, top, pill_radius, math.pi, 3 * math.pi / 2)
            cr.arc(right, top, pill_radius, 3 * math.pi / 2, 0)
            cr.arc(right, bottom, pill_radius, 0, math.pi / 2)
            cr.arc(left, bottom, pill_radius, math.pi / 2, math.pi)
            cr.close_path()

        pill_path()

        # Semi-transparent dark background - more opaque
        cr.set_source_rgba(0.1, 0.1, 0.15, 0.85)
        cr.fill()

        # Red border when recording (push-to-talk active)
        if self.is_recording:
            pill_path()
            cr.set_source_rgba(1.0, 0.2, 0.2, 0.9)  # Bright red
            cr.set_line_width(2.5 * self.scale)
            cr.stroke()

    def draw_orb(self, cr, x, y, palette=None):
        """Draw the glowing orb with pulsing animation.

        palette=None draws the classic cyan gradient. A palette (from
        indicator_styles.orb_palette) recolors the glow, core and particles
        while keeping the same structure.
        """
        # Calculate current size based on gentle pulse (only when not recording)
        if self.is_recording:
            # Freeze the pulse when recording - keep orb steady
            current_size = self.base_size
        else:
            # Gentle pulse animation when at rest
            pulse_scale = 1.0 + 0.1 * math.sin(self.pulse_phase)
            current_size = self.base_size * pulse_scale

        # Outer glow layers (multiple for softer effect)
        glow_color = palette["glow"] if palette else (0.3, 0.7, 1.0)  # Cyan

        for i in range(3):
            glow_radius = current_size * (1.5 - i * 0.15)
            gradient = cairo.RadialGradient(x, y, current_size * 0.5, x, y, glow_radius)

            alpha = 0.4 * (1.0 - i * 0.3)
            if self.is_recording:
                alpha *= self._orb_level
            gradient.add_color_stop_rgba(0, glow_color[0], glow_color[1], glow_color[2], alpha)
            gradient.add_color_stop_rgba(1, glow_color[0], glow_color[1], glow_color[2], 0)

            cr.set_source(gradient)
            cr.arc(x, y, glow_radius, 0, 2 * math.pi)
            cr.fill()

        # Core orb with soft, hazy edge
        core_gradient = cairo.RadialGradient(x, y, 0, x, y, current_size * 1.3)
        if palette:
            for stop, rgb, a in palette["core"]:
                core_gradient.add_color_stop_rgba(stop, rgb[0], rgb[1], rgb[2], a)
        else:
            core_gradient.add_color_stop_rgba(0, 1.0, 1.0, 1.0, 1.0)  # White center
            core_gradient.add_color_stop_rgba(0.4, 0.5, 0.9, 1.0, 0.9)  # Light cyan
            core_gradient.add_color_stop_rgba(0.7, 0.2, 0.6, 0.9, 0.6)  # Blue mid
            core_gradient.add_color_stop_rgba(0.9, 0.2, 0.5, 0.8, 0.3)  # Soft edge
            core_gradient.add_color_stop_rgba(1.0, 0.2, 0.5, 0.8, 0.0)  # Fade to transparent

        cr.set_source(core_gradient)
        cr.arc(x, y, current_size * 1.3, 0, 2 * math.pi)
        cr.fill()

        # Particles around orb - use fixed base_size, not pulsing current_size
        self.draw_particles(cr, x, y, self.base_size * 1.8, palette)

    def draw_particles(self, cr, center_x, center_y, base_radius, palette=None):
        """Draw small glowing particles that extend radially based on voice.

        palette=None keeps the classic cyan→blue grading; a palette recolors
        the near→far gradient to match a chosen orb color.
        """
        num_particles = 16

        for i in range(num_particles):
            # Fixed angle for each particle (evenly distributed around circle)
            angle = i * 2 * math.pi / num_particles

            # Radial distance: start touching orb, limited extension
            # Quiet/Silence (0.0): 0.7x base radius (very close, almost touching)
            # Loud (1.0): 1.4x base radius
            min_distance = 0.7
            max_distance = 1.4

            if self.is_recording:
                # Move radially based on audio level
                particle_radial_extend = self._orb_level
            else:
                # Stay at rest position
                particle_radial_extend = 0.0

            spread = max_distance - min_distance
            radial_distance = base_radius * (min_distance + spread * particle_radial_extend)

            # Particle position along the radial line
            px = center_x + radial_distance * math.cos(angle)
            py = center_y + radial_distance * math.sin(angle)

            # Particle size: subtle pulse at rest, or responds to voice when active
            if self.is_recording:
                # Size varies with voice level
                particle_size = 4 + (self._orb_level * 2)
            else:
                # Subtle individual pulse at rest (each particle slightly offset)
                individual_offset = i * 0.4  # Stagger the pulse per particle
                pulse = 1.0 + 0.15 * math.sin(self.particle_pulse_phase + individual_offset)
                particle_size = 4 * pulse

            # Color shifts based on distance from orb: near→far grading.
            distance_factor = (radial_distance / base_radius - min_distance) / spread
            if palette:
                near, far = palette["p_near"], palette["p_far"]
                r = near[0] + (far[0] - near[0]) * distance_factor
                g = near[1] + (far[1] - near[1]) * distance_factor
                b = near[2] + (far[2] - near[2]) * distance_factor
            else:
                # Classic: warm cyan/white (0.7,0.95,1.0) → cooler blue (0.4,0.6,1.0)
                r = 0.7 - (distance_factor * 0.3)
                g = 0.95 - (distance_factor * 0.35)
                b = 1.0

            # Particle glow (brighter when more energetic)
            alpha = 0.6 + (self._orb_level * 0.3) if self.is_recording else 0.6
            particle_gradient = cairo.RadialGradient(px, py, 0, px, py, particle_size * 1.5)
            particle_gradient.add_color_stop_rgba(0, r, g, b, alpha)
            particle_gradient.add_color_stop_rgba(1, r * 0.7, g * 0.7, b * 0.7, 0)

            cr.set_source(particle_gradient)
            cr.arc(px, py, particle_size * 1.5, 0, 2 * math.pi)
            cr.fill()

    def draw_timer(self, cr, x, y, seconds):
        """Draw the timer text centered vertically with orb"""
        # Format time as MM:SS
        minutes = seconds // 60
        secs = seconds % 60
        time_text = f"{minutes}:{secs:02d}"

        # Set font - scaled
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(22 * self.scale)

        # Get text extents for vertical centering
        extents = cr.text_extents(time_text)
        text_height = extents.height
        text_y = y + text_height / 2

        # Draw shadow for readability (scaled offset)
        cr.set_source_rgba(0, 0, 0, 0.5)
        cr.move_to(x + 2 * self.scale, text_y + 2 * self.scale)
        cr.show_text(time_text)

        # Draw main text
        cr.set_source_rgb(0.9, 0.9, 0.9)
        cr.move_to(x, text_y)
        cr.show_text(time_text)
