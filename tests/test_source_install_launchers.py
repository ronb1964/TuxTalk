"""A source install (a pip install into a venv) gets what the AppImage gets:
a `tuxtalk` command, a menu entry with TuxTalk's icon, start at login, and a
command Help can suggest for a desktop shortcut."""
import os
import pathlib
import sys
import types

import pytest

from tuxtalk import appimage_copy, autostart, config, control, desktop_launcher, installation

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _source_home(tmp_path, monkeypatch, venv_parent):
    """A computer whose TuxTalk runs from a venv under *venv_parent*, with every
    place TuxTalk writes (menu entry, icons, start at login) inside tmp_path."""
    home = tmp_path / "home"
    prefix = venv_parent / "venv"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("APPDIR", raising=False)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.setattr(installation, "get_install_type", lambda *a, **k: "source")
    menu_entry = home / ".local/share/applications/tuxtalk.desktop"
    monkeypatch.setattr(desktop_launcher, "DESKTOP_FILE_PATH", str(menu_entry))
    autostart_entry = home / ".config/autostart/tuxtalk.desktop"
    monkeypatch.setattr(autostart, "_autostart_desktop_path", lambda: str(autostart_entry))
    monkeypatch.setattr(desktop_launcher.menu_cache, "refresh_menu_cache", lambda: None)
    monkeypatch.setattr(desktop_launcher.menu_cache, "refresh_icon_caches", lambda base: None)
    # First run records start at login in the settings; keep that off the real ones.
    monkeypatch.setattr(config, "load_config", lambda: types.SimpleNamespace(launch_at_login=True))
    command = prefix / "bin/tuxtalk"
    command.parent.mkdir(parents=True)
    command.write_text("#!/usr/bin/python3\n")
    command.chmod(0o755)
    return types.SimpleNamespace(
        command=str(command), menu_entry=menu_entry, autostart_entry=autostart_entry,
        icons=home / ".local/share/icons/hicolor")


@pytest.fixture
def source_home(tmp_path, monkeypatch):
    return _source_home(tmp_path, monkeypatch, tmp_path / "home/.local/share/TuxTalk")


@pytest.fixture
def source_home_with_space(tmp_path, monkeypatch):
    return _source_home(tmp_path, monkeypatch, tmp_path / "home/My Apps")


def test_the_tuxtalk_command_starts_the_tray():
    pyproject = config._parse_toml_text((ROOT / "pyproject.toml").read_text())
    scripts = pyproject["tool"]["poetry"]["scripts"]
    assert scripts == {"tuxtalk": "tuxtalk.tray:main"}


def test_the_package_carries_the_projects_icon():
    assert (ROOT / "src/tuxtalk/icons/io.github.ronb1964.TuxTalk.png").read_bytes() == \
        (ROOT / "io.github.ronb1964.TuxTalk.png").read_bytes()


def test_a_source_install_writes_its_menu_entry_with_the_venvs_command(source_home):
    ok, _ = desktop_launcher.create_desktop_launcher()
    text = source_home.menu_entry.read_text()
    assert ok and f"Exec={source_home.command}\n" in text
    assert f"Exec={source_home.command} --open preferences\n" in text and "Icon=tuxtalk\n" in text
    assert (source_home.icons / "256x256/apps/tuxtalk.png").is_file()


def test_a_menu_entry_under_a_home_with_a_space_still_starts(source_home_with_space):
    ok, _ = desktop_launcher.create_desktop_launcher()
    text = source_home_with_space.menu_entry.read_text()
    assert ok and f'Exec="{source_home_with_space.command}"\n' in text
    assert f'Exec="{source_home_with_space.command}" --open preferences\n' in text


def test_first_run_gives_a_source_install_a_menu_entry_and_start_at_login(source_home):
    assert appimage_copy.put_in_place() == (False, True)
    assert f"Exec={source_home.command}\n" in source_home.menu_entry.read_text()
    assert f"Exec={source_home.command}\n" in source_home.autostart_entry.read_text()


def test_first_run_leaves_a_source_installs_existing_menu_entry_alone(source_home):
    source_home.menu_entry.parent.mkdir(parents=True)
    source_home.menu_entry.write_text("[Desktop Entry]\nName=Mine\n")
    assert appimage_copy.put_in_place() == (False, False)
    assert source_home.menu_entry.read_text() == "[Desktop Entry]\nName=Mine\n"


def test_start_at_login_starts_the_running_source_install_over_a_packaged_one(
        source_home, monkeypatch):
    """/usr/bin/tuxtalk exists too: a distro package next to a pip install."""
    real_isfile, real_access = os.path.isfile, os.access
    monkeypatch.setattr(os.path, "isfile",
                        lambda p: p == "/usr/bin/tuxtalk" or real_isfile(p))
    monkeypatch.setattr(os, "access",
                        lambda p, mode: p == "/usr/bin/tuxtalk" or real_access(p, mode))
    assert autostart.get_launch_command() == source_home.command


