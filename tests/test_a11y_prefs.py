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
    state = types.SimpleNamespace(on=False, screen_reader=False)
    monkeypatch.setattr(a11y_switch, "_status",
                        lambda name: state.on if name == "IsEnabled" else state.screen_reader)

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


def test_the_desktops_own_switch_is_shown_on_and_why_it_cant_be_turned_off_here(
        switch, prefs_window):
    """On for a screen reader, or by the user's own desktop setting. The
    reason is in a label, which a screen reader reaches, not only a tooltip
    on a box it can't focus."""
    switch.on = True
    box = check(prefs_window)
    assert box.get_active() and not box.get_sensitive()
    find(box.get_toplevel(), Gtk.Label, prefs_advanced.A11Y_DESKTOPS_NOTE)


def test_a_switch_that_wont_turn_on_unticks_and_says_so(switch, prefs_window, monkeypatch):
    shown = []
    monkeypatch.setattr(prefs_advanced, "show_message",
                        lambda parent, kind, title, *a, **k: shown.append(title))
    monkeypatch.setattr(a11y_switch, "_set", lambda on: False)
    box = check(prefs_window)
    box.set_active(True)
    assert not box.get_active()
    assert shown == [prefs_advanced.A11Y_FAILED_TITLE]


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


def test_a_screen_reader_that_took_the_switch_over_shows_it_on(switch, prefs_window,
                                                               monkeypatch):
    """Unticked while a screen reader runs: the switch stays on for it, and
    the box says so rather than looking off."""
    shown = []
    monkeypatch.setattr(prefs_advanced, "show_message",
                        lambda parent, kind, title, *a, **k: shown.append(title))
    a11y_switch.allow()
    box = check(prefs_window)
    switch.screen_reader = True
    box.set_active(False)
    assert switch.on is True
    assert box.get_active() and not box.get_sensitive()
    assert shown == [prefs_advanced.A11Y_SCREEN_READER_TITLE]
    note = find(box.get_toplevel(), Gtk.Label, prefs_advanced.A11Y_DESKTOPS_NOTE)
    assert note.get_visible()


def test_a_no_that_went_off_but_couldnt_be_saved_or_undone_shows_it_off(
        switch, prefs_window, monkeypatch):
    """The box shows the switch as it is, not as it was before the click."""
    monkeypatch.setattr(prefs_advanced, "show_message", lambda *a, **k: None)
    a11y_switch.allow()
    box = check(prefs_window)
    monkeypatch.setattr(a11y_switch, "_saved", lambda state: False)
    real_set = a11y_switch._set
    monkeypatch.setattr(a11y_switch, "_set", lambda on: real_set(on) if not on else False)
    box.set_active(False)
    assert switch.on is False and not box.get_active()


def test_a_handover_shows_even_when_the_switch_cant_be_read_after(switch, prefs_window,
                                                                  monkeypatch):
    monkeypatch.setattr(prefs_advanced, "show_message", lambda *a, **k: None)
    a11y_switch.allow()
    box = check(prefs_window)
    switch.screen_reader = True
    monkeypatch.setattr(a11y_switch, "is_on", lambda: None)
    box.set_active(False)
    assert box.get_active() and not box.get_sensitive()
