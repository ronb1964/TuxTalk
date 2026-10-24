"""Usage stats: how much you've dictated, kept as daily counts only.

TuxTalk promises dictated text never reaches the disk, so stats store
numbers per day (dictations, words, seconds of speech) and nothing else.
"""
import datetime
import json

import pytest

from tuxtalk import stats

D = datetime.date


@pytest.fixture
def stats_file(tmp_path, monkeypatch):
    path = tmp_path / "stats.json"
    monkeypatch.setattr(stats, "stats_path", lambda: str(path))
    return path


# --- counting words ----------------------------------------------------------

@pytest.mark.parametrize("text, n", [
    ("Hello world.", 2),
    ("I'm here, it's fine!", 4),                 # contractions are one word
    ("Order 4 sheets of 3/4 plywood", 7),        # numbers count
    ("  ", 0),
    ("", 0),
    ("line one\xa7SHIFT_ENTER\xa7line two", 4),  # spoken new-line marker isn't a word
    ("smart “quotes” — and dashes", 4),   # the dash is not a word
    ("今日はいい天気", 7),            # Japanese: one per character
    ("我爱你", 3),                                    # Chinese
    ("안녕하세요 세계", 2),           # Korean spaces its words
    ("TalkTypeは速い", 4),                            # 1 English word + 3 characters
])
def test_count_words(text, n):
    assert stats.count_words(text) == n


# --- recording ---------------------------------------------------------------

def test_record_adds_up_per_day_and_stores_no_text(stats_file):
    stats.record(5, 2.0, today=D(2026, 10, 2))
    stats.record(10, 4.5, today=D(2026, 10, 2))
    stats.record(3, 1.0, today=D(2026, 10, 3))

    data = json.loads(stats_file.read_text())
    assert data["days"] == {
        "2026-10-02": {"dictations": 2, "words": 15, "seconds": 6.5},
        "2026-10-03": {"dictations": 1, "words": 3, "seconds": 1.0},
    }
    assert set(data) == {"version", "days"}


def test_record_ignores_empty_dictations(stats_file):
    stats.record(0, 3.0, today=D(2026, 10, 2))
    assert not stats_file.exists()


def test_record_never_raises_and_keeps_a_damaged_file(stats_file):
    stats_file.write_text("{not json")
    stats.record(5, 2.0, today=D(2026, 10, 2))      # must not raise
    assert stats_file.read_text() == "{not json"     # and must not wipe it


def test_load_survives_missing_and_odd_files(stats_file):
    assert stats.load() == {}
    stats_file.write_text('{"version": 1, "days": {"2026-10-02": {"words": "x"}, "bad": 3}}')
    days = stats.load()
    assert days == {"2026-10-02": {"dictations": 0, "words": 0, "seconds": 0.0}}


def test_reset_removes_everything(stats_file):
    stats.record(5, 2.0, today=D(2026, 10, 2))
    stats.reset()
    assert stats.load() == {}


# --- the numbers Preferences shows --------------------------------------------

def test_time_saved_compares_typing_with_speaking():
    # 400 words take 10 minutes to type at 40 wpm; spoken in 3 minutes.
    assert stats.time_saved(400, 180) == 600 - 180
    assert stats.time_saved(10, 60) == 0             # never negative


def test_summary_today_last_7_days_and_all_time(stats_file):
    today = D(2026, 10, 10)
    stats.record(100, 40, today=today)
    stats.record(50, 20, today=today - datetime.timedelta(days=6))   # inside 7 days
    stats.record(30, 10, today=today - datetime.timedelta(days=7))   # outside

    s = stats.summary(stats.load(), today)
    assert (s["today"]["words"], s["today"]["dictations"]) == (100, 1)
    assert s["week"]["words"] == 150
    assert s["all"]["words"] == 180 and s["all"]["dictations"] == 3
    assert s["all"]["seconds"] == 70
    assert s["all"]["saved"] == stats.time_saved(180, 70)
    assert s["wpm"] == round(180 / (70 / 60))
    assert s["first_day"] == today - datetime.timedelta(days=7)


