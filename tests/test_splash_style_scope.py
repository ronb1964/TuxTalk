"""The splash screen's stylesheet must not restyle other windows.

Bare `window { background-color: transparent }` and `image` rules added
screen-wide and never removed would strip the background from every plain
window the tray opens afterwards (Fix a Word, for one).
"""
import pytest
from conftest import unscoped_selectors
from gi.repository import Gdk, Gtk

from tuxtalk import splash_screen as ss


@pytest.fixture
def splash():
    s = ss.SplashScreen()
    yield s
    Gtk.StyleContext.remove_provider_for_screen(s._screen, s.css_provider)
    s.window.destroy()


def test_every_rule_is_scoped_to_the_splash(splash):
    assert unscoped_selectors(splash.css_provider.to_string(), "tt-splash") == []


def test_the_splash_window_carries_its_class_and_hint(splash):
    assert splash.window.get_style_context().has_class("tt-splash")
    assert splash.window.get_type_hint() == Gdk.WindowTypeHint.SPLASHSCREEN


def test_the_stylesheet_goes_with_the_splash(splash, monkeypatch):
    removed = []
    monkeypatch.setattr(ss.Gtk.StyleContext, "remove_provider_for_screen",
                        lambda screen, provider: removed.append(provider))
    monkeypatch.setattr(splash.window, "set_opacity", lambda o: None)
    done = []
    splash._fade_out(0.05, lambda: done.append(True))
    assert removed == [splash.css_provider]
    assert done == [True]

