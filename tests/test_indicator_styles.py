"""Color/backing helpers and the waveform/bars/radial renderers.

Pure cairo — every renderer is exercised by drawing onto a headless
ImageSurface, so these run without a display. Geometry and colors come from
live prototyping.
"""
import numpy as np
import pytest

from tuxtalk import indicator_styles as S


def test_backing_alpha_endpoints_and_monotonic():
    assert S.backing_alpha(0.0, 0.5) == pytest.approx(0.5)
    assert S.backing_alpha(1.0, 0.5) == pytest.approx(0.0, abs=1e-9)
    xs = [i / 50 for i in range(51)]
    vals = [S.backing_alpha(r, 0.5) for r in xs]
    assert all(vals[i] >= vals[i + 1] - 1e-9 for i in range(len(vals) - 1))


def test_resolve_color_system_uses_accent():
    assert S.resolve_color("system", "#48b7f5", (0.2, 0.4, 0.26)) == (0.2, 0.4, 0.26)


def test_resolve_color_custom_parses_hex():
    r, g, b = S.resolve_color("custom", "#ff8000", (0, 0, 0))
    assert r == pytest.approx(1.0, abs=0.01)
    assert g == pytest.approx(0.5, abs=0.02)
    assert b == pytest.approx(0.0, abs=0.01)


def test_resolve_color_cyan_mode():
    assert S.resolve_color("cyan", "#ff8000", (0.2, 0.4, 0.26)) == S.CYAN


def test_is_cyan_matches_the_classic_color_and_its_hex():
    """The classic orb colors are keyed to the color itself, not a mode name:
    is_cyan must recognize the CYAN constant and the classic cyan hex (which
    parses to a hair off CYAN) as cyan, and reject other colors."""
    assert S.is_cyan(S.CYAN)
    assert S.is_cyan(S.resolve_color("custom", "#4db3ff", (0, 0, 0)))
    assert not S.is_cyan((1.0, 0.0, 0.0))
    assert not S.is_cyan((0.2, 0.4, 0.26))   # a green accent


def test_the_classic_cyan_orbs_keep_their_own_colors():
    """The glowing orb draws its hand-tuned gradient (no palette), the crisp
    one its classic colors."""
    cyan = S.resolve_color("custom", "#4db3ff", (0, 0, 0))
    assert S.orb_palette_for("orb", cyan) is None
    assert S.orb_palette_for("crisp_orb", cyan) is S.CLASSIC_CRISP_ORB


def test_another_color_recolors_the_whole_crisp_orb():
    red = (0.9, 0.2, 0.2)
    pal = S.orb_palette_for("crisp_orb", red)
    assert pal["outer"] == pal["far"] == red
    for lighter in (pal["inner"], pal["near"]):
        assert all(c > base for c, base in zip(lighter, red, strict=True))


def test_another_color_recolors_the_glowing_orb_keeping_its_white_core():
    red = (0.9, 0.2, 0.2)
    pal = S.orb_palette_for("orb", red)
    assert pal["glow"] == pal["p_far"] == red
    assert pal["core"][0][1] == (1.0, 1.0, 1.0)


def _ink(draw):
    """How many pixels *draw* put the style's blue on: the dark backing
    behind every style doesn't count."""
    cairo = pytest.importorskip("cairo")
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 120)
    draw(cairo.Context(surf))
    surf.flush()
    pixels = np.frombuffer(surf.get_data(), np.uint8).reshape(120, surf.get_stride() // 4, 4)
    return int((pixels[:, :200, 0] > 40).sum())     # ARGB32 keeps blue first


def _has_ink(draw):
    return _ink(draw) > 0


def _wave():
    return (0.3 * np.sin(np.linspace(0, 20, 800))).astype(np.float32)


def test_waveform_draws_something():
    assert _has_ink(lambda cr: S.draw_waveform(cr, 200, 120, _wave(), 0.6, (0.3, 0.7, 0.96), 1.0))


def test_bars_draw_something():
    spec = np.linspace(0.1, 1.0, 20).astype(np.float32)
    assert _has_ink(lambda cr: S.draw_bars(cr, 200, 120, spec, (0.3, 0.7, 0.96)))


class _Recording:
    """A cairo context that also notes each rectangle drawn on it."""

    def __init__(self, cr):
        self._cr, self.rectangles = cr, []

    def rectangle(self, *rect):
        self.rectangles.append(rect)
        self._cr.rectangle(*rect)

    def __getattr__(self, name):
        return getattr(self._cr, name)


def test_bars_at_the_small_size_are_real_bars():
    """Small is 120 wide: a fixed 4-pixel gap between the 20 bands left
    each bar narrower than nothing, thin slivers drawn backwards."""
    cairo = pytest.importorskip("cairo")
    cr = _Recording(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 120, 72)))
    S.draw_bars(cr, 120, 72, [0.5] * 20, (0.3, 0.7, 0.96))
    widths = [width for _x, _y, width, _h in cr.rectangles[-20:]]
    assert len(cr.rectangles) >= 20 and min(widths) > 0


