"""Folds: settings most people never need, closed behind a button that names
them, with a dim summary beside it (CLAUDE.md, "Every fold is a button").
GTK's expander arrow was too easy to miss."""
import pathlib
from types import SimpleNamespace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk.prefs import PreferencesWindow  # noqa: E402
from tuxtalk.ui_style import Fold  # noqa: E402

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "tuxtalk"


def test_the_button_opens_the_settings_and_says_hide():
    opened = []
    fold = Fold("Show fine-tuning", "Hide fine-tuning", on_toggle=opened.append)
    fold.button.clicked()
    assert fold.is_open() and fold.button.get_label() == "Hide fine-tuning"
    fold.button.clicked()
    assert not fold.is_open() and fold.button.get_label() == "Show fine-tuning"
    assert opened == [True, False]


def test_showing_the_window_leaves_it_closed():
    window = Gtk.Window()
    fold = Fold("Show more shortcuts", "Hide more shortcuts")
    fold.content.attach(Gtk.Label(label="Paste Last Dictation:"), 0, 0, 1, 1)
    window.add(fold)
    window.show_all()
    assert not fold.is_open()
    window.destroy()


def test_a_warning_beside_the_button_is_not_dimmed():
    fold = Fold("Show system check", "Hide system check")
    fold.set_summary("✓ everything is set up")
    assert fold.summary.get_opacity() < 1
    fold.set_summary("⚠ something needs your attention", dim=False)
    assert fold.summary.get_opacity() == 1


def test_no_window_uses_the_expander_arrow():
    users = [p.name for p in SRC.rglob("*.py") if "Gtk.Expander" in p.read_text()]
    assert users == []


def _prefs(config):
    w = SimpleNamespace(config=config)
    w.shortcuts_fold = Fold("Show more shortcuts", "Hide more shortcuts")
    w.position_fold = Fold("Show fine position", "Hide fine position")
    return w


def test_more_shortcuts_shows_the_keys_beside_its_button():
    w = _prefs({})
    w.vc_hotkey_combo, w.pl_hotkey_combo = Gtk.ComboBoxText(), Gtk.ComboBoxText()
    for combo, key in ((w.vc_hotkey_combo, "Ctrl+Alt+V"), (w.pl_hotkey_combo, "Ctrl+Alt+P")):
        combo.append("", "None (disabled)")
        combo.append(key, key)
    w.vc_hotkey_combo.set_active_id("")
    w.pl_hotkey_combo.set_active_id("Ctrl+Alt+P")
    PreferencesWindow._update_shortcuts_summary(w)
    assert w.shortcuts_fold.summary.get_text() == "cheat sheet off · paste last Ctrl+Alt+P"


def test_fine_position_says_how_far_the_indicator_moved():
    w = _prefs({"indicator_offset_x": 0, "indicator_offset_y": 0})
    PreferencesWindow._update_position_summary(w)
    assert w.position_fold.summary.get_text() == "not moved"
    # Saved in screen pixels: a negative y is up.
    w.config.update(indicator_offset_x=20, indicator_offset_y=-10)
    PreferencesWindow._update_position_summary(w)
    assert w.position_fold.summary.get_text() == "20 px right, 10 px up"
