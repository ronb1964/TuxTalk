"""The hotkey test must run against the config the SERVICE uses, not pending edits.

The Test Hotkeys dialog labels its rows from the pending dropdown selections in
Preferences, but the running service reports which hotkey ROLE it matched
against the SAVED config. Change the hold key without applying and the two
disagree: pressing the new key hits the service's old toggle key, and the dialog
lights the wrong row, which looks like an off-by-one bug.
"""

from tuxtalk.settings_schema import unapplied_hotkeys


def test_no_pending_changes_when_shown_matches_saved():
    cfg = {"hotkey": "F8", "toggle_hotkey": "F9", "mode": "hold"}
    assert unapplied_hotkeys(cfg, dict(cfg)) == []


def test_detects_an_unapplied_hold_key():
    shown = {"hotkey": "F5", "toggle_hotkey": "F9"}
    saved = {"hotkey": "F4", "toggle_hotkey": "F9"}
    assert unapplied_hotkeys(shown, saved) == ["hotkey"]


def test_detects_both_hotkeys_changed():
    shown = {"hotkey": "F5", "toggle_hotkey": "F6"}
    saved = {"hotkey": "F4", "toggle_hotkey": "F5"}
    assert unapplied_hotkeys(shown, saved) == ["hotkey", "toggle_hotkey"]


def test_ignores_unrelated_setting_changes():
    """A pending model or beeps change must not block the hotkey test."""
    shown = {"hotkey": "F8", "toggle_hotkey": "F9", "model": "large", "beeps": False}
    saved = {"hotkey": "F8", "toggle_hotkey": "F9", "model": "small", "beeps": True}
    assert unapplied_hotkeys(shown, saved) == []
