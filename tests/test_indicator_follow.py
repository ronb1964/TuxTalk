"""TuxTalk Labs: the recording indicator follows the window you're dictating
into, centred near its bottom edge. Uses the same active-window position as
the floating messages (window_geometry).
"""
import types

import pytest

from tuxtalk import config, recording_indicator as ri, window_geometry as wg
from tuxtalk.window_geometry import Rect


# --- where the active window is, as a rectangle --------------------------------------

def test_the_rect_maps_like_the_point_on_a_scaled_screen():
    monitors = [("DP-1", Rect(0, 0, 3840, 2160))]
    assert wg.to_screen_rect(Rect(100, 100, 400, 200), "DP-1", Rect(0, 0, 1920, 1080), monitors) \
        == Rect(200, 200, 800, 400)


def test_the_rect_is_one_to_one_without_screen_sizes():
    assert wg.to_screen_rect(Rect(10, 20, 30, 40), "", None, []) == Rect(10, 20, 30, 40)


def test_the_active_window_rect_follows_the_same_sources(monkeypatch):
    monkeypatch.setattr(wg, "screen_monitors", lambda: [("DP-6", Rect(0, 0, 3440, 1440))])
    monkeypatch.setattr(wg, "_reported", lambda: "100,200,800,600|DP-6|0,0,3440,1440")
    assert wg.active_window_rect() == Rect(100, 200, 800, 600)


# --- where the indicator goes ----------------------------------------------------------------

def test_centred_near_the_bottom_of_the_window():
    pos = ri.follow_position(Rect(100, 200, 800, 600), 200, 80, Rect(0, 0, 3440, 1440))
    assert pos == (400, 200 + 600 - 80 - ri.FOLLOW_MARGIN)


def test_kept_on_the_monitor():
    pos = ri.follow_position(Rect(3300, 1300, 300, 300), 200, 80, Rect(0, 0, 3440, 1440))
    assert pos == (3240, 1360)


def test_a_tiny_window_still_gets_the_indicator_inside_its_monitor():
    pos = ri.follow_position(Rect(0, 0, 50, 30), 200, 80, Rect(0, 0, 1920, 1080))
    assert pos == (0, 0)


class _Ind:
    """Just enough of RecordingIndicator for _follow_target."""
    follow_window = True
    position = "bottom-center"
    _follow_target = ri.RecordingIndicator._follow_target


def test_the_indicator_asks_for_the_window_when_following(monkeypatch):
    monkeypatch.setattr(wg, "active_window_rect", lambda: Rect(100, 200, 800, 600))
    monkeypatch.setattr(ri, "_monitor_rect_at", lambda x, y: Rect(0, 0, 3440, 1440))
    assert _Ind()._follow_target(200, 80) == ri.follow_position(Rect(100, 200, 800, 600), 200, 80,
                                                                 Rect(0, 0, 3440, 1440))


def test_no_window_known_falls_back_to_the_usual_position(monkeypatch):
    monkeypatch.setattr(wg, "active_window_rect", lambda: None)
    assert _Ind()._follow_target(200, 80) is None


def test_switched_off_never_asks(monkeypatch):
    monkeypatch.setattr(wg, "active_window_rect", lambda: pytest.fail("asked"))
    ind = _Ind()
    ind.follow_window = False
    assert ind._follow_target(200, 80) is None


# --- the setting ------------------------------------------------------------------------------

def test_off_by_default_and_applied_without_a_restart():
    assert config.Settings().indicator_follow_window is False
    assert "indicator_follow_window" in config.LIVE_APPLIED_KEYS


def test_the_service_passes_it_to_the_indicator(service_main):
    started = service_main(recording_indicator=True, indicator_follow_window=True)
    options = next(step[1] for step in started if step[0] == "shows the indicator")
    assert options["follow_window"] is True


def test_a_changed_setting_reaches_the_running_indicator(monkeypatch):
    from tuxtalk import app
    applied = []
    indicator = types.SimpleNamespace(apply_settings=lambda *a, **k: applied.append(k))
    monkeypatch.setattr(app, "load_config", lambda: config.Settings(indicator_follow_window=True))
    app._reload_live_settings(config.Settings(), indicator)
    assert applied[0]["follow_window"] is True


def test_it_is_a_labs_switch(prefs_window):
    from gi.repository import Gtk
    from gtk_text import below, find
    window = prefs_window()
    labs = below(find(window.window, Gtk.Label, "🧪 TuxTalk Labs"))
    switch = next(w for w in labs if isinstance(w, Gtk.CheckButton)
                  and w.get_label() == "Follows the window you\u2019re dictating into")
    assert not switch.get_active()
    switch.set_active(True)
    assert window.config["indicator_follow_window"] is True


def test_the_labs_help_page_explains_it():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from tuxtalk import help_dialog
    dialog = help_dialog.show_help_dialog(tab="start")
    try:
        def walk(w):
            yield w
            if isinstance(w, Gtk.Container):
                for c in w.get_children():
                    yield from walk(c)
        notebook = next(w for w in walk(dialog) if isinstance(w, Gtk.Notebook))
        page = notebook.get_nth_page(notebook.get_current_page())
        text = " ".join(w.get_text() for w in walk(page) if isinstance(w, Gtk.Label))
        assert "Indicator Follows Your Window" in text
    finally:
        dialog.destroy()


# --- the Audio tab's position, inside the window ---------------------------------------------

WIN = Rect(1000, 200, 800, 600)
MON = Rect(0, 0, 3440, 1440)


@pytest.mark.parametrize("position, expected", [
    ("bottom-center", (1000 + 300, 200 + 600 - 80 - ri.FOLLOW_MARGIN)),
    ("center", (1300, 200 + 260)),
    ("top-left", (1000 + ri.FOLLOW_SIDE, 200 + ri.FOLLOW_TOP)),
    ("top-right", (1000 + 800 - 200 - ri.FOLLOW_SIDE, 200 + ri.FOLLOW_TOP)),
    ("bottom-left", (1000 + ri.FOLLOW_SIDE, 200 + 600 - 80 - ri.FOLLOW_MARGIN)),
    ("right-center", (1000 + 800 - 200 - ri.FOLLOW_SIDE, 460)),
    ("something-unknown", (1300, 460)),
])
def test_the_audio_tab_position_applies_inside_the_window(position, expected):
    assert ri.follow_position(WIN, 200, 80, MON, position) == expected


def test_the_indicator_uses_its_configured_position_when_following(monkeypatch):
    monkeypatch.setattr(wg, "active_window_rect", lambda: WIN)
    monkeypatch.setattr(ri, "_monitor_rect_at", lambda x, y: MON)
    ind = _Ind()
    ind.position = "top-right"
    assert ind._follow_target(200, 80) == ri.follow_position(WIN, 200, 80, MON, "top-right")
