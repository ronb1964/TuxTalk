"""Cairo drawing for the recording indicator's non-orb styles and the soft
backing, the orbs' palettes, and LevelGain, which scales each style to the mic.

Pure cairo (no GTK widget), so every renderer is testable by drawing onto an
ImageSurface with no display.
"""
import math
import random
from dataclasses import dataclass

import cairo
import numpy as np

# Core alpha of the soft dark backing behind the non-orb styles, which keeps
# them readable over a light window.
BACKING_CORE = 0.50

# The classic orb cyan; also the fallback for an unreadable custom color.
CYAN = (0.30, 0.70, 1.0)


def resolve_color(mode, custom_hex, accent_rgb):
    """(r,g,b) in 0..1 for the active color mode.

    system → the desktop accent, custom → the chosen hex, anything else →
    CYAN. One control governs every style, including the orb.
    """
    if mode == "custom":
        h = str(custom_hex or "").lstrip("#")
        try:
            if len(h) != 6:
                raise ValueError(h)
            return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        except ValueError:
            # A hand-edited or restored value that isn't #rrggbb. This runs on
            # every frame, so raising would leave the indicator blank.
            return CYAN
    if mode == "system":
        return tuple(accent_rgb)
    return CYAN


def _fg(base, intensity, alpha=None):
    """Foreground color, brightened slightly toward white with intensity."""
    t = 0.35 * intensity
    rgb = tuple(base[i] + (1.0 - base[i]) * t for i in range(3))
    return (*rgb, alpha if alpha is not None else 0.82 + 0.18 * intensity)


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# The two orbs: the glowing one, soft gradients (the default), and the crisp
# one, solid shapes that stay sharp on a fractionally scaled monitor.
ORB_STYLES = ("orb", "crisp_orb")


def glow_orb_palette(color):
    """The glowing orb's gradient palette from a single base color.

    Mirrors the classic cyan orb's structure (white-hot core → color → dark
    edge, particles color-graded near→far) so recoloring keeps its look and
    only the hue changes. The classic cyan itself keeps the orb's own
    hand-tuned draw path (see orb_palette_for).
    """
    white, black = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
    return {
        "glow": color,
        "core": [(0.0, white, 1.0),
                 (0.4, mix(color, white, 0.55), 0.9),
                 (0.7, color, 0.6),
                 (0.9, mix(color, black, 0.15), 0.3),
                 (1.0, mix(color, black, 0.15), 0.0)],
        "p_near": mix(color, white, 0.5),
        "p_far": color,
    }


# The classic cyan crisp orb's own colors: a lighter core and dots that cool
# from near-white beside the orb to blue as they move out.
CLASSIC_CRISP_ORB = {"outer": CYAN, "inner": (0.5, 0.9, 1.0),
                     "near": (0.7, 0.95, 1.0), "far": (0.4, 0.6, 1.0)}


def crisp_orb_palette(color):
    """The crisp orb's colors for a base color: the core disc and ring in it,
    a lighter center, and dots from light near the orb to the color itself."""
    white = (1.0, 1.0, 1.0)
    return {"outer": color, "inner": mix(color, white, 0.55),
            "near": mix(color, white, 0.5), "far": color}


# The classic cyan glowing orb's hand-tuned core: a white center fading
# through cyan and blue to a transparent edge, as (stop, rgb, alpha).
_CLASSIC_GLOW_CORE = ((0.0, (1.0, 1.0, 1.0), 1.0), (0.4, (0.5, 0.9, 1.0), 0.9),
                      (0.7, (0.2, 0.6, 0.9), 0.6), (0.9, (0.2, 0.5, 0.8), 0.3),
                      (1.0, (0.2, 0.5, 0.8), 0.0))


@dataclass(frozen=True)
class OrbFrame:
    """What one frame of an orb is drawn from: its size, the screen's scale,
    the voice (orb_level's 0..1 while recording, else 0), whether it is
    recording, and the two phases of its pulse at rest."""
    base_size: float
    scale: float
    level: float
    recording: bool
    pulse_phase: float
    particle_pulse_phase: float


def draw_orb(cr, style, x, y, palette, frame):
    """Draw the orb of *style* (ORB_STYLES), the glowing one or the crisp
    one, centred on (*x*, *y*). *palette* is orb_palette_for's for the style."""
    if style == "crisp_orb":
        _draw_crisp_orb(cr, x, y, palette, frame)
    else:
        _draw_glow_orb(cr, x, y, palette, frame)


