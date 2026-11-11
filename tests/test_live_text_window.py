"""The live text window: under the indicator, growing with the dictation."""
import types

import pytest

from tuxtalk import live_text_window as ltw
from tuxtalk import recording_state as rs

SCREEN = types.SimpleNamespace(x=0, y=0, width=1920, height=1080)


def test_it_sits_centred_under_the_indicator():
    assert ltw.place(400, 100, (900, 500, 120, 40), SCREEN) == (760, 548)


def test_with_no_room_below_it_goes_above():
    x, y = ltw.place(400, 300, (900, 900, 120, 40), SCREEN)
    assert y == 900 - ltw._GAP - 300


def test_it_stays_on_the_screen():
    x, y = ltw.place(560, 100, (1880, 20, 30, 30), SCREEN)
    assert x + 560 <= SCREEN.width - ltw._MARGIN


@pytest.fixture
def shown(monkeypatch):
    """A window for a recording that's running; returns (window, recording)."""
    monkeypatch.setattr("tuxtalk.ui_style.is_x11", lambda display: True)
    rec = rs.Recording()
    monkeypatch.setattr(rs.state, "current", rec)
    monkeypatch.setattr(rs.state, "is_recording", True)
    window = ltw.LiveTextWindow(lambda: None)
    yield window, rec
    window._hide_now()


def test_the_words_show_while_the_recording_runs(shown):
    window, rec = shown
    window._show_now("Dear Sam, thanks for the photos", rec)
    assert window._win is not None and window._win.get_visible()


def test_a_read_finishing_after_the_stop_doesnt_bring_it_back(shown, monkeypatch):
    window, rec = shown
    monkeypatch.setattr(rs.state, "is_recording", False)
    window._show_now("too late", rec)
    assert window._win is None


def test_a_read_for_the_last_recording_isnt_shown_in_the_next(shown, monkeypatch):
    window, rec = shown
    monkeypatch.setattr(rs.state, "current", rs.Recording())
    window._show_now("from before", rec)
    assert window._win is None


def test_it_grows_with_the_words_up_to_a_third_of_the_screen(shown):
    """Then the newest lines show and the first ones fade out at the top."""
    window, rec = shown
    window._show_now("A short start.", rec)
    short = window._size[1]
    window._show_now("And then it goes on and on. " * 4, rec)
    longer = window._size[1]
    assert longer > short and window._shift == 0
    window._show_now("And then it goes on and on. " * 400, rec)
    monitor = ltw._monitor_at(ltw.Gdk.Display.get_default(), 0, 0)
    assert window._size[1] == monitor.height // 3 and window._shift > 0


def test_closing_it_lets_the_next_recording_open_a_fresh_one(shown):
    window, rec = shown
    window._show_now("one", rec)
    window._hide_now()
    assert window._win is None
    window._show_now("two", rec)
    assert window._win is not None
