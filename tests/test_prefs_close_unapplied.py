"""Closing Preferences with the X asks before throwing away unapplied changes.

0.15.0 testing: settings changed without Apply were lost without a word when
the window was closed. Now it asks: Apply, Discard or Keep Editing.
"""
from types import SimpleNamespace

import pytest

from tuxtalk import prefs


def _window(unapplied, answer):
    calls = []
    w = SimpleNamespace(
        _waiting_for_kde=False,
        _has_unapplied_changes=lambda: unapplied,
        _ask_apply_before_closing=lambda: calls.append("asked") or answer,
        on_ok=lambda button: calls.append("ok"),
        _release=lambda: calls.extend(["stop", "release"]),
    )
    return w, calls


@pytest.fixture(autouse=True)
def _no_main_quit(monkeypatch):
    monkeypatch.setattr(prefs.Gtk, "main_quit", lambda: None)


def test_nothing_changed_closes_without_asking():
    w, calls = _window(unapplied=False, answer=None)
    assert prefs.PreferencesWindow.on_window_close(w, None, None) is False
    assert "asked" not in calls and "release" in calls


def test_apply_saves_through_ok_and_lets_ok_close():
    w, calls = _window(unapplied=True, answer="apply")
    assert prefs.PreferencesWindow.on_window_close(w, None, None) is True
    assert calls == ["asked", "ok"]


def test_discard_closes_as_before():
    w, calls = _window(unapplied=True, answer="discard")
    assert prefs.PreferencesWindow.on_window_close(w, None, None) is False
    assert calls == ["asked", "stop", "release"]


def test_keep_editing_leaves_the_window_open():
    w, calls = _window(unapplied=True, answer="keep")
    assert prefs.PreferencesWindow.on_window_close(w, None, None) is True
    assert calls == ["asked"]


def _changes(config_at_open, config, commands_same=True):
    w = SimpleNamespace(
        _config_at_open=config_at_open, config=config,
        words_tab=SimpleNamespace(changed=lambda: not commands_same),
    )
    w._changed_since_open = lambda: prefs.PreferencesWindow._changed_since_open(w)
    return prefs.PreferencesWindow._has_unapplied_changes(w)


def test_unapplied_changes_are_settings_commands_and_a_language_pick():
    opened = {"auto_period": True, "language": "en", "language_mode": "auto"}
    assert not _changes(opened, dict(opened))
    assert _changes(opened, dict(opened, auto_period=False))
    assert _changes(opened, dict(opened), commands_same=False)
    # The Language dropdown writes its pick into the settings straight away.
    assert _changes(opened, dict(opened, language_mode="manual", language="de"))
