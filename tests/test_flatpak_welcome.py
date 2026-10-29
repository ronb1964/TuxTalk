"""The welcome screen in the Flatpak: the desktop sets the dictation keys.

On a desktop whose portal has no GlobalShortcuts the Flatpak can never have
a dictation key, so the welcome is a stop screen that points to the other
packages. Elsewhere it says how this desktop asks for the keys.
"""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from gtk_text import find, shown_text  # noqa: E402


@pytest.fixture
def flatpak_on(monkeypatch):
    """The Flatpak on the desktop named by the argument."""
    def on(desktop):
        monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    return on


@pytest.fixture
def opened(monkeypatch):
    """The pages Open the Downloads Page asked for, without opening a browser."""
    pages = []
    monkeypatch.setattr(Gtk, "show_uri_on_window", lambda parent, uri, time: pages.append(uri))
    return pages


@pytest.fixture
def xfce_welcome(flatpak_on, request):
    flatpak_on("XFCE")
    return request.getfixturevalue("welcome_window")


def test_a_desktop_without_the_portal_gets_the_stop_screen(xfce_welcome, opened):
    text = " ".join(shown_text(xfce_welcome.dialog))
    assert "The dictation hotkey won’t work on XFCE" in text
    assert xfce_welcome.hotkey_unsupported
    assert xfce_welcome.required_pulse_box in xfce_welcome._pulse_targets()
    find(xfce_welcome.dialog, Gtk.Button, "⬇  Open the Downloads Page").clicked()
    assert opened == ["https://github.com/ronb1964/TuxTalk/releases"]


@pytest.fixture
def kde_welcome(flatpak_on, request):
    flatpak_on("KDE")
    from tuxtalk import flatpak_welcome
    request.getfixturevalue("monkeypatch").setattr(
        flatpak_welcome, "globalshortcuts_available", lambda: True)
    return request.getfixturevalue("welcome_window")


def test_kde_is_told_how_kde_asks_for_the_keys(kde_welcome):
    text = " ".join(shown_text(kde_welcome.dialog))
    assert "To set your keys on KDE Plasma:" in text
    assert "Allow restoring on future sessions" in text
    assert "GNOME" not in text
    assert kde_welcome.required_pulse_box in kde_welcome._pulse_targets()
