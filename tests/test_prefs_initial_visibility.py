"""Rows that depend on a setting must be hidden when Preferences opens.

Hiding a row while its tab is built isn't enough: window.show_all() makes it
visible again, so a user with auto-timeout off would see a minutes spinner
that does nothing. Hiding it later on a timer makes it flash on screen first.
"""
from types import SimpleNamespace

from tuxtalk.prefs import PreferencesWindow
from tuxtalk.prefs_general import GeneralTab


class FakeWidget:
    def __init__(self):
        self.visible = False

    def set_visible(self, value):
        self.visible = value


class FakeWindow:
    """show_all() shows every child, as Gtk's does."""

    def __init__(self, children):
        self.children = children
        self.presented = False

    def show_all(self):
        for child in self.children:
            child.visible = True

    def present(self):
        self.presented = True


def _stub(config):
    rows = {name: FakeWidget() for name in
            ("timeout_label", "timeout_spin", "timeout_minutes_label", "timeout_note")}
    tab = SimpleNamespace(config=config, **rows)
    tab.update_timeout_ui_state = GeneralTab.update_timeout_ui_state.__get__(tab)
    return SimpleNamespace(config=config, window=FakeWindow(list(rows.values())),
                           ok_btn=SimpleNamespace(grab_default=lambda: None), general_tab=tab)


def test_rows_for_settings_that_are_off_stay_hidden_after_show_all():
    stub = _stub({"auto_timeout_enabled": False})

    PreferencesWindow._show_window(stub)

    assert stub.window.presented
    tab = stub.general_tab
    assert not tab.timeout_label.visible and not tab.timeout_spin.visible
    assert not tab.timeout_note.visible and not tab.timeout_minutes_label.visible


def test_rows_for_settings_that_are_on_are_shown():
    stub = _stub({"auto_timeout_enabled": True})

    PreferencesWindow._show_window(stub)

    assert stub.general_tab.timeout_label.visible and stub.general_tab.timeout_spin.visible


def test_turning_power_saving_on_scrolls_its_settings_into_view(monkeypatch):
    """It's the last thing on the General tab: the minutes and the note it
    shows were below the window's edge (Ron, 2026-10-17)."""
    shown = []
    tab = _stub({"auto_timeout_enabled": True}).general_tab
    monkeypatch.setattr("tuxtalk.prefs_general.scroll_into_view",
                        lambda widget, delay_ms=60: shown.append(widget))
    on = SimpleNamespace(get_active=lambda: True)
    off = SimpleNamespace(get_active=lambda: False)
    GeneralTab._on_auto_timeout_toggled(tab, off)
    assert shown == []
    GeneralTab._on_auto_timeout_toggled(tab, on)
    assert shown == [tab.timeout_note]
