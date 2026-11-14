"""Preferences → Advanced → Fine-tuning: the desktop's accessibility switch,
which TuxTalk turns off only if it turned it on; and uninstalling puts it
back the same way (a11y_switch)."""
import types

import pytest
from gi.repository import Gtk
from gtk_text import find

from tuxtalk import a11y_switch, prefs_advanced

LABEL = prefs_advanced.A11Y_SWITCH_LABEL


@pytest.fixture
def switch(monkeypatch, tmp_path):
    monkeypatch.setattr(a11y_switch, "state_path", lambda: str(tmp_path / "a11y-switch.json"))
    state = types.SimpleNamespace(on=False)
    monkeypatch.setattr(a11y_switch, "is_on", lambda: state.on)

    def set_on(on):
        state.on = on
        return True
    monkeypatch.setattr(a11y_switch, "_set", set_on)
    monkeypatch.setattr(Gtk.Dialog, "run", lambda dialog: Gtk.ResponseType.OK)
    return state


def check(prefs_window):
    window = prefs_window()
    window.select_tab("advanced")
    find(window.window, Gtk.Button, "Show fine-tuning").clicked()
    return find(window.window, Gtk.CheckButton, LABEL)


def test_ticking_it_turns_the_switch_on_and_remembers_the_yes(switch, prefs_window):
    box = check(prefs_window)
    assert not box.get_active()
    box.set_active(True)
    assert switch.on is True and a11y_switch.answer() == "on"


def test_unticking_it_turns_off_what_tuxtalk_turned_on(switch, prefs_window):
    a11y_switch.allow()
    box = check(prefs_window)
    assert box.get_active() and box.get_sensitive()
    box.set_active(False)
    assert switch.on is False and a11y_switch.answer() == "not_now"


def test_the_desktops_own_switch_is_shown_on_and_cant_be_turned_off_here(switch, prefs_window):
    """On for a screen reader, or by the user's own desktop setting."""
    switch.on = True
    box = check(prefs_window)
    assert box.get_active() and not box.get_sensitive()
    assert "desktop" in box.get_tooltip_text()


def test_with_no_accessibility_bus_there_is_nothing_to_switch(switch, prefs_window):
    switch.on = None
    window = prefs_window()
    window.select_tab("advanced")
    find(window.window, Gtk.Button, "Show fine-tuning").clicked()
    with pytest.raises(LookupError):
        find(window.window, Gtk.CheckButton, LABEL)


def test_giving_it_back_turns_off_only_what_tuxtalk_turned_on(switch):
    """What uninstalling does (uninstall.undo_desktop_changes)."""
    a11y_switch.allow()
    assert a11y_switch.give_back() is True and switch.on is False
    switch.on = True
    a11y_switch._save({"answer": "on", "ours": False})
    assert a11y_switch.give_back() is True and switch.on is True


def test_a_switch_that_wont_turn_off_is_said(switch, monkeypatch):
    a11y_switch.allow()
    monkeypatch.setattr(a11y_switch, "_set", lambda on: False)
    assert a11y_switch.give_back() is False
