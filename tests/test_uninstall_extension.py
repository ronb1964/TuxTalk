"""Uninstalling the GNOME extension leaves no trace in GNOME's lists, and
Preferences' Install button stops saying "✓ Installed" (0.15.0 VM round)."""
import types

import pytest

from tuxtalk import desktop_detect, extension_helper as eh


def test_uninstall_removes_the_files_and_both_list_entries(monkeypatch, tmp_path):
    (tmp_path / eh.EXTENSION_UUID).mkdir()
    (tmp_path / eh.EXTENSION_UUID / "metadata.json").write_text("{}")
    monkeypatch.setattr(desktop_detect, "get_extension_dir", lambda: str(tmp_path))
    monkeypatch.setattr(eh.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0))
    forgotten = []
    monkeypatch.setattr(desktop_detect, "forget_extension",
                        lambda uuid, keys: forgotten.append((uuid, set(keys))))
    assert eh.uninstall_extension() is True
    assert not (tmp_path / eh.EXTENSION_UUID).exists()
    assert forgotten == [(eh.EXTENSION_UUID, {"disabled-extensions", "enabled-extensions"})]


def test_the_install_button_says_install_again_after_an_uninstall(monkeypatch):
    pytest.importorskip("gi")
    from tuxtalk.prefs_system_check import SystemCheck   # first: it picks GTK 3
    from gi.repository import Gtk

    win = types.SimpleNamespace(
        extension_status_label=Gtk.Label(),
        install_extension_button=Gtk.Button(label="✓ Installed"),
        uninstall_extension_button=Gtk.Button(label="Uninstall Extension"),
        _set_restart_info_sensitive=lambda s: None)
    monkeypatch.setattr(eh, "get_extension_status",
                        lambda: {"available": True, "installed": False, "enabled": False})
    SystemCheck._show_extension_status(win)
    assert win.install_extension_button.get_label() == "Install Extension"
    assert win.install_extension_button.get_sensitive()
