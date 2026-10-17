"""Type at pause: the meter, upstream's SegmentedVUMeter with the room level
and the speech threshold marked on it."""
import cairo

from tuxtalk.prefs import PauseMeter, SegmentedVUMeter


def pixel(surface, x, y):
    """(r, g, b) of one pixel of an ARGB32 surface, 0-255."""
    surface.flush()
    data = surface.get_data()
    i = y * surface.get_stride() + x * 4
    b, g, r = data[i], data[i + 1], data[i + 2]
    return r, g, b


def test_it_is_the_mic_tests_meter():
    assert issubclass(PauseMeter, SegmentedVUMeter)


def test_show_snapshot_sets_level_and_threshold():
    m = PauseMeter()
    m.show_snapshot(0.5, 0.75)
    assert m.level == 0.5 and m.threshold == 0.75


def test_only_the_threshold_is_marked():
    """The room needs no mark: in silence the meter itself shows it."""
    m = PauseMeter()
    m.show_snapshot(0.0, 0.75)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 20)
    cr = cairo.Context(surface)
    m._draw_marks(cr, 200, 20)
    r, g, b = pixel(surface, 150, 10)       # the threshold mark: #04B503
    assert g > 150 and r < 60 and b < 60
    assert pixel(surface, 50, 10) == (0, 0, 0)   # nothing drawn elsewhere


def test_no_threshold_no_mark():
    m = PauseMeter()
    m.show_snapshot(0.0, 0.0)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 20)
    m._draw_marks(cairo.Context(surface), 200, 20)
    assert pixel(surface, 0, 10) == (0, 0, 0)


def test_a_cut_flashes_briefly():
    m = PauseMeter()
    m.show_snapshot(0.3, 0.2, cut=True)
    assert m.flashing()
    m._flash_until = 0.0
    assert not m.flashing()


def test_a_cut_draws_a_green_frame():
    """The flash must be easy to see: a #04B503 frame round the meter, not a
    faint veil over it."""
    m = PauseMeter()
    m.show_snapshot(0.0, 0.0, cut=True)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 20)
    cr = cairo.Context(surface)
    m._draw_marks(cr, 200, 20)
    r, g, b = pixel(surface, 100, 0)        # top edge
    assert g > 150 and r < 60 and b < 60
    r, g, b = pixel(surface, 100, 10)       # inside: left alone
    assert (r, g, b) != (255, 255, 255) and g < 150
