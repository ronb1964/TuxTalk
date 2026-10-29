"""The indicator works out its color once, not on every frame.

on_draw runs about 60 times a second while recording, too often to build a
fresh Gtk.Label each time to look up the theme accent (which may not even be
used).
"""
import pytest

from tuxtalk import indicator_styles as S
from tuxtalk import recording_indicator as ri


class _Ind:
    _resolved_colors = ri.RecordingIndicator._resolved_colors
    _resolved_color = ri.RecordingIndicator._resolved_color

    def __init__(self, mode, color):
        self.color_mode, self.custom_color = mode, color
        self._color_cache = None


@pytest.fixture
def accent_lookups(monkeypatch):
    lookups = []
    monkeypatch.setattr(ri.ui_style, "desktop_accent_rgb",
                        lambda: lookups.append(1) or (0.2, 0.4, 0.26))
    return lookups


def test_a_custom_color_never_looks_up_the_accent(accent_lookups):
    ind = _Ind("custom", "#ff8000")
    for _ in range(60):
        ind._resolved_color()
    assert accent_lookups == []
    assert ind._resolved_colors() == (S.resolve_color("custom", "#ff8000", None),
                                      S.orb_palette_for(S.resolve_color("custom", "#ff8000", None)))


def test_the_system_accent_is_looked_up_once(accent_lookups):
    ind = _Ind("system", "#ff8000")
    for _ in range(60):
        assert ind._resolved_color() == (0.2, 0.4, 0.26)
    assert accent_lookups == [1]


def test_a_settings_change_is_picked_up_straight_away(accent_lookups):
    ind = _Ind("custom", "#ff8000")
    ind._resolved_color()
    ind.custom_color = "#00ff00"
    assert ind._resolved_color() == (0.0, 1.0, 0.0)
    ind.color_mode = "system"
    assert ind._resolved_color() == (0.2, 0.4, 0.26)