def _draw_glow_orb(cr, x, y, palette, frame):
    """Draw the glowing orb with pulsing animation.

    palette=None draws the classic cyan gradient. A palette (from
    glow_orb_palette) recolors the glow, core and particles while keeping
    the same structure.
    """
    # A gentle pulse at rest; recording holds the orb steady.
    if frame.recording:
        current_size = frame.base_size
    else:
        current_size = frame.base_size * (1.0 + 0.1 * math.sin(frame.pulse_phase))

    # Outer glow layers (several, for a softer edge)
    glow_color = palette["glow"] if palette else CYAN
    for i in range(3):
        glow_radius = current_size * (1.5 - i * 0.15)
        gradient = cairo.RadialGradient(x, y, current_size * 0.5, x, y, glow_radius)
        alpha = 0.4 * (1.0 - i * 0.3)
        if frame.recording:
            alpha *= frame.level
        gradient.add_color_stop_rgba(0, glow_color[0], glow_color[1], glow_color[2], alpha)
        gradient.add_color_stop_rgba(1, glow_color[0], glow_color[1], glow_color[2], 0)
        cr.set_source(gradient)
        cr.arc(x, y, glow_radius, 0, 2 * math.pi)
        cr.fill()

    # Core orb with a soft, hazy edge
    core_gradient = cairo.RadialGradient(x, y, 0, x, y, current_size * 1.3)
    stops = palette["core"] if palette else _CLASSIC_GLOW_CORE
    for stop, rgb, a in stops:
        core_gradient.add_color_stop_rgba(stop, rgb[0], rgb[1], rgb[2], a)
    cr.set_source(core_gradient)
    cr.arc(x, y, current_size * 1.3, 0, 2 * math.pi)
    cr.fill()

    # Particles keep to the fixed base_size, not the pulsing size.
    _draw_glow_particles(cr, x, y, frame.base_size * 1.8, palette, frame)


def _draw_glow_particles(cr, center_x, center_y, base_radius, palette, frame):
    """Draw 16 small glowing particles that move out with the voice, from
    0.7x *base_radius* (touching the orb) to 1.4x, shading near to far:
    classic cyan to blue, or a palette's own colors."""
    min_distance, max_distance = 0.7, 1.4
    spread = max_distance - min_distance
    extend = frame.level
    radial_distance = base_radius * (min_distance + spread * extend)
    if palette:
        near, far = palette["p_near"], palette["p_far"]
    else:
        near, far = (0.7, 0.95, 1.0), (0.4, 0.6, 1.0)
    r, g, b = mix(near, far, extend)
    alpha = 0.6 + extend * 0.3 if frame.recording else 0.6
    for i in range(16):
        angle = i * 2 * math.pi / 16
        px = center_x + radial_distance * math.cos(angle)
        py = center_y + radial_distance * math.sin(angle)
        if frame.recording:
            particle_size = 4 + extend * 2
        else:
            # A subtle pulse at rest, each particle a little out of step.
            particle_size = 4 * (1.0 + 0.15 * math.sin(frame.particle_pulse_phase + i * 0.4))
        particle_gradient = cairo.RadialGradient(px, py, 0, px, py, particle_size * 1.5)
        particle_gradient.add_color_stop_rgba(0, r, g, b, alpha)
        particle_gradient.add_color_stop_rgba(1, r * 0.7, g * 0.7, b * 0.7, 0)
        cr.set_source(particle_gradient)
        cr.arc(px, py, particle_size * 1.5, 0, 2 * math.pi)
        cr.fill()