def test_start_at_login_quotes_a_venv_path_with_a_space(source_home_with_space):
    assert autostart.get_launch_command() == f'"{source_home_with_space.command}"'


def test_first_run_windows_get_tuxtalks_icon_in_a_source_install(source_home):
    assert desktop_launcher.create_hidden_desktop_entry() is True
    text = source_home.menu_entry.read_text()
    assert f"Exec={source_home.command}\n" in text
    assert "Icon=tuxtalk\n" in text and "NoDisplay=true\n" in text
    assert not desktop_launcher.desktop_launcher_exists()


def test_help_suggests_the_venvs_own_command_for_a_shortcut(source_home):
    assert control.shortcut_command() == f"{source_home.command} --toggle"


def test_help_quotes_a_venv_path_with_a_space_for_a_shortcut(source_home_with_space):
    assert control.shortcut_command() == f"'{source_home_with_space.command}' --toggle"


@pytest.fixture
def no_tuxtalk_script(source_home, monkeypatch):
    """A pip install outside a venv whose `tuxtalk` script isn't where pip
    puts it (source_install.command() finds none)."""
    from tuxtalk import source_install
    monkeypatch.setattr(source_install, "command", lambda: None)
    return source_home


def test_without_its_tuxtalk_script_no_menu_entry_points_at_a_missing_program(
        no_tuxtalk_script):
    ok, _ = desktop_launcher.create_desktop_launcher()
    assert not ok and not no_tuxtalk_script.menu_entry.exists()
    assert desktop_launcher.create_hidden_desktop_entry() is False
    assert not no_tuxtalk_script.menu_entry.exists()


def test_without_its_tuxtalk_script_start_at_login_isnt_written(no_tuxtalk_script):
    assert autostart.set_autostart(True) is False
    assert not no_tuxtalk_script.autostart_entry.exists()


def test_without_its_tuxtalk_script_help_suggests_the_plain_command(no_tuxtalk_script):
    assert control.shortcut_command() == "tuxtalk --toggle"


def test_the_icon_list_ends_with_the_packages_own_png_then_the_users_copy():
    from tuxtalk import app_icon
    files = app_icon.icon_files()
    assert files[-2:] == [str(ROOT / "src/tuxtalk/icons/io.github.ronb1964.TuxTalk.png"),
                          app_icon.user_icon_file()]


def _entries_starting(source_home, program):
    """A menu entry and a start-at-login entry, both starting *program*."""
    for entry in (source_home.menu_entry, source_home.autostart_entry):
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(f"[Desktop Entry]\nName=TuxTalk\nExec={program}\nType=Application\n")


def _start_tuxtalk_again(monkeypatch):
    """TuxTalk started after its first run, as far as the login and menu
    entries are concerned (the service and update check are left out)."""
    from tuxtalk import tray
    monkeypatch.setattr(tray.GLib, "timeout_add", lambda *a: None)
    tray._start_normally(types.SimpleNamespace(_auto_start_service=lambda ms: None,
                                               auto_check_for_updates=None), None, None)


def test_first_run_replaces_a_menu_entry_left_by_a_deleted_appimage(source_home, tmp_path):
    _entries_starting(source_home, tmp_path / "home/AppImages/TuxTalk.AppImage")
    assert appimage_copy.put_in_place() == (False, True)
    assert f"Exec={source_home.command}\n" in source_home.menu_entry.read_text()


def test_at_start_entries_that_start_an_old_venvs_tuxtalk_are_pointed_at_this_one(
        source_home, tmp_path, monkeypatch):
    """The venv was moved, or made again elsewhere: the menu and start at
    login started a program that isn't there any more."""
    _entries_starting(source_home, tmp_path / "old-venv/bin/tuxtalk")
    _start_tuxtalk_again(monkeypatch)
    assert f"Exec={source_home.command}\n" in source_home.menu_entry.read_text()
    assert f"Exec={source_home.command}\n" in source_home.autostart_entry.read_text()


def test_at_start_entries_that_start_another_install_the_user_keeps_stay(
        source_home, tmp_path, monkeypatch):
    other = tmp_path / "other-venv/bin/tuxtalk"
    other.parent.mkdir(parents=True)
    other.write_text("#!/bin/sh\n")
    other.chmod(0o755)
    _entries_starting(source_home, other)
    _start_tuxtalk_again(monkeypatch)
    assert f"Exec={other}\n" in source_home.menu_entry.read_text()
    assert f"Exec={other}\n" in source_home.autostart_entry.read_text()


def test_at_start_a_start_at_login_turned_off_stays_off(source_home, tmp_path, monkeypatch):
    _entries_starting(source_home, tmp_path / "old-venv/bin/tuxtalk")
    source_home.autostart_entry.write_text(source_home.autostart_entry.read_text()
                                           + "Hidden=true\n")
    _start_tuxtalk_again(monkeypatch)
    assert "Hidden=true\n" in source_home.autostart_entry.read_text()
    assert source_home.command not in source_home.autostart_entry.read_text()