def test_bars_at_medium_and_large_keep_their_4_pixel_gaps():
    cairo = pytest.importorskip("cairo")
    for width in (200, 280):
        cr = _Recording(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, width, 120)))
        S.draw_bars(cr, width, 120, [0.5] * 20, (0.3, 0.7, 0.96))
        (x0, _y, w0, _h), (x1, *_rest) = cr.rectangles[-20:-18]
        assert x1 - (x0 + w0) == pytest.approx(4)


def test_animation_response_moves_the_bars():
    """The slider changed the waveform and the ring, never the bars."""
    spec = np.full(20, 0.3, dtype=np.float32)
    calm = _ink(lambda cr: S.draw_bars(cr, 200, 120, spec, (0.3, 0.7, 0.96), 1.0))
    lively = _ink(lambda cr: S.draw_bars(cr, 200, 120, spec, (0.3, 0.7, 0.96), 2.0))
    assert lively > calm


def test_radial_draws_something():
    assert _has_ink(lambda cr: S.draw_radial(cr, 200, 120, _wave(), 0.6, (0.3, 0.7, 0.96), 1.0))


def test_empty_data_does_not_crash():
    # The service may draw a frame before any audio has arrived.
    _has_ink(lambda cr: S.draw_waveform(cr, 200, 120, [], 0.0, (0.3, 0.7, 0.96), 1.0))
    _has_ink(lambda cr: S.draw_bars(cr, 200, 120, [], (0.3, 0.7, 0.96)))
    _has_ink(lambda cr: S.draw_radial(cr, 200, 120, [], 0.0, (0.3, 0.7, 0.96), 1.0))


# --- Radial: big enough to see, and moving with ordinary speech ---

def _speech(peak, n=1024):
    """A speech-like window: a 220 Hz tone whose loudness swells to `peak`."""
    t = np.arange(n) / 16000
    return list(np.sin(2 * np.pi * 220 * t) * peak * (0.5 + 0.5 * np.sin(2 * np.pi * 7 * t)))


def test_radial_moves_well_for_normal_speech_at_default_sensitivity():
    # Speech peaks around 0.15, and at default sensitivity that must move the
    # ring well, without turning sensitivity almost all the way up.
    offs = S.radial_offsets(_speech(0.15), 1.0)
    assert len(offs) == S.RADIAL_BINS
    assert sum(offs) / len(offs) > 0.35


def test_radial_rests_in_silence_and_stays_in_range():
    assert max(S.radial_offsets([0.0] * 1024, 1.0)) == 0.0
    loud = S.radial_offsets(_speech(0.9), 2.0)
    assert all(0.0 <= d <= 1.0 for d in loud)


def test_radial_short_window_does_not_crash():
    # Fewer samples than points on the ring, e.g. the first audio block.
    assert len(S.radial_offsets([0.1] * 10, 1.0)) == S.RADIAL_BINS


