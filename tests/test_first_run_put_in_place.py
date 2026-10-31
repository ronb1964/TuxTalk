"""First run putting TuxTalk in place (appimage_copy.put_in_place): only a
self-managed AppImage is copied into ~/AppImages and gets a menu launcher.

APPIMAGE is passed down to everything an AppImage starts, so a .deb TuxTalk
opened from an AppImage terminal or editor sees that program's; and the AUR's
AppImage is pacman's to update and remove.
"""
import pathlib
import types

import pytest

from tuxtalk import appimage_copy, autostart, config, desktop_launcher, installation

# Set as APPDIR: this code runs from inside the AppImage APPIMAGE names.
CODE = str(pathlib.Path(installation.__file__).resolve().parent)


@pytest.fixture
def first_run(tmp_path, monkeypatch):
    """put_in_place with its launcher, login entry and copy recorded."""
    done = []
    monkeypatch.setattr(installation, "APPIMAGE_PATH", str(tmp_path / "AppImages/TuxTalk.AppImage"))
    monkeypatch.setattr(desktop_launcher, "desktop_launcher_exists", lambda: False)
    monkeypatch.setattr(desktop_launcher, "create_desktop_launcher",
                        lambda: done.append("menu launcher") or (True, ""))
    monkeypatch.setattr(autostart, "set_autostart",
                        lambda enable, prefer_installed_appimage=False:
                        done.append(("starts at login", prefer_installed_appimage)) or True)
    monkeypatch.setattr(config, "load_config", lambda: types.SimpleNamespace(launch_at_login=True))
    monkeypatch.setattr(appimage_copy, "start", lambda path: done.append(("copies", path)))
    monkeypatch.delenv("FLATPAK_ID", raising=False)

    def run():
        return appimage_copy.put_in_place(), done
    return run


def test_an_appimage_from_downloads_is_copied_and_gets_a_launcher(first_run, tmp_path,
                                                                    monkeypatch):
    download = str(tmp_path / "Downloads/TuxTalk-v0.19.0-x86_64.AppImage")
    monkeypatch.setenv("APPIMAGE", download)
    monkeypatch.setenv("APPDIR", CODE)
    result, done = first_run()
    assert result == (True, True)
    assert done == ["menu launcher", ("starts at login", True), ("copies", download)]


def test_a_package_started_from_another_appimage_doesnt_copy_that_program_in(
        first_run, monkeypatch):
    """Copied to ~/AppImages/TuxTalk.AppImage, the editor started at every
    login in TuxTalk's place, and one of two TuxTalk menu entries opened it."""
    monkeypatch.setattr(installation, "__file__",
                        "/opt/talktype/usr/lib/python3/site-packages/tuxtalk/installation.py")
    monkeypatch.setattr(installation, "_detect_system_package_manager", lambda: "deb")
    monkeypatch.setenv("APPIMAGE", "/home/u/Applications/Cursor.AppImage")
    monkeypatch.setenv("APPDIR", "/tmp/.mount_CursorAb12")
    assert installation.get_running_appimage_path() is None
    result, done = first_run()
    assert result == (False, False)
    assert done == [("starts at login", False)]


def test_the_aur_package_is_left_to_pacman(first_run, monkeypatch):
    """A copy in ~/AppImages overrode the package's menu entry, was never
    updated by pacman, and kept starting at login after pacman -R."""
    monkeypatch.setenv("APPIMAGE", "/opt/tuxtalk/TuxTalk.AppImage")
    monkeypatch.setenv("APPDIR", CODE)
    assert installation.get_install_type() == "aur"
    result, done = first_run()
    assert result == (False, False)
    assert done == [("starts at login", False)]
