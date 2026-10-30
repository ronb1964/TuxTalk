"""Onboarding screens that scroll must say so.

GTK's scrollbar hides until the mouse moves over it, so the welcome screen
ended neatly at the window's edge and looked finished: new users clicked
"Set it up" without seeing the optional features below (Ron, 2026-10-16).
ui_style.more_below_bar() shows the scrollbar for good and a "More below"
bar while there's more to see.
"""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import ui_style  # noqa: E402


def _scrolled(value, upper, page):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    scrolled = Gtk.ScrolledWindow()
    scrolled.get_vadjustment().configure(value, 0, upper, 10, page * 0.9, page)
    return scrolled


def test_bar_shows_while_there_is_more_below():
    scrolled = _scrolled(0, 1500, 600)
    bar = ui_style.more_below_bar(scrolled)
    assert bar.get_child_visible()
    assert not scrolled.get_overlay_scrolling(), "the scrollbar must always show"


def test_bar_hides_at_the_bottom_and_comes_back():
    scrolled = _scrolled(0, 1500, 600)
    bar = ui_style.more_below_bar(scrolled)
    adj = scrolled.get_vadjustment()
    adj.set_value(900)  # 900 + 600 = the end
    assert not bar.get_child_visible()
    adj.set_value(200)
    assert bar.get_child_visible()


def test_bar_hidden_when_everything_fits():
    scrolled = _scrolled(0, 400, 600)
    assert not ui_style.more_below_bar(scrolled).get_child_visible()


def test_bar_hidden_but_keeps_its_space():
    """Hidden with set_child_visible, not hide(): the window keeps its layout,
    so nothing jumps when you reach the bottom."""
    scrolled = _scrolled(0, 1500, 600)
    bar = ui_style.more_below_bar(scrolled)
    bar.show()
    scrolled.get_vadjustment().set_value(900)
    assert not bar.get_child_visible()
    assert bar.get_visible()


def test_clicking_the_bar_scrolls_down_without_passing_the_end():
    scrolled = _scrolled(0, 1500, 600)
    bar = ui_style.more_below_bar(scrolled)
    adj = scrolled.get_vadjustment()
    bar.clicked()
    assert adj.get_value() == pytest.approx(480)  # most of a page
    bar.clicked()
    bar.clicked()
    assert adj.get_value() == pytest.approx(900)  # stops at the end
    assert not bar.get_child_visible()


def _more_below(texts):
    return [text for text in texts if "More below" in text]


def test_the_welcome_screen_uses_it(welcome_window):
    from gtk_text import shown_text
    assert _more_below(shown_text(welcome_window.dialog))


def test_setup_complete_uses_it_on_a_short_screen(setup_complete, monkeypatch):
    """Its text scrolls only on a screen too short for it: one 480 pixels
    tall here, whatever xvfb-run gives (640x480 on Fedora, 1280x1024 on
    Ubuntu, where everything fits)."""
    monkeypatch.setattr(ui_style, "workarea_height", lambda near=None: int(480 * ui_style.USABLE_SHARE))
    assert _more_below(setup_complete())