def test_radial_fills_its_window():
    # Loud speech reaches well past a third of the window's shorter side, so
    # even "small" is more than a speck.
    import cairo
    w, h = 200, 180
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    S.draw_radial(cr, w, h, _speech(0.4), 1.0, (0.3, 0.7, 0.96), 1.0)
    # The ring's blue, not the dark backing around it (ARGB32 keeps blue first).
    blue = np.frombuffer(surf.get_data(), np.uint8).reshape(h, surf.get_stride() // 4, 4)[:, :w, 0]
    ys, xs = np.nonzero(blue > 40)
    reach = np.max(np.hypot(xs - w / 2, ys - h / 2))
    assert reach > 0.35 * min(w, h)
    assert reach <= 0.5 * min(w, h)   # and never past the window's edge


def _run_gain(scale, seconds=2.0):
    """Feed a gain ~64 ms blocks of speech-like audio (with gaps) at `scale`."""
    g = S.LevelGain()
    t = 0.0
    for k in range(int(seconds / 0.064)):
        talking = (k % 8) < 6                      # short gaps, like between words
        block = np.array(_speech(scale if talking else scale * 0.02))
        g.update(block, t)
        t += 0.064
    return g


def test_radial_gain_makes_a_quiet_mic_move_like_a_loud_one():
    # A quiet mic must not make its user almost yell, even at high sensitivity.
    quiet, loud = _run_gain(0.03), _run_gain(0.3)
    avg = lambda g, pk: sum(S.radial_offsets(_speech(pk), 1.0, gain=g)) / S.RADIAL_BINS
    q, l = avg(quiet, 0.03), avg(loud, 0.3)
    assert q > 0.35
    assert abs(q - l) < 0.05


def test_radial_gain_keeps_a_silent_room_still():
    g = S.LevelGain()
    for k in range(40):
        g.update(np.random.default_rng(k).normal(0, 0.002, 1024), k * 0.064)
    room = np.random.default_rng(99).normal(0, 0.002, 1024)
    assert sum(S.radial_offsets(list(room), 1.0, gain=g)) / S.RADIAL_BINS < 0.15


def test_radial_gain_summary_reports_and_resets():
    g = _run_gain(0.05)
    assert g.summary().startswith("Radial levels:")
    assert g.summary() is None


def test_radial_gain_recovers_when_dictation_starts_mid_word():
    # The first block can already be speech; it must not leave the ring dull.
    g = S.LevelGain()
    g.update(np.array(_speech(0.03)), 0.0)
    g.update(np.array(_speech(0.03 * 0.02)), 0.064)   # first gap between words
    assert g.normalize(0.03) > 0.9


def _orb_after(levels):
    """The orb's movement for the last of *levels*, one block each 64 ms."""
    g = S.LevelGain()
    for k, level in enumerate(levels):
        g.follow(level, k * 0.064)
    return S.orb_level(g, levels[-1], 1.0)


def _talking(word_level, room_level, blocks=60):
    return [word_level if k % 8 < 6 else room_level for k in range(blocks)]


def test_the_orb_moves_as_far_on_a_quiet_mic_as_on_a_loud_one():
    """A dynamic mic speaking at about 1.5% of the fixed scale used to keep
    the orb all but still."""
    quiet = _orb_after(_talking(0.015, 0.0003))
    loud = _orb_after(_talking(0.8, 0.02))
    assert quiet > 0.9
    assert quiet == pytest.approx(loud, abs=0.05)


def test_the_orb_stays_still_in_a_silent_room():
    rng = np.random.default_rng(3)
    assert _orb_after(list(rng.uniform(0.0002, 0.0004, 60))) < 0.15


def test_a_malformed_custom_color_falls_back_to_cyan():
    """It is drawn every frame, so raising would leave the indicator blank."""
    for bad in ("red", "#fff", "", "#12345g", None):
        assert S.resolve_color("custom", bad, (0, 0, 0)) == S.CYAN


def test_the_waveform_keeps_one_point_per_pixel_within_range():
    points = S.waveform_points(np.linspace(-1, 1, 1024), 120, 1.0)
    assert len(points) == 120
    assert min(points) == -1.0 and max(points) == 1.0


def test_the_waveform_follows_sensitivity():
    quiet = S.waveform_points([0.1] * 10, 10, 1.0)
    assert quiet[0] == pytest.approx(0.3)
    assert S.waveform_points([0.1] * 10, 10, 2.0)[0] == pytest.approx(0.6)
