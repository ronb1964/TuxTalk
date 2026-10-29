"""First-run setup windows show TuxTalk's icon, not the desktop's generic one.

KDE and GNOME find a window's icon through the .desktop file its program name
(tuxtalk for the AppImage) names. The AppImage's menu launcher is only written
by the Ready screen, so the tray writes a hidden entry first, which the Ready
screen then replaces with the real launcher.
"""
import pathlib

import pytest

from tuxtalk import update_checker as uc
from tuxtalk.autostart import desktop_exec_program

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(uc, "DESKTOP_FILE_PATH", str(tmp_path / "apps" / "tuxtalk.desktop"))
    monkeypatch.setattr(uc, "APPIMAGE_PATH", str(tmp_path / "AppImages" / "TuxTalk.AppImage"))
    monkeypatch.setattr(uc, "_install_app_icon", lambda: "tuxtalk")
    monkeypatch.setattr(uc.menu_cache, "refresh_menu_cache", lambda: None)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "Downloads" / "TuxTalk-v0.15.0-x86_64.AppImage"))
    # Running from inside it: APPIMAGE alone may be another app's, inherited.
    monkeypatch.setenv("APPDIR", str(ROOT))
    return tmp_path


def test_an_appimage_without_a_launcher_gets_a_hidden_entry(home):
    assert uc.create_hidden_desktop_entry()
    text = (home / "apps" / "tuxtalk.desktop").read_text()
    assert "Icon=tuxtalk\n" in text
    assert "NoDisplay=true\n" in text          # no menu entry before setup finishes
    assert f"Exec={home}/Downloads/TuxTalk-v0.15.0-x86_64.AppImage\n" in text
    assert "Type=Application\n" in text
    # ...which doesn't count as the launcher, so the Ready screen still writes it.
    assert not uc.desktop_launcher_exists()


def test_the_ready_screen_replaces_it_with_the_real_launcher(home):
    uc.create_hidden_desktop_entry()
    ok, _ = uc.create_desktop_launcher()
    assert ok
    text = (home / "apps" / "tuxtalk.desktop").read_text()
    assert "NoDisplay" not in text
    assert uc.desktop_launcher_exists()


def test_an_existing_launcher_is_left_alone(home):
    (home / "apps").mkdir()
    (home / "apps" / "tuxtalk.desktop").write_text("[Desktop Entry]\nName=Mine\n")
    assert not uc.create_hidden_desktop_entry()
    assert (home / "apps" / "tuxtalk.desktop").read_text() == "[Desktop Entry]\nName=Mine\n"
    assert uc.desktop_launcher_exists()


@pytest.mark.parametrize("appimage,flatpak", [
    ("/opt/tuxtalk/TuxTalk.AppImage", ""),     # AUR: a user entry would hide its menu entry
    ("", "io.github.ronb1964.TuxTalk"),        # Flatpak
    ("", ""),                                  # dev checkout
])
def test_installs_with_their_own_launcher_get_nothing(home, monkeypatch, appimage, flatpak):
    monkeypatch.setenv("APPIMAGE", appimage)
    monkeypatch.setenv("FLATPAK_ID", flatpak)
    assert not uc.create_hidden_desktop_entry()
    assert not (home / "apps" / "tuxtalk.desktop").exists()


def test_a_failure_never_raises(home, monkeypatch):
    monkeypatch.setattr(uc, "DESKTOP_FILE_PATH", "/proc/nope/tuxtalk.desktop")
    assert not uc.create_hidden_desktop_entry()


def test_the_tray_writes_it_after_the_rename_and_before_any_window(tray_main):
    started = tray_main()
    # The rename moves talktype.desktop only while tuxtalk.desktop is missing.
    assert (started.index("adopts the old install") < started.index("writes the hidden menu entry")
            < started.index("makes the tray"))


@pytest.mark.parametrize("name", ["My Apps/TuxTalk-v0.15.0-x86_64.AppImage",
                                  "Tux $Talk \"q\" `x`\\y.AppImage"])
def test_a_download_path_with_spaces_still_makes_a_valid_entry(home, monkeypatch, name):
    """GLib drops an entry whose Exec program it can't find, and an unquoted
    space splits the path, so GNOME would show the generic icon."""
    gi_repo = pytest.importorskip("gi.repository")
    gio, glib = gi_repo.Gio, gi_repo.GLib
    appimage = home / name
    appimage.parent.mkdir(parents=True, exist_ok=True)
    appimage.write_text("#!/bin/sh\n")
    appimage.chmod(0o755)
    monkeypatch.setenv("APPIMAGE", str(appimage))
    assert uc.create_hidden_desktop_entry()
    info = gio.DesktopAppInfo.new_from_filename(str(home / "apps" / "tuxtalk.desktop"))
    assert info is not None
    # GLib splits the command line like this before it expands field codes.
    argv = glib.shell_parse_argv(info.get_commandline())[1]
    assert argv[0].replace("%%", "%") == str(appimage)


def test_a_percent_in_the_path_is_not_read_as_a_field_code():
    assert desktop_exec_program("/home/a/100%.AppImage") == "/home/a/100%%.AppImage"
    assert desktop_exec_program("/home/a/TuxTalk.AppImage") == "/home/a/TuxTalk.AppImage"


def test_writing_the_launcher_doesnt_wait_for_the_menu_cache(home, monkeypatch):
    """KDE's cache refresh (a 0.5 s sleep and kbuildsycoca) held Setup Complete
    back one to three seconds after the downloads."""
    import threading
    released = threading.Event()
    monkeypatch.setattr(uc.menu_cache, "refresh_menu_cache", lambda: released.wait(5))
    finished = threading.Event()
    threading.Thread(target=lambda: uc.create_desktop_launcher() and finished.set(),
                     daemon=True).start()
    try:
        assert finished.wait(2)
    finally:
        released.set()
