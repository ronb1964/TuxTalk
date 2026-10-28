"""Repairing a login entry left pointing at a stale AppImage.

Setups before 0.10.1 pointed launch-at-login at the AppImage the user happened
to run setup from (usually ~/Downloads). Such an entry keeps launching that old
copy, which may need libfuse2 and then silently never starts at login on
systems without it, so the installed AppImage repairs it on startup.
"""
import pytest

from tuxtalk import autostart, config


@pytest.fixture
def env(tmp_path, monkeypatch):
    return _env(tmp_path, monkeypatch)


def _env(home, monkeypatch):
    """An installed AppImage in *home* and the path of its login entry."""
    installed = home / "AppImages" / "TalkType.AppImage"
    installed.parent.mkdir()
    installed.write_text("")
    installed.chmod(0o755)
    entry = home / ".config" / "autostart" / "talktype.desktop"
    entry.parent.mkdir(parents=True)
    monkeypatch.setattr(autostart, "INSTALLED_APPIMAGE", str(installed))
    monkeypatch.setattr(autostart, "_autostart_desktop_path", lambda: str(entry))
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setenv("APPIMAGE", str(installed))
    return home, installed, entry


def _entry(path, exec_line, extra=""):
    path.write_text(f"[Desktop Entry]\nType=Application\nName=TalkType\nExec={exec_line}\n{extra}")


def test_entry_pointing_at_an_old_downloaded_appimage_is_repaired(env):
    home, installed, entry = env
    _entry(entry, str(home / "Downloads" / "TalkType-v0.10.0-x86_64.AppImage"))

    assert autostart.repair_stale_appimage_autostart() is True
    assert f"Exec={installed}\n" in entry.read_text()


def test_entry_already_on_the_installed_copy_is_left_alone(env):
    home, installed, entry = env
    _entry(entry, str(installed))
    before = entry.read_text()

    assert autostart.repair_stale_appimage_autostart() is False
    assert entry.read_text() == before


@pytest.mark.parametrize("contents", [
    "[Desktop Entry]\nType=Application\nName=TalkType\nHidden=true\n",   # turned off
    "[Desktop Entry]\nExec=/x/TalkType-v0.10.0-x86_64.AppImage\nX-GNOME-Autostart-enabled=false\n",
    "[Desktop Entry]\nExec=/usr/bin/talktype\n",                         # .deb/.rpm launcher
    "[Desktop Entry]\nExec=/home/u/Apps/SomethingElse.AppImage\n",       # not ours
])
def test_disabled_or_unrelated_entries_are_never_touched(env, contents):
    _home, _installed, entry = env
    entry.write_text(contents)

    assert autostart.repair_stale_appimage_autostart() is False
    assert entry.read_text() == contents


def test_only_the_appimage_repairs(env, monkeypatch):
    home, _installed, entry = env
    _entry(entry, str(home / "Downloads" / "TalkType-v0.10.0-x86_64.AppImage"))

    monkeypatch.delenv("APPIMAGE")                         # dev, .deb, .rpm
    assert autostart.repair_stale_appimage_autostart() is False
    monkeypatch.setenv("APPIMAGE", "/x/TalkType.AppImage")
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    assert autostart.repair_stale_appimage_autostart() is False


def test_no_installed_copy_means_no_repair(env):
    home, installed, entry = env
    installed.unlink()
    _entry(entry, str(home / "Downloads" / "TalkType-v0.10.0-x86_64.AppImage"))

    assert autostart.repair_stale_appimage_autostart() is False


# --- The entry's description: an older one names Faster-Whisper ---

@pytest.mark.parametrize("dev, old, new", [
    (False, "AI-powered dictation for Wayland using Faster-Whisper", autostart.AUTOSTART_COMMENT),
    (True, "AI-powered dictation - Development Version", autostart.AUTOSTART_COMMENT_DEV),
])
def test_old_description_is_refreshed_and_nothing_else_changes(tmp_path, monkeypatch, dev, old, new):
    entry = tmp_path / "talktype.desktop"
    before = ("[Desktop Entry]\nName=TalkType\nComment=" + old +
              "\nExec=/home/u/AppImages/TalkType.AppImage\nX-GNOME-Autostart-enabled=false\n")
    entry.write_text(before)
    monkeypatch.setattr(autostart, "_autostart_desktop_path", lambda: str(entry))
    monkeypatch.setattr(config, "DEV_MODE", dev)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert autostart.refresh_autostart_comment() is True
    assert entry.read_text() == before.replace("Comment=" + old, "Comment=" + new)
    assert autostart.refresh_autostart_comment() is False      # already current


def test_a_custom_description_is_left_alone(tmp_path, monkeypatch):
    entry = tmp_path / "talktype.desktop"
    entry.write_text("[Desktop Entry]\nComment=My own words\nExec=talktype\n")
    monkeypatch.setattr(autostart, "_autostart_desktop_path", lambda: str(entry))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert autostart.refresh_autostart_comment() is False
    assert "My own words" in entry.read_text()


def test_new_entries_get_the_current_description(tmp_path, monkeypatch):
    entry = tmp_path / "autostart" / "talktype.desktop"
    monkeypatch.setattr(autostart, "_autostart_desktop_path", lambda: str(entry))
    monkeypatch.setattr(autostart, "get_launch_command", lambda *a, **k: "/usr/bin/talktype")
    monkeypatch.setattr(autostart, "get_icon_path", lambda: "icon")
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert autostart.set_autostart(True)
    assert f"Comment={autostart.AUTOSTART_COMMENT}\n" in entry.read_text()


@pytest.fixture
def spaced_env(tmp_path, monkeypatch):
    """env, with a home folder that has a space in its name."""
    home = tmp_path / "Jo Ann"
    home.mkdir()
    return _env(home, monkeypatch)


@pytest.mark.parametrize("where", ["installed", "download"])
def test_an_unquoted_path_with_a_space_is_repaired(spaced_env, where):
    """Written unquoted before 0.19, "/home/Jo Ann/..." made the desktop look
    for "/home/Jo", so TuxTalk never started at login; it's written quoted."""
    home, installed, entry = spaced_env
    old = installed if where == "installed" else home / "Downloads" / "TuxTalk-v0.18.2.AppImage"
    old.parent.mkdir(exist_ok=True)
    old.write_text("")
    _entry(entry, str(old))
    assert autostart.repair_stale_appimage_autostart() is True
    assert f'Exec="{installed}"\n' in entry.read_text()
    assert autostart.repair_stale_appimage_autostart() is False      # once
