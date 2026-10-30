"""Install paths, launcher names and update URLs after the rename (0.15.0)."""
import os

from tuxtalk import (autostart, config, desktop_identity, desktop_launcher, installation,
                     update_checker, update_install)


def test_appimage_and_launchers_use_the_new_name(monkeypatch):
    assert installation.APPIMAGE_NAME == "TuxTalk.AppImage"
    assert autostart.INSTALLED_APPIMAGE.endswith("/AppImages/TuxTalk.AppImage")
    assert desktop_launcher.DESKTOP_FILE_PATH.endswith("/applications/tuxtalk.desktop")
    monkeypatch.setattr(config, "DEV_MODE", False)
    assert autostart._autostart_desktop_path().endswith("/autostart/tuxtalk.desktop")
    monkeypatch.setattr(config, "DEV_MODE", True)
    assert autostart._autostart_desktop_path().endswith("/autostart/tuxtalk-dev.desktop")


def test_window_identity_matches_the_new_launchers():
    assert desktop_identity.desktop_id("appimage") == "tuxtalk"
    assert desktop_identity.desktop_id("aur") == "tuxtalk"
    assert desktop_identity.desktop_id("dev") == "tuxtalk-dev"


def test_updates_come_from_the_renamed_repo():
    assert update_checker.GITHUB_API_URL == "https://api.github.com/repos/ronb1964/TuxTalk/releases/latest"
    assert "/.local/share/TuxTalk/" in update_install.UPDATE_DIR


def test_update_message_shows_after_an_update_from_talktype(tmp_path, monkeypatch):
    # A 0.14.x updater leaves .talktype_just_updated; the "Update complete"
    # message must still appear the first time TuxTalk starts.
    monkeypatch.setattr(installation, "APPIMAGE_DIR", str(tmp_path))
    (tmp_path / ".talktype_just_updated").write_text("0.14.6")
    assert update_install.check_just_updated() == "0.14.6"
    assert not (tmp_path / ".talktype_just_updated").exists()
    (tmp_path / ".tuxtalk_just_updated").write_text("0.15.0")
    assert update_install.check_just_updated() == "0.15.0"


def test_appimages_in_downloads_are_found_under_either_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(autostart.sys, "executable", "/tmp/.mount_TuxTalk123/usr/bin/python3")
    d = tmp_path / "Downloads"
    d.mkdir()
    a = d / "TuxTalk-v0.15.0-x86_64.AppImage"
    a.write_text("x")
    os.chmod(a, 0o755)
    assert autostart.get_launch_command() == str(a)


def test_package_detection_asks_for_tuxtalk_first(monkeypatch):
    import shutil
    import subprocess
    asked = []

    class R:
        def __init__(self, rc):
            self.returncode = rc

    def fake_run(cmd, **k):
        asked.append(cmd[-1])
        return R(0 if cmd[-1] == "tuxtalk" else 1)
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/" + n if n == "rpm" else None)
    monkeypatch.setattr(subprocess, "run", fake_run)
    installation._detect_system_package_manager.cache_clear()
    try:
        assert installation._detect_system_package_manager() == "rpm"
        assert asked[0] == "tuxtalk"
        installation._detect_system_package_manager()
        assert asked.count("tuxtalk") == 1, "the package database was queried twice"
    finally:
        installation._detect_system_package_manager.cache_clear()


def _detect_with(monkeypatch, run, os_release):
    """_detect_system_package_manager with rpm and dpkg-query present,
    *run* answering their queries and *os_release* (bytes) as the file."""
    import builtins
    import io
    import shutil
    import subprocess
    real_open = builtins.open
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(builtins, "open", lambda path, *a, **k: io.TextIOWrapper(
        io.BytesIO(os_release), encoding="utf-8") if path == "/etc/os-release"
        else real_open(path, *a, **k))
    installation._detect_system_package_manager.cache_clear()
    try:
        return installation._detect_system_package_manager()
    finally:
        installation._detect_system_package_manager.cache_clear()


def test_a_package_query_that_hangs_is_said_and_detection_goes_on(monkeypatch, caplog):
    """A hung rpm query made the install type fall back to the distro family
    without a word, so a log never said why updates were offered the wrong way."""
    import subprocess
    asked = []

    def hung(cmd, **kw):
        asked.append(" ".join(cmd))
        raise subprocess.TimeoutExpired(cmd, 5)
    caplog.set_level("INFO")
    assert _detect_with(monkeypatch, hung, b'ID=ubuntu\n') == "deb"
    assert asked == ["rpm -q tuxtalk", "dpkg-query -W tuxtalk",
                     "rpm -q talktype", "dpkg-query -W talktype"]
    assert "rpm -q tuxtalk didn't answer" in caplog.text


def test_an_unreadable_os_release_means_no_package_manager(monkeypatch, caplog):
    """Bytes that aren't text in /etc/os-release: detection says so and
    answers None, rather than raising into Preferences' Updates tab."""
    import subprocess

    def not_installed(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1)
    caplog.set_level("INFO")
    assert _detect_with(monkeypatch, not_installed, b"ID=\xff\xfe\n") is None
    assert "could not read /etc/os-release" in caplog.text


def test_package_restart_uses_the_tuxtalk_launcher(monkeypatch):
    import subprocess
    started = []
    monkeypatch.setattr(update_install.os.path, "exists", lambda p: p == "/usr/bin/tuxtalk")
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: started.append(args))
    ok, _ = update_install.restart_via_launcher()
    assert ok and "/usr/bin/tuxtalk" in started[0][-1]