def test_summary_daily_words_cover_the_last_14_days_oldest_first(stats_file):
    today = D(2026, 10, 10)
    stats.record(7, 3, today=today)
    stats.record(4, 2, today=today - datetime.timedelta(days=13))
    stats.record(9, 2, today=today - datetime.timedelta(days=14))     # too old for the chart

    daily = stats.summary(stats.load(), today)["daily"]
    assert len(daily) == 14
    assert daily[0] == (today - datetime.timedelta(days=13), 4)
    assert daily[-1] == (today, 7)
    assert sum(w for _d, w in daily) == 11


def test_streak_counts_days_in_a_row_up_to_today_or_yesterday(stats_file):
    today = D(2026, 10, 10)
    for back in (1, 2, 3, 5):
        stats.record(1, 1, today=today - datetime.timedelta(days=back))
    # Nothing yet today: a streak running through yesterday still counts.
    assert stats.summary(stats.load(), today)["streak"] == 3
    stats.record(1, 1, today=today)
    assert stats.summary(stats.load(), today)["streak"] == 4


def test_empty_summary_is_all_zero(stats_file):
    s = stats.summary({}, D(2026, 10, 10))
    assert s["all"] == {"dictations": 0, "words": 0, "seconds": 0.0, "saved": 0}
    assert s["wpm"] is None and s["streak"] == 0 and s["first_day"] is None


@pytest.mark.parametrize("seconds, text", [
    (0, "0 min"),
    (25, "under a minute"),
    (60, "1 min"),
    (59 * 60, "59 min"),
    (3600, "1 hr"),
    (3600 + 25 * 60, "1 hr 25 min"),
    (50 * 3600, "50 hr"),
])
def test_format_duration(seconds, text):
    assert stats.format_duration(seconds) == text


# --- the dictation service counts each dictation -----------------------------

def _dictate(monkeypatch, app, seconds=2.0):
    import numpy as np
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, language: "hello there friend")
    monkeypatch.setattr(app, "_prepare_text", lambda raw, *a, **k: "Hello there, friend. ")
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_inject_text", lambda text, mode: None)
    monkeypatch.setattr("tuxtalk.history.add_entry", lambda text: None)
    frames = [(np.ones(int(app.SAMPLE_RATE * seconds), dtype=np.int16) * 1000).tobytes()]
    app._transcribe_and_inject(frames, app.SAMPLE_RATE, beeps_on=False,
                               smart_quotes=False, notify_on=False)


def test_a_dictation_is_counted_with_its_words_and_speaking_time(stats_file, monkeypatch):
    from tuxtalk import app
    monkeypatch.setattr(app, "_usage_stats", True)
    _dictate(monkeypatch, app, seconds=2.0)

    today = stats.load()[datetime.date.today().isoformat()]
    assert today == {"dictations": 1, "words": 3, "seconds": 2.0}
    assert "friend" not in stats_file.read_text()        # numbers only, never text


def test_turning_stats_off_stops_counting(stats_file, monkeypatch):
    from tuxtalk import app
    monkeypatch.setattr(app, "_usage_stats", False)
    _dictate(monkeypatch, app)
    assert not stats_file.exists()


def test_usage_stats_setting_applies_without_a_restart():
    from tuxtalk import config
    assert config.Settings().usage_stats is True
    assert "usage_stats" in config.LIVE_APPLIED_KEYS


def test_preferences_finds_tabs_by_name_not_position():
    """A Stats tab sits before Updates, so --tab=updates and bring_to_front
    find tabs by name; a hard-coded position (4) would open Stats instead."""
    import types
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from tuxtalk.prefs import PreferencesWindow

    notebook = Gtk.Notebook()
    for name in ("General", "Audio", "Advanced", "Commands", "Stats", "Updates & Backup"):
        page = Gtk.Box()
        page.show()          # GtkNotebook won't switch to a hidden page
        notebook.append_page(page, Gtk.Label(label=name))
    window = types.SimpleNamespace(notebook=notebook)

    assert PreferencesWindow.select_tab(window, "updates") is True
    assert notebook.get_current_page() == 5
    assert PreferencesWindow.select_tab(window, "Stats") is True
    assert notebook.get_current_page() == 4
    assert PreferencesWindow.select_tab(window, "nope") is False


