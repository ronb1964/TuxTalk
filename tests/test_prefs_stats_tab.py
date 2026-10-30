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


def test_reset_stats_asks_then_starts_from_zero(tab, message_boxes):
    t, _ = tab
    message_boxes.answer = Gtk.ResponseType.OK
    t._on_reset(None)
    assert message_boxes.shown[0].buttons == ["Cancel", "Reset Stats"]
    assert t.cells[("all", "words")].get_text() == "0"


def test_a_reset_that_cannot_delete_the_file_says_so(tab, monkeypatch, message_boxes):
    """A stats file TuxTalk may not delete gave a traceback in the log and
    nothing on screen, with the counts silently still there."""
    t, _ = tab

    def refuse(path):
        raise PermissionError(13, "Permission denied", path)
    monkeypatch.setattr(stats.os, "remove", refuse)
    message_boxes.answer = Gtk.ResponseType.OK
    t._on_reset(None)
    assert [box.title for box in message_boxes.shown] == [
        "Reset your usage stats?", "Your stats couldn't be reset"]


def test_the_typing_speed_box_is_named_by_its_label(tab):
    """A screen reader reads "Your typing speed:" on the number box."""
    t, _ = tab
    labels = [w for w in t.typing_wpm_spin.get_parent().get_children()
              if isinstance(w, Gtk.Label) and w.get_text() == "Your typing speed:"]
    assert labels and labels[0].get_mnemonic_widget() is t.typing_wpm_spin
