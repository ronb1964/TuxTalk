"""The splash screen's stylesheet must not restyle other windows.

Bare `window { background-color: transparent }` and `image` rules added
screen-wide and never removed would strip the background from every plain
window the tray opens afterwards (Fix a Word, for one).
"""
import re

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from tuxtalk import splash_screen as ss  # noqa: E402


@pytest.fixture
def splash():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    s = ss.SplashScreen()
    yield s
    Gtk.StyleContext.remove_provider_for_screen(s._screen, s.css_provider)
    s.window.destroy()


def test_every_rule_is_scoped_to_the_splash(splash):
    css = splash.css_provider.to_string()
    selectors = re.findall(r"^([^{}\n][^{}]*)\{", css, re.MULTILINE)
    assert selectors
    for selector in selectors:
        assert "tt-splash" in selector, selector


def test_the_splash_window_carries_its_class_and_hint(splash):
    assert splash.window.get_style_context().has_class("tt-splash")
    assert splash.window.get_type_hint() == Gdk.WindowTypeHint.SPLASHSCREEN


def _drain():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def test_the_stylesheet_goes_with_the_splash(splash, monkeypatch):
    removed = []
    monkeypatch.setattr(ss.Gtk.StyleContext, "remove_provider_for_screen",
                        lambda screen, provider: removed.append(provider))
    monkeypatch.setattr(splash.window, "set_opacity", lambda o: None)
    monkeypatch.setattr(splash.window, "show_all", lambda: None)
    done = []
    splash.show_and_fade(lambda: done.append(True))
    splash._fade_out(0.05)
    _drain()
    assert removed == [splash.css_provider]
    assert done == [True]


def test_a_splash_closed_while_it_fades_in_still_opens_the_welcome(splash, monkeypatch):
    """The welcome waits on the splash's callback: closed in its first half
    second, first run hung with nothing on screen and the service never
    started."""
    monkeypatch.setattr(splash.window, "show_all", lambda: None)
    done = []
    splash.show_and_fade(lambda: done.append(True))
    splash.window.destroy()
    _drain()
    assert done == [True]
