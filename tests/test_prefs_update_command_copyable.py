"""A source install's update command, long as it is, can be copied from Preferences.

Updates & Backup shows the command in its Update Status label. A plain label can't
be selected, so the user would have to retype about 200 characters in a terminal.
"""
from types import SimpleNamespace

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import installation, source_install  # noqa: E402
from tuxtalk.prefs_updates import UpdatesTab  # noqa: E402


def _tab_with_a_real_status_label():
    """The Update Status box built as Preferences builds it, minus the rest of the tab."""
    hidden = SimpleNamespace(show=lambda: None, hide=lambda: None)
    tab = SimpleNamespace(_action_button=lambda *_: hidden, _current_release=None,
                          _on_download_update_clicked=None, _on_view_release_clicked=None,
                          _on_update_extension_clicked=None)
    tab.update_actions_box = Gtk.Box()
    UpdatesTab._build_update_status(tab, Gtk.Box())
    tab.download_btn = tab.view_release_btn = tab.update_extension_btn = hidden
    return tab


def test_the_update_command_of_a_source_install_can_be_selected_and_copied(monkeypatch):
    monkeypatch.setattr(installation, "get_install_type", lambda: "source")
    tab = _tab_with_a_real_status_label()

    UpdatesTab._handle_update_result(tab, {
        "success": True, "update_available": True, "current_version": "0.20.0",
        "latest_version": "0.21.0"}, SimpleNamespace(set_sensitive=lambda _: None))

    label = tab.update_status_label
    assert label.get_selectable()
    assert label.get_line_wrap()
    assert source_install.update_command("0.21.0") in label.get_text()


def test_a_release_tag_with_markup_characters_still_shows_the_message(monkeypatch):
    """Pango rejects a label whose markup holds a bare & or <, and the user
    would see no message and no command at all."""
    monkeypatch.setattr(installation, "get_install_type", lambda: "dev")
    tab = _tab_with_a_real_status_label()

    UpdatesTab._handle_update_result(tab, {
        "success": True, "update_available": True, "current_version": "0.20.0",
        "latest_version": "0.22.0 <R&D>"}, SimpleNamespace(set_sensitive=lambda _: None))

    assert "TuxTalk 0.22.0 <R&D> is available." in tab.update_status_label.get_text()
