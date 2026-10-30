"""set_autostart writes an autostart .desktop that relaunches via the wrapper.

Onboarding and Preferences both call this. The enabled file must carry the real
launch command (the env-setting wrapper, not bare Python) and be marked as an
enabled autostart entry; disabling must write a Hidden=true stub rather than
leaving a live entry behind.
"""

import pytest

from tuxtalk import autostart


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    path = tmp_path / "autostart" / "talktype.desktop"
    monkeypatch.setattr(autostart, "_autostart_desktop_path", lambda: str(path))
    monkeypatch.setattr(autostart, "get_launch_command", lambda *a, **k: "/usr/bin/talktype")
    monkeypatch.setattr(autostart, "get_icon_path", lambda: "/icons/talktype.svg")
    return path


def test_enable_writes_wrapper_launch_command(desktop):
    assert autostart.set_autostart(True) is True
    text = desktop.read_text()
    assert "Exec=/usr/bin/talktype" in text
    assert "X-GNOME-Autostart-enabled=true" in text
    assert "Hidden=true" not in text
    # Must NOT be the bare-Python line that fails at login.
    assert "python3 -m tuxtalk.tray" not in text


def test_disable_writes_hidden_stub(desktop):
    autostart.set_autostart(True)
    assert autostart.set_autostart(False) is True
    text = desktop.read_text()
    assert "Hidden=true" in text
    assert "Exec=/usr/bin/talktype" not in text


def test_enable_creates_missing_directory(desktop):
    # Parent dir does not exist yet; set_autostart must create it.
    assert not desktop.parent.exists()
    assert autostart.set_autostart(True) is True
    assert desktop.exists()


def test_an_appimage_entry_gets_tuxtalks_icon_not_a_microphone(tmp_path, monkeypatch):
    """The AppImage's own files vanish when it exits, so an entry naming them
    (or none, the microphone) has no TuxTalk icon at login; first run puts
    TuxTalk's icon in the user's icon folder, which the entry can name."""
    home = tmp_path / "home"
    icon = home / ".local/share/icons/hicolor/256x256/apps/tuxtalk.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"png")
    mount = tmp_path / ".mount_TuxTalkAb12"           # the running AppImage's files
    (mount / "AppDir").mkdir(parents=True)
    (mount / "AppDir/io.github.ronb1964.TuxTalk.svg").write_text("<svg/>")
    monkeypatch.setattr(autostart, "_project_root", lambda: str(mount))
    monkeypatch.setattr(autostart.sys, "executable", str(mount / "usr/bin/python3"))
    monkeypatch.setattr(autostart.os.path, "expanduser", lambda p: p.replace("~", str(home), 1))
    real_isfile = autostart.os.path.isfile
    monkeypatch.setattr(autostart.os.path, "isfile",      # no system-wide icon here
                        lambda p: not p.startswith("/usr/") and real_isfile(p))
    assert autostart.get_icon_path() == "tuxtalk"


def test_an_extracted_appimage_s_own_icon_isnt_named_either(tmp_path, monkeypatch):
    """Run with extract-and-run, the AppImage's files are in a folder that's
    deleted at exit: only /.mount_ was skipped."""
    home = tmp_path / "home"
    icon = home / ".local/share/icons/hicolor/256x256/apps/tuxtalk.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"png")
    extracted = tmp_path / "appimage_extracted_ab12" / "squashfs-root"
    (extracted / "AppDir").mkdir(parents=True)
    (extracted / "AppDir/io.github.ronb1964.TuxTalk.svg").write_text("<svg/>")
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "TuxTalk.AppImage"))
    monkeypatch.setenv("APPDIR", str(extracted))
    monkeypatch.setattr(autostart, "_project_root", lambda: str(extracted))
    monkeypatch.setattr(autostart.sys, "executable", str(extracted / "usr/bin/python3"))
    monkeypatch.setattr(autostart.os.path, "expanduser", lambda p: p.replace("~", str(home), 1))
    real_isfile = autostart.os.path.isfile
    monkeypatch.setattr(autostart.os.path, "isfile",
                        lambda p: not p.startswith("/usr/") and real_isfile(p))
    assert autostart.get_icon_path() == "tuxtalk"


def test_the_icon_lookup_only_looks(tmp_path, monkeypatch):
    """Preferences sets start at login on its window's thread; installing the
    icon there refreshes icon caches, which can freeze it for seconds."""
    from tuxtalk import desktop_launcher
    monkeypatch.setattr(desktop_launcher, "install_app_icon",
                        lambda: pytest.fail("installed the icon"))
    monkeypatch.setattr(autostart, "_project_root", lambda: str(tmp_path / "none"))
    monkeypatch.setattr(autostart.sys, "executable", str(tmp_path / "none/bin/python3"))
    monkeypatch.setattr(autostart.os.path, "isfile", lambda p: False)
    assert autostart.get_icon_path() in ("tuxtalk", "audio-input-microphone")
