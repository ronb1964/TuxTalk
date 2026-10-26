"""Cairo drawing for the recording indicator's non-orb styles + the soft backing.

Pure cairo (no GTK widget), so every renderer is testable by drawing onto an
ImageSurface with no display.
"""
import math
import random

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


def _mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def orb_palette(color):
    """Build the orb's gradient palette from a single base color.

    Mirrors the classic cyan orb's structure (white-hot core → color → dark
    edge, particles color-graded near→far) so recoloring keeps its look and
    only the hue changes. The classic cyan itself keeps the orb's own
    hand-tuned draw path (see orb_palette_for).
    """
    white, black = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
    return {
        "glow": color,
        "core": [(0.0, white, 1.0),
                 (0.4, _mix(color, white, 0.55), 0.9),
                 (0.7, color, 0.6),
                 (0.9, _mix(color, black, 0.15), 0.3),
                 (1.0, _mix(color, black, 0.15), 0.0)],
        "p_near": _mix(color, white, 0.5),
        "p_far": color,
    }


def is_cyan(rgb, tol=0.03):
    """True when a color is (within tolerance) the classic orb cyan.

    Whenever the resolved color is this cyan, the orb draws its hand-tuned
    classic path. The tolerance is needed because the classic cyan hex
    (#4db3ff) parses a hair off the CYAN constant.
    """
    return all(abs(rgb[i] - CYAN[i]) <= tol for i in range(3))


def orb_palette_for(rgb):
    """The orb palette for a resolved color, or None to draw the classic path.

    None → the classic cyan orb. A dict → recolor the orb to `rgb`.
    """
    return None if is_cyan(rgb) else orb_palette(rgb)


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
    n = max(1, int(iw))
    step = max(1, len(samples) // n)
    pts = samples[::step][:n]
    cr.new_path()
    for i, v in enumerate(pts):
        y = mid - max(-1.0, min(1.0, v * 3.0 * sensitivity)) * amp
        cr.line_to(x0 + i, y) if i else cr.move_to(x0 + i, y)
    cr.set_source_rgba(*_fg(color_rgb, min(1.0, level + 0.3)))
    cr.set_line_width(2.6)
    cr.stroke()


def draw_bars(cr, w, h, spectrum, color_rgb, sensitivity=1.0):
    """The bars style: one bar per spectrum band, rising from a baseline,
    each band's level scaled by *sensitivity* (Animation response)."""
    draw_backing(cr, w / 2, h / 2, w * 0.49, h * 0.46)
    n = len(spectrum)
    if n == 0:
        return
    iw = w * 0.52
    x0 = (w - iw) / 2
    gap = 4
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
RADIAL_GAIN = 2.5        # fixed boost, used only when no RadialGain is given (tests, previews)
RADIAL_AUTO_GAIN = 0.8   # with RadialGain: a peak as loud as you've recently been ~ 85% out
RADIAL_BINS = 96
# Where each time slice sits on the ring: a fixed shuffle (seed 7). A regular
# stride looks like a star; a shuffle looks organic.
_RADIAL_SPOTS = list(range(RADIAL_BINS))
random.Random(7).shuffle(_RADIAL_SPOTS)
RADIAL_SMOOTHING = 2     # wrap-around smoothing passes; more = rounder (3 is too round)


class RadialGain:
    """Follows how loud THIS microphone gets, so the radial suits any mic.

    A fixed boost tuned on a loud mic barely moves on a quiet one, even at
    high sensitivity. Like the bars, this learns two levels from the audio
    itself:
      noise   - the room's quiet level. Drops at once to any quieter block and
                creeps up only slowly, so the gaps between words keep it honest.
      ceiling - how loud you've been lately. Jumps up to any louder block and
                halves every 3 seconds. Treated as never below 4x the noise, so
                a silent room is not stretched into movement.
    normalize() then maps a peak to 0..1 between the two.

    update() runs on the audio thread and normalize() on the GTK thread; they
    share only plain floats, so no lock is needed.
    """
    NOISE_RISE_PER_SEC = 0.1
    CEILING_HALF_LIFE = 3.0

    def __init__(self):
        self.noise = None
        self.ceiling = 0.0
        self._last = None
        self.peaks = []          # block peaks of the current dictation, for the log

    def update(self, samples, now):
        p = float(np.max(np.abs(samples))) if len(samples) else 0.0
        dt = 0.0 if self._last is None else max(0.0, now - self._last)
        self._last = now
        if self.noise is None or p < self.noise:
            self.noise = p
        else:
            self.noise += (p - self.noise) * min(1.0, dt * self.NOISE_RISE_PER_SEC)
        decayed = self.ceiling * 0.5 ** (dt / self.CEILING_HALF_LIFE)
        self.ceiling = max(p, decayed)
        if len(self.peaks) < 20000:
            self.peaks.append(p)

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


def radial_offsets(samples, sensitivity, bins=RADIAL_BINS, gain=None):
    """How far (0..1) each of `bins` points around the ring pushes outward.

    Each slice of the sample window gives its PEAK, not a single raw sample:
    raw samples are small and swing negative half the time, which makes a
    ring jitter inward and barely move. A gentle curve
    (power 0.7) lifts quiet speech more than loud. With a RadialGain the peak is
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