def _draw_crisp_orb(cr, x, y, palette, frame):
    """Draw the crisp orb: a solid core, a ring that widens with the voice
    and 16 dots around it, all with hard edges.

    Shapes that fade to transparent read as out of focus on a scaled
    monitor, so nothing here is a gradient. *palette* holds the colors
    (crisp_orb_palette). At rest the core and ring pulse gently; recording
    holds them still and the voice moves the ring and dots.
    """
    level = frame.level
    pulse = 1.0 if frame.recording else 1.0 + 0.1 * math.sin(frame.pulse_phase)
    size = frame.base_size * pulse
    cr.set_source_rgba(*palette["outer"], 0.35 + 0.5 * level)
    cr.set_line_width(1.5 * frame.scale)
    cr.arc(x, y, size * (1.05 + 0.3 * level), 0, 2 * math.pi)
    cr.stroke()

    cr.set_source_rgb(*palette["outer"])
    cr.arc(x, y, size * 0.85, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(*palette["inner"])
    cr.arc(x, y, size * 0.45, 0, 2 * math.pi)
    cr.fill()

    _draw_crisp_particles(cr, x, y, frame.base_size * 1.8, palette, frame)


def _draw_crisp_particles(cr, center_x, center_y, base_radius, palette, frame):
    """Draw the crisp orb's 16 dots: touching it in silence, out to
    0.88 x *base_radius* at full voice, still inside the pill, shading
    from light near the orb to the orb's color far from it. At rest each
    pulses a little, out of step with the next."""
    level = frame.level
    rgb = mix(palette["near"], palette["far"], level)
    cr.set_source_rgba(*rgb, 0.7 + 0.3 * level)
    distance = base_radius * (0.7 + 0.18 * level)
    for i in range(16):
        angle = i * 2 * math.pi / 16
        if frame.recording:
            radius = (1.6 + 0.8 * level) * frame.scale
        else:
            pulse = 1.0 + 0.15 * math.sin(frame.particle_pulse_phase + i * 0.4)
            radius = 1.6 * frame.scale * pulse
        cr.new_sub_path()
        cr.arc(center_x + distance * math.cos(angle), center_y + distance * math.sin(angle),
               radius, 0, 2 * math.pi)
    cr.fill()


def is_cyan(rgb, tol=0.03):
    """True when a color is (within tolerance) the classic orb cyan.

    Whenever the resolved color is this cyan, the glowing orb draws its
    hand-tuned classic path. The tolerance is needed because the classic cyan
    hex (#4db3ff) parses a hair off the CYAN constant.
    """
    return all(abs(rgb[i] - CYAN[i]) <= tol for i in range(3))


def orb_palette_for(style, rgb):
    """The palette *style*'s orb draws in for a resolved color. The glowing
    orb: None for the classic cyan (its hand-tuned path), else one recolored
    to *rgb*. The crisp orb: the classic cyan's own colors, or ones built
    from *rgb*."""
    if style == "crisp_orb":
        return CLASSIC_CRISP_ORB if is_cyan(rgb) else crisp_orb_palette(rgb)
    return None if is_cyan(rgb) else glow_orb_palette(rgb)


def backing_alpha(r, core, plateau=0.42):
    """Alpha of the backing at normalized radius r (0=center, 1=edge).

    Flat `core` out to `plateau`, then a raised-cosine taper to zero. The
    cosine lands at r=1 with zero slope, so the backing dissolves instead of
    ending in a hard outline.
    """
    if r <= plateau:
        return core
    t = (r - plateau) / (1.0 - plateau)
    return core * (0.5 + 0.5 * math.cos(math.pi * t))


def draw_backing(cr, cx, cy, rx, ry):
    """Feathered dark backing centered at (cx,cy), sized rx×ry.

    Sampled across many stops because cairo draws straight lines between stops,
    and too few show each junction as a faint ring. rx/ry must be chosen so
    the ellipse feathers to zero inside the window, or the window clips it into
    a hard edge.
    """
    cr.save()
    cr.translate(cx, cy)
    cr.scale(rx, ry)
    g = cairo.RadialGradient(0, 0, 0, 0, 0, 1)
    N = 96
    for k in range(N + 1):
        r = k / N
        g.add_color_stop_rgba(r, 0.05, 0.06, 0.09, backing_alpha(r, BACKING_CORE))
    cr.set_source(g)
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.fill()
    cr.restore()


def draw_waveform(cr, w, h, samples, level, color_rgb, sensitivity):
    """The waveform style: the recent samples as one line across the middle."""
    draw_backing(cr, w / 2, h / 2, w * 0.48, h * 0.44)
    if len(samples) == 0:
        return
    iw = w * 0.60
    x0 = (w - iw) / 2
    mid = h / 2
    amp = (h * 0.46 / 2) * 0.9
    cr.new_path()
    for i, value in enumerate(waveform_points(samples, iw, sensitivity)):
        y = mid - value * amp
        cr.line_to(x0 + i, y) if i else cr.move_to(x0 + i, y)
    cr.set_source_rgba(*_fg(color_rgb, min(1.0, level + 0.3)))
    cr.set_line_width(2.6)
    cr.stroke()


def waveform_points(samples, width, sensitivity):
    count = max(1, int(width))
    step = max(1, len(samples) // count)
    return [max(-1.0, min(1.0, float(v) * 3.0 * sensitivity)) for v in samples[::step][:count]]


def draw_bars(cr, w, h, spectrum, color_rgb, sensitivity=1.0):
    """The bars style: one bar per spectrum band, rising from a baseline,
    each band's level scaled by *sensitivity* (Animation response)."""
    draw_backing(cr, w / 2, h / 2, w * 0.49, h * 0.46)
    n = len(spectrum)
    if n == 0:
        return
    iw = w * 0.52
    x0 = (w - iw) / 2
    # At most 4 pixels, and a share of the width below Medium's 200, so the
    # 20 bands keep a positive width at Small.
    gap = min(4, w * 0.02)
    bw = (iw - gap * (n - 1)) / n
    ch = h * 0.40
    baseline = h / 2 + ch / 2
    for i, mag in enumerate(spectrum):
        mag = min(1.0, mag * sensitivity)
        x = x0 + i * (bw + gap)
        bh = max(2, mag * ch)
        cr.set_source_rgba(*_fg(color_rgb, mag, alpha=0.64 + 0.36 * mag))
        cr.rectangle(x, baseline - bh, bw, bh)
        cr.fill()


# Radial geometry, as fractions of the window's shorter side / of the ring.
RADIAL_MAX_R = 0.42      # outer reach of the ring
RADIAL_BASE = 0.42       # resting ring, as a fraction of RADIAL_MAX_R
RADIAL_GAIN = 2.5        # fixed boost, used only when no LevelGain is given (tests, previews)
RADIAL_AUTO_GAIN = 0.8   # with LevelGain: a peak as loud as you've recently been ~ 85% out
RADIAL_BINS = 96
# Where each time slice sits on the ring: a fixed shuffle (seed 7). A regular
# stride looks like a star; a shuffle looks organic.
_RADIAL_SPOTS = list(range(RADIAL_BINS))
random.Random(7).shuffle(_RADIAL_SPOTS)
RADIAL_SMOOTHING = 2     # wrap-around smoothing passes; more = rounder (3 is too round)


class LevelGain:
    """Follows how loud THIS microphone gets, so the radial and the orb suit
    any mic.

    A fixed boost tuned on a loud mic barely moves on a quiet one, even at
    high sensitivity. Like the bars, this learns two levels from the audio
    itself:
      noise   - the room's quiet level. Drops at once to any quieter block and
                creeps up only slowly, so the gaps between words keep it honest.
      ceiling - how loud you've been lately. Jumps up to any louder block and
                halves every 3 seconds. Treated as never below 4x the noise, so
                a silent room is not stretched into movement.
    normalize() then maps a level to 0..1 between the two.

    follow() runs on the audio thread, from one caller at a time, and
    normalize() on the GTK thread; they share only plain floats, so no lock
    is needed.
    """
    NOISE_RISE_PER_SEC = 0.1
    CEILING_HALF_LIFE = 3.0

    def __init__(self):
        self.noise = None
        self.ceiling = 0.0
        self._last = None
        self.peaks = []          # the radial's block peaks this dictation, for the log

    def update(self, samples, now):
        """Follow a block of raw samples by its peak, kept for the log's
        summary (the radial)."""
        p = float(np.max(np.abs(samples))) if len(samples) else 0.0
        self.follow(p, now)
        if len(self.peaks) < 20000:
            self.peaks.append(p)

    def follow(self, p, now):
        """Follow one block's level *p*, measured at *now* (monotonic seconds)."""
        dt = 0.0 if self._last is None else max(0.0, now - self._last)
        self._last = now
        if self.noise is None or p < self.noise:
            self.noise = p
        else:
            self.noise += (p - self.noise) * min(1.0, dt * self.NOISE_RISE_PER_SEC)
        decayed = self.ceiling * 0.5 ** (dt / self.CEILING_HALF_LIFE)
        self.ceiling = max(p, decayed)

    def normalize(self, peak):
        # The 4x-noise floor is applied here, not stored in the ceiling: when a
        # dictation's first block is already speech, the noise briefly reads as
        # that speech, and a stored floor would keep the ring sluggish for seconds.
        noise = self.noise or 0.0
        top = max(self.ceiling, noise * 4, 1e-3)
        quiet = noise * 1.5
        return max(0.0, peak - quiet) / (top - quiet)

    def summary(self):
        """One log line of what this mic delivered, then forget it."""
        if not self.peaks:
            return None
        a = np.sort(np.asarray(self.peaks))
        pick = lambda q: a[min(len(a) - 1, int(q * len(a)))]
        self.peaks = []
        return (f"Radial levels: block peak median {pick(0.5):.4f}, 90% {pick(0.9):.4f}, "
                f"max {a[-1]:.4f}; noise {self.noise or 0:.4f}, ceiling {self.ceiling:.4f}")


def orb_level(gain, level, sensitivity):
    """How far (0..1) the orb's ring and dots move for *level*: scaled to this
    mic's own range by LevelGain *gain*, then by Animation response."""
    return min(1.0, gain.normalize(level) * sensitivity)


def radial_offsets(samples, sensitivity, bins=RADIAL_BINS, gain=None):
    """How far (0..1) each of `bins` points around the ring pushes outward.

    Each slice of the sample window gives its PEAK, not a single raw sample:
    raw samples are small and swing negative half the time, which makes a
    ring jitter inward and barely move. A gentle curve
    (power 0.7) lifts quiet speech more than loud. With a LevelGain the peak is
    first scaled to this mic's own range; without one a fixed boost is used.

    The slices are then spread around the ring out of time order (slice i goes
    to a fixed, shuffled spot). In time order, a syllable starting partway
    through the window makes one side bulge, and the bulge crawls around the
    ring; spread out, the whole shape swells evenly with the voice.
    Smoothing then rounds the spikes off into a wobble.
    """
    n = len(samples)
    if n == 0:
        return []
    peaks = []
    for b in range(bins):
        lo = b * n // bins
        hi = max(lo + 1, (b + 1) * n // bins)
        peak = max((abs(v) for v in samples[lo:hi]), default=0.0)
        level = gain.normalize(peak) * RADIAL_AUTO_GAIN if gain else peak * RADIAL_GAIN
        peaks.append(min(1.0, (level * sensitivity) ** 0.7))
    spots = _RADIAL_SPOTS if bins == RADIAL_BINS else range(bins)
    out = [peaks[spots[i]] for i in range(bins)]
    for _ in range(RADIAL_SMOOTHING):
        out = [(out[i - 1] + 2 * out[i] + out[(i + 1) % bins]) / 4 for i in range(bins)]
    return out


def draw_radial(cr, w, h, samples, level, color_rgb, sensitivity, gain=None):
    """The radial style: a ring around a dot that swells with the voice."""
    cx, cy = w / 2, h / 2
    max_r = min(w, h) * RADIAL_MAX_R
    # 1.15 x the ring keeps the feathered edge inside the window (0.42 * 1.15
    # = 0.483 of the shorter side), so it never gets clipped into a hard edge.
    draw_backing(cr, cx, cy, max_r * 1.15, max_r * 1.15)
    if len(samples) == 0:
        return
    base = max_r * RADIAL_BASE
    offsets = radial_offsets(samples, sensitivity, gain=gain)
    cr.new_path()
    for i, d in enumerate(offsets):
        ang = (i / len(offsets)) * 2 * math.pi - math.pi / 2
        rr = base + d * (max_r - base)
        x, y = cx + math.cos(ang) * rr, cy + math.sin(ang) * rr
        cr.line_to(x, y) if i else cr.move_to(x, y)
    cr.close_path()
    # A faint fill gives the shape body; the stroke thickens a little with size.
    cr.set_source_rgba(*_fg(color_rgb, level, 0.18 + 0.12 * level))
    cr.fill_preserve()
    cr.set_source_rgba(*_fg(color_rgb, min(1.0, level + 0.3)))
    cr.set_line_width(2.2 + max_r * 0.015)
    cr.stroke()
    cr.set_source_rgba(*_fg(color_rgb, level, 0.8))
    cr.arc(cx, cy, base * (0.6 + 0.15 * level), 0, 2 * math.pi)
    cr.fill()
