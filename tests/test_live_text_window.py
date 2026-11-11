"""The live text window: under the indicator, growing with the dictation."""
import pytest

from tuxtalk import live_text_window as ltw
from tuxtalk import recording_state as rs
from tuxtalk.window_geometry import Rect

SCREEN = Rect(0, 0, 1920, 1080)


def test_it_sits_centred_under_the_indicator():
    assert ltw.place(400, 100, (900, 500, 120, 40), SCREEN) == (760, 548)


def test_with_no_room_below_it_goes_above():
    x, y = ltw.place(400, 300, (900, 900, 120, 40), SCREEN)
    assert y == 900 - ltw._GAP - 300


@pytest.mark.parametrize("anchor", [(1880, 20, 30, 30), (5, 1060, 10, 10), (-50, -50, 0, 0)],
                         ids=["top right", "bottom left", "off the top left"])
def test_it_stays_on_the_screen(anchor):
    x, y = ltw.place(560, 300, anchor, SCREEN)
    assert ltw._MARGIN <= x <= SCREEN.w - ltw._MARGIN - 560
    assert ltw._MARGIN <= y <= SCREEN.h - ltw._MARGIN - 300


def test_with_no_x_display_there_is_no_window(monkeypatch):
    """GTK 3 would make it an ordinary window there, taking the keyboard
    from the app being dictated into."""
    monkeypatch.setattr(ltw, "is_x11", lambda display: False)
    assert ltw.make(lambda: None) is None


@pytest.fixture
def shown(monkeypatch):
    """A window for a recording that's running; returns (window, recording)."""
    monkeypatch.setattr(ltw, "is_x11", lambda display: True)
    rec = rs.Recording()
    monkeypatch.setattr(rs.state, "current", rec)
    monkeypatch.setattr(rs.state, "is_recording", True)
    window = ltw.make(lambda: None)
    yield window, rec
    window._hide_now()


def test_the_words_show_while_the_recording_runs(shown):
    window, rec = shown
    window._show_now("Dear Sam, thanks for the photos", rec, 0)
    assert window._win is not None and window._win.get_visible()


def test_it_never_takes_the_keyboard(shown):
    window, rec = shown
    window._show_now("Dear Sam", rec, 0)
    assert window._win.get_window_type() == ltw.Gtk.WindowType.POPUP
    assert not window._win.get_accept_focus()


def test_a_screen_reader_can_read_the_words(shown):
    window, rec = shown
    window._show_now("Dear Sam, thanks for the photos", rec, 0)
    accessible = window._win.get_accessible()
    assert accessible.get_name() == "Live text"
    assert accessible.get_description() == "Dear Sam, thanks for the photos"


def test_a_read_finishing_after_the_stop_doesnt_bring_it_back(shown, monkeypatch):
    window, rec = shown
    monkeypatch.setattr(rs.state, "is_recording", False)
    window._show_now("too late", rec, 0)
    assert window._win is None


def test_a_read_for_the_last_recording_isnt_shown_in_the_next(shown, monkeypatch):
    window, rec = shown
    monkeypatch.setattr(rs.state, "current", rs.Recording())
    window._show_now("from before", rec, 0)
    assert window._win is None


def test_words_from_a_phrase_already_typed_dont_come_back(shown):
    """Type at pause typed the phrase while GTK was busy: its words are in
    the document now, not still being said."""
    window, rec = shown
    rec.chunks_submitted = 1
    window._show_now("typed already", rec, 0)
    assert window._win is None


def test_it_grows_with_the_words_up_to_a_third_of_the_screen(shown):
    """Then the newest lines show and the first ones fade out at the top."""
    window, rec = shown
    window._show_now("A short start.", rec, 0)
    short = window._size[1]
    window._show_now("And then it goes on and on. " * 4, rec, 0)
    longer = window._size[1]
    assert longer > short and window._shift == 0
    window._show_now("And then it goes on and on. " * 400, rec, 0)
    monitor = ltw.monitor_rect_at(0, 0)
    assert window._size[1] == monitor.h // 3 and window._shift > 0


def test_closing_it_lets_the_next_recording_open_a_fresh_one(shown):
    window, rec = shown
    window._show_now("one", rec, 0)
    window._hide_now()
    assert window._win is None
    window._show_now("two", rec, 0)
    assert window._win is not None
