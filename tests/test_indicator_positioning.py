"""The indicator position control must reflect what actually works.

The indicator positions itself with gtk_window_move(). XWayland honours it and
native Wayland ignores it, and the tray launches the dictation service with
GDK_BACKEND=x11,wayland — so the indicator gets XWayland whenever an X display
exists, on any desktop, with no extension involved. On a KDE Plasma Wayland
session without the extension, top-left lands at (20, 20).

So Preferences must not grey out the position dropdown on Wayland just because
the GNOME extension is absent, or tell the user positioning "requires the
GNOME extension".
"""
import pytest


def test_positioning_available_follows_the_x_display(monkeypatch):
    from tuxtalk.recording_indicator import positioning_available

    monkeypatch.setenv("DISPLAY", ":0")
    assert positioning_available() is True, (
        "An X display is present, so the service gets XWayland and "
        "gtk_window_move() is honoured."
    )

    monkeypatch.delenv("DISPLAY", raising=False)
    assert positioning_available() is False, (
        "With no X display the service falls back to native Wayland, which "
        "ignores gtk_window_move() and centres the indicator."
    )


def test_positioning_does_not_depend_on_the_gnome_extension(monkeypatch):
    """The extension never had anything to do with window positioning."""
    from tuxtalk.recording_indicator import positioning_available

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert positioning_available() is True, (
        "A Wayland session with XWayland available must still allow "
        "positioning — this is exactly Ron's KDE setup, where it was greyed out."
    )


@pytest.mark.parametrize("available", [True, False])
def test_prefs_uses_the_helper_rather_than_guessing_from_the_session(
        prefs_window, monkeypatch, available):
    """Not an `is_wayland and not has_extension` guess: the Position dropdown
    works exactly when positioning_available() says it does."""
    from gi.repository import Gtk
    from gtk_text import beside, find, shown_text
    from tuxtalk import recording_indicator
    monkeypatch.setattr(recording_indicator, "positioning_available", lambda: available)
    window = prefs_window()
    position = beside(find(window.indicator_grid, Gtk.Label, "Position:"))
    assert position.get_sensitive() is available
    warned = [t for t in shown_text(window.indicator_grid) if "XWayland is not available" in t]
    assert bool(warned) is not available


