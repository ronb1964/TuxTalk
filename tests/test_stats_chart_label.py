"""The Stats graph labels its tallest bar: "most: 1 word", not "1 words"."""
import datetime
from types import SimpleNamespace

import pytest


class _RecordingCairo:
    """Stands in for the cairo context; keeps what show_text() was given."""

    def __init__(self):
        self.texts = []

    def show_text(self, text):
        self.texts.append(text)

    def text_extents(self, text):
        return SimpleNamespace(width=6 * len(text))

    def __getattr__(self, _name):  # set_source_rgba, rectangle, fill, ...
        return lambda *a, **k: None


def _draw(words_per_day):
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from tuxtalk.prefs_stats import StatsTab
    start = datetime.date(2026, 10, 1)
    owner = SimpleNamespace(_daily=[(start + datetime.timedelta(days=i), w)
                                    for i, w in enumerate(words_per_day)])
    color = SimpleNamespace(red=1, green=1, blue=1)
    widget = SimpleNamespace(
        get_allocated_width=lambda: 560, get_allocated_height=lambda: 120,
        get_style_context=lambda: SimpleNamespace(get_color=lambda _f: color))
    cr = _RecordingCairo()
    StatsTab._draw_chart(owner, widget, cr)
    return [t for t in cr.texts if t.startswith("most:")]


def test_one_word_is_singular():
    assert _draw([0, 1, 0]) == ["most: 1 word"]


def test_more_words_are_plural_with_thousands_separator():
    assert _draw([1200, 3, 0]) == ["most: 1,200 words"]


def test_no_label_on_an_empty_graph():
    assert _draw([0, 0, 0]) == []
