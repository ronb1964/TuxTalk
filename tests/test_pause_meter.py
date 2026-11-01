"""Type at pause: the meter, upstream's SegmentedVUMeter, flashing a green
frame where a phrase would be typed. What counts as speech is the voice
detector's call, so no level is marked on it."""
import cairo

from tuxtalk.mic_test import SegmentedVUMeter
from tuxtalk.prefs_type_at_pause import PauseMeter


def pixel(surface, x, y):
    """(r, g, b) of one pixel of an ARGB32 surface, 0-255."""
    surface.flush()
    data = surface.get_data()
    i = y * surface.get_stride() + x * 4
    b, g, r = data[i], data[i + 1], data[i + 2]
    return r, g, b


def test_it_is_the_mic_tests_meter():
    assert issubclass(PauseMeter, SegmentedVUMeter)


def test_show_snapshot_sets_the_level():
    m = PauseMeter()
    m.show_snapshot(0.5)
    assert m.level == 0.5


def test_without_a_cut_nothing_is_marked():
    m = PauseMeter()
    m.show_snapshot(0.7)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 20)
    m._draw_marks(cairo.Context(surface), 200, 20)
    assert all(pixel(surface, x, 10) == (0, 0, 0) for x in range(0, 200, 10))


def test_a_cut_flashes_briefly():
    m = PauseMeter()
    m.show_snapshot(0.3, cut=True)
    assert m.flashing()
    m._flash_until = 0.0
    assert not m.flashing()


def test_a_cut_draws_a_green_frame():
    """The flash must be easy to see: a #04B503 frame round the meter, not a
    faint veil over it."""
    m = PauseMeter()
    m.show_snapshot(0.0, cut=True)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 20)
    cr = cairo.Context(surface)
    m._draw_marks(cr, 200, 20)
    r, g, b = pixel(surface, 100, 0)        # top edge
    assert g > 150 and r < 60 and b < 60
    r, g, b = pixel(surface, 100, 10)       # inside: left alone
    assert (r, g, b) != (255, 255, 255) and g < 150
