"""Save Backup must ask before replacing a backup the dialog didn't ask about.

The file dialog confirms overwriting only the name as typed. Typing
"mybackup" when mybackup.zip exists adds ".zip" afterwards, which would
replace that backup without asking.
"""
from types import SimpleNamespace

from gi.repository import Gtk

from tuxtalk import settings_backup
from tuxtalk.prefs_updates import UpdatesTab


def _window(monkeypatch, typed, answer):
    asked = []
    monkeypatch.setattr("tuxtalk.prefs_updates.show_message",
                        lambda parent, kind, title, text="", buttons=None: asked.append(title) or answer)
    d = SimpleNamespace(
        window=None, _pick_file=lambda title, save_name=None: typed,
        _show_last_backup=lambda: None, _fill_restore_list=lambda: None,
        _show_backup_folder=lambda: None)
    return d, asked


def test_cancel_keeps_the_existing_backup(tmp_path, monkeypatch):
    (tmp_path / "mybackup.zip").write_text("the old backup")
    written = []
    monkeypatch.setattr(settings_backup, "write_backup", written.append)
    d, asked = _window(monkeypatch, str(tmp_path / "mybackup"), Gtk.ResponseType.CANCEL)

    UpdatesTab._on_save_backup_clicked(d, None)

    assert asked == ["Replace the existing backup?"]
    assert written == []


def test_ok_replaces_it(tmp_path, monkeypatch):
    (tmp_path / "mybackup.zip").write_text("the old backup")
    written = []
    monkeypatch.setattr(settings_backup, "write_backup", written.append)
    d, asked = _window(monkeypatch, str(tmp_path / "mybackup"), Gtk.ResponseType.OK)

    UpdatesTab._on_save_backup_clicked(d, None)

    assert asked == ["Replace the existing backup?", "Backup saved"]
    assert written == [str(tmp_path / "mybackup.zip")]


def test_a_new_name_is_not_questioned(tmp_path, monkeypatch):
    written = []
    monkeypatch.setattr(settings_backup, "write_backup", written.append)
    d, asked = _window(monkeypatch, str(tmp_path / "fresh"), Gtk.ResponseType.OK)

    UpdatesTab._on_save_backup_clicked(d, None)

    assert asked == ["Backup saved"]
    assert written == [str(tmp_path / "fresh.zip")]