def test_saving_keeps_no_backup_and_reset_clears_an_old_one(stats_file):
    stats.record(5, 2.0, today=D(2026, 10, 2))
    stats.record(5, 2.0, today=D(2026, 10, 2))
    assert not (stats_file.parent / "stats.json.bak").exists()
    assert [p.name for p in stats_file.parent.iterdir()] == ["stats.json"]   # no temp left

    (stats_file.parent / "stats.json.bak").write_text("old counts")   # from an earlier build
    stats.reset()
    assert not stats_file.exists()
    assert not (stats_file.parent / "stats.json.bak").exists()


def test_a_damaged_file_is_reported_not_shown_as_empty(stats_file):
    assert stats.is_damaged() is False                   # no file yet is fine
    stats_file.write_text("{not json")
    assert stats.is_damaged() is True
    stats.reset()
    assert stats.is_damaged() is False


def test_stats_are_saved_after_the_text_is_typed(stats_file, monkeypatch):
    """Saving the counts must never delay the dictated text."""
    import numpy as np
    from tuxtalk import app
    order = []
    monkeypatch.setattr(app, "_usage_stats", True)
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, language: "hello there")
    monkeypatch.setattr(app, "_prepare_text", lambda raw, *a, **k: "Hello there. ")
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.history.add_entry", lambda text: None)
    monkeypatch.setattr(app, "_inject_text", lambda text, mode: order.append("typed"))
    monkeypatch.setattr(stats, "record", lambda words, seconds: order.append("counted"))
    # A full second: a near-silent 0.1 s would be dropped as an accidental tap
    # (silence.is_speechless_tap) before reaching the engine.
    frames = [(np.ones(app.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    app._transcribe_and_inject(frames, app.SAMPLE_RATE, beeps_on=False,
                               smart_quotes=False, notify_on=False)
    assert order == ["typed", "counted"]


def test_your_stats_sits_between_voice_commands_and_help_in_both_menus(tray_menu):
    """CLAUDE.md menu order; the tray and the GNOME extension must match."""
    import pathlib
    items = [item.get_label() for item in tray_menu.get_children()]
    stats = items.index("Your Stats...")
    assert items[stats - 1:stats + 2] == ["Voice Commands...", "Your Stats...", "Help..."]

    root = pathlib.Path(__file__).resolve().parent.parent
    js = (root / "gnome-extension/talktype@ronb1964.github.io/extension.js").read_text()
    order = [js.index(f"new PopupMenu.PopupMenuItem('{label}')")
             for label in ("Voice Commands...", "Your Stats...", "Help...")]
    assert order == sorted(order)
    assert '<method name="OpenPreferencesStats"/>' in js


# --- the user's own typing speed ---------------------------------------------

def test_time_saved_uses_the_users_typing_speed():
    # 600 words: 10 min to type at 60 wpm, 15 min at 40; spoken in 5 min.
    assert stats.time_saved(600, 300, wpm=60) == 600 - 300
    assert stats.time_saved(600, 300, wpm=40) == 900 - 300


@pytest.mark.parametrize("raw, wpm", [
    (55, 55), ("70", 70), (3, 10), (900, 200), ("fast", 40), (None, 40),
])
def test_typing_speed_is_kept_to_a_sensible_whole_number(raw, wpm):
    assert stats.clamp_typing_wpm(raw) == wpm


def test_summary_works_out_time_saved_at_the_chosen_speed(stats_file):
    today = D(2026, 10, 10)
    stats.record(600, 300, today=today)
    assert stats.summary(stats.load(), today, typing_wpm=60)["all"]["saved"] == 300
    assert stats.summary(stats.load(), today)["all"]["saved"] == 600   # default 40


def test_typing_speed_setting_needs_no_service_restart():
    from tuxtalk import config
    assert config.Settings().typing_wpm == 40
    assert "typing_wpm" in config.LIVE_APPLIED_KEYS


def test_the_tray_still_finds_updates_after_the_tab_became_updates_and_backup():
    import types
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from tuxtalk.prefs import PreferencesWindow

    notebook = Gtk.Notebook()
    for name in ("General", "Stats", "Updates & Backup"):
        page = Gtk.Box()
        page.show()
        notebook.append_page(page, Gtk.Label(label=name))
    window = types.SimpleNamespace(notebook=notebook)
    for asked in ("updates", "backup", "Updates"):
        notebook.set_current_page(0)
        assert PreferencesWindow.select_tab(window, asked) is True
        assert notebook.get_current_page() == 2
