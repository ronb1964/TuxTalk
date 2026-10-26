"""The recording indicator must accept new settings without being rebuilt.

show_at_position() reads self.position, self.scale and the offsets at show
time rather than at construction, so applying new settings to a running
service is just updating those attributes — the next press of F8 shows the
indicator in the new place. That is what lets Preferences skip the restart.

Also covers the window size: show_at_position() computes int(200 * scale) for
its positioning maths, so the window must be scaled the same way. An unscaled
200x180 window at size="large" (1.4x) would put right- and bottom-anchored
positions roughly 80px away from where they belong.
"""

import pytest


@pytest.fixture
def indicator():
    """A real RecordingIndicator, skipping when there is no display."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    if not Gtk.init_check(None)[0]:
        pytest.skip("no display available")

    from tuxtalk.recording_indicator import RecordingIndicator

    ind = RecordingIndicator(position="center", offset_x=0, offset_y=0, size="medium")
    yield ind
    ind.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()


class TestApplySettings:
    def test_updates_position_and_offsets(self, indicator):
        indicator.apply_settings("top-left", "medium", 50, -10)

        assert indicator.position == "top-left"
        assert indicator.offset_x == 50
        assert indicator.offset_y == -10

    def test_position_is_normalised_to_lower_case(self, indicator):
        """__init__ lower-cases it; apply_settings must agree or matching fails."""
        indicator.apply_settings("TOP-RIGHT", "medium", 0, 0)
        assert indicator.position == "top-right"

    def test_size_change_updates_scale(self, indicator):
        assert indicator.scale == pytest.approx(1.0)

        indicator.apply_settings("center", "large", 0, 0)
        assert indicator.scale == pytest.approx(1.4)

        indicator.apply_settings("center", "small", 0, 0)
        assert indicator.scale == pytest.approx(0.6)

    def test_a_size_change_resizes_the_orb_too(self, indicator):
        """The window grew, and the orb in it stayed its old size until a restart."""
        from gi.repository import Gtk
        indicator.apply_settings("center", "large", 0, 0)
        while Gtk.events_pending():
            Gtk.main_iteration()
        assert indicator.base_size == int(15 * 1.4)

    def test_animation_response_moves_the_orb(self, indicator):
        """The orb, the default style, ignored the slider."""
        indicator.set_audio_level(0.3)
        indicator.apply_settings("center", "medium", 0, 0, sensitivity=2.0)
        assert indicator._orb_level == pytest.approx(0.6)

    def test_unknown_size_falls_back_to_medium(self, indicator):
        """Matches __init__'s behaviour rather than crashing the service loop."""
        indicator.apply_settings("center", "enormous", 0, 0)
        assert indicator.scale == pytest.approx(1.0)


class TestWindowSizeMatchesPositioningMaths:
    """The window must actually be the size the positioning maths assumes."""

    @pytest.mark.parametrize(
        "size,expected_scale", [("small", 0.6), ("medium", 1.0), ("large", 1.4)]
    )
    def test_window_size_tracks_scale_at_construction(self, size, expected_scale):
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        if not Gtk.init_check(None)[0]:
            pytest.skip("no display available")

        from tuxtalk.recording_indicator import RecordingIndicator

        ind = RecordingIndicator(size=size)
        try:
            width, height = ind.get_size_request()
            if width <= 0:
                width, height = ind.get_default_size()

            assert width == int(200 * expected_scale), (
                f"Window is {width}px wide but show_at_position() positions it as "
                f"{int(200 * expected_scale)}px. Right- and bottom-anchored "
                f"positions land off by the difference."
            )
            assert height == int(180 * expected_scale)
        finally:
            ind.destroy()

    def test_window_resizes_when_size_changes(self, indicator):
        from gi.repository import Gtk

        indicator.apply_settings("center", "large", 0, 0)
        while Gtk.events_pending():
            Gtk.main_iteration()

        width, height = indicator.get_size_request()
        if width <= 0:
            width, height = indicator.get_default_size()

        assert width == int(200 * 1.4), (
            "apply_settings changed the scale without resizing the window, so "
            "the positioning maths and the real window disagree again."
        )
        assert height == int(180 * 1.4)


def test_the_window_is_resized_on_the_gtk_thread_only(indicator, monkeypatch):
    """apply_settings runs on the service's input thread while Gtk.main()
    runs on another. Resizing the window there would touch GTK from two
    threads at once, which can crash the service."""
    from gi.repository import Gtk

    calls = []
    monkeypatch.setattr(indicator, "resize", lambda w, h: calls.append((w, h)))

    indicator.apply_settings("center", "large", 0, 0)
    assert calls == [], "resized straight away, on the caller's thread"

    while Gtk.events_pending():
        Gtk.main_iteration()
    assert calls == [(int(200 * 1.4), int(180 * 1.4))]


@pytest.mark.parametrize("desktop, popup", [("COSMIC", True), ("KDE", False), ("GNOME", False)])
def test_the_indicator_never_takes_the_focus_on_cosmic(monkeypatch, desktop, popup):
    """COSMIC focused the ordinary indicator window, and the dictation's
    target lost the keyboard while it showed (Pop!_OS VM, 2026-10-21)."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    if not Gtk.init_check(None)[0]:
        pytest.skip("no display available")
    from tuxtalk.recording_indicator import RecordingIndicator

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    ind = RecordingIndicator(position="center", size="medium")
    try:
        assert (ind.get_property("type") == Gtk.WindowType.POPUP) is popup
    finally:
        ind.destroy()
