"""The recording indicator lets clicks through to the window underneath.

input_shape_combine_region(None) removes the input shape instead of emptying
it, so the whole transparent indicator window would take the clicks meant for
the app underneath, right where the "follow the window" option puts it.
"""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import recording_indicator as ri  # noqa: E402


def test_the_indicator_is_click_through(monkeypatch):
    shapes = []
    monkeypatch.setattr(Gtk.Widget, "input_shape_combine_region",
                        lambda widget, region: shapes.append(region))
    indicator = ri.RecordingIndicator()
    try:
        indicator.realize()
        assert len(shapes) == 1
        assert shapes[0] is not None and shapes[0].is_empty()
    finally:
        indicator.destroy()
