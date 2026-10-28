"""Preferences, Stats tab: the counts from the stats file, time saved at the
user's typing speed, and Reset Stats."""
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import prefs_stats, stats  # noqa: E402


@pytest.fixture
def tab(monkeypatch):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    saved = {}
    win = SimpleNamespace(config={"typing_wpm": 40}, window=None,
                          update_config=lambda key, value: saved.__setitem__(key, value))
    stats.record(300, 120)                     # 300 words in two minutes, today
    t = prefs_stats.StatsTab(win)
    t.page = t.build()      # held, as the notebook holds it: a dropped page takes its signals
    return t, saved


def test_the_tab_shows_todays_words(tab):
    t, _ = tab
    assert t.cells[("today", "words")].get_text() == "300"
    assert "words a minute" in t.speed.get_text()


def test_a_faster_typist_saves_less_time_and_it_shows_at_once(tab):
    t, saved = tab
    before = t.cells[("today", "saved")].get_text()
    t.typing_wpm_spin.set_value(120)
    assert saved == {"typing_wpm": 120}
    assert t.cells[("today", "saved")].get_text() != before


class _ResetAnswered:
    """A Reset Stats question answered with Reset Stats."""
    def __init__(self, **kw):
        pass

    def format_secondary_text(self, text):
        pass

    def add_button(self, label, response):
        pass

    def run(self):
        return Gtk.ResponseType.OK

    def destroy(self):
        pass


def test_reset_stats_asks_then_starts_from_zero(tab, monkeypatch):
    t, _ = tab
    monkeypatch.setattr(prefs_stats, "message_dialog", _ResetAnswered)
    t._on_reset(None)
    assert t.cells[("all", "words")].get_text() == "0"


def test_a_reset_that_cannot_delete_the_file_says_so(tab, monkeypatch):
    """A stats file TuxTalk may not delete gave a traceback in the log and
    nothing on screen, with the counts silently still there."""
    t, _ = tab
    told = []

    def refuse(path):
        raise PermissionError(13, "Permission denied", path)
    monkeypatch.setattr(stats.os, "remove", refuse)
    monkeypatch.setattr(prefs_stats, "message_dialog", _ResetAnswered)
    monkeypatch.setattr("tuxtalk.ui_style.message_dialog", lambda **kw: pytest.fail("a real box"))
    monkeypatch.setattr(prefs_stats, "show_message",
                        lambda parent, kind, title, text="": told.append(title))
    t._on_reset(None)
    assert told == ["Your stats couldn't be reset"]
