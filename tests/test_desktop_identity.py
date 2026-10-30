"""TuxTalk's windows name the launcher they belong to.

Without a program name, every window reports itself as "python3" on Wayland
and KDE shows its generic "W" icon in the title bar and taskbar instead of
TuxTalk's microphone. The desktop finds the icon by matching the program name
to a launcher (.desktop file), and each install type names its launcher
differently:

  AppImage   ~/.local/share/applications/tuxtalk.desktop  (first run)
  AUR        /usr/share/applications/tuxtalk.desktop
  deb / rpm  /usr/share/applications/io.github.ronb1964.TuxTalk.desktop
  Flatpak    io.github.ronb1964.TuxTalk
  dev        ~/.local/share/applications/tuxtalk-dev.desktop
"""
import pathlib

import pytest

from tuxtalk import desktop_identity

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("install_type,expected", [
    ("appimage", "tuxtalk"),
    ("aur", "tuxtalk"),
    ("deb", "io.github.ronb1964.TuxTalk"),
    ("rpm", "io.github.ronb1964.TuxTalk"),
    ("package", "io.github.ronb1964.TuxTalk"),
    ("flatpak", "io.github.ronb1964.TuxTalk"),
    ("dev", "tuxtalk-dev"),
])
def test_name_matches_the_launcher_of_each_install(install_type, expected):
    assert desktop_identity.desktop_id(install_type) == expected


def test_the_launchers_really_have_those_names():
    # The .deb's and the .rpm's: test_native_packages builds them and looks.
    assert 'install -Dm644 tuxtalk.desktop "${pkgdir}/usr/share/applications/tuxtalk.desktop"' \
        in (ROOT / "aur" / "PKGBUILD").read_text()
    from tuxtalk.desktop_launcher import DESKTOP_FILE_PATH
    assert DESKTOP_FILE_PATH.endswith("/tuxtalk.desktop")


def test_apply_sets_the_program_name(monkeypatch):
    names = []
    monkeypatch.setattr("tuxtalk.installation.get_install_type", lambda **k: "appimage")
    monkeypatch.setattr(desktop_identity.GLib, "set_prgname", names.append)
    desktop_identity.apply()
    assert names == ["tuxtalk"]


def test_apply_never_raises(monkeypatch):
    def broken(**k):
        raise RuntimeError("no idea how this is installed")
    monkeypatch.setattr("tuxtalk.installation.get_install_type", broken)
    desktop_identity.apply()


def test_apply_does_not_query_the_package_database(monkeypatch):
    """rpm/deb/package all map to one launcher name, so startup skips the
    rpm -q / dpkg-query calls."""
    from tuxtalk import installation
    names = []
    monkeypatch.setattr(installation, "_detect_system_package_manager",
                        lambda: pytest.fail("queried the package database at startup"))
    monkeypatch.setattr(installation.os.path, "realpath",
                        lambda p: "/opt/tuxtalk/usr/src/tuxtalk/installation.py")
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(desktop_identity.GLib, "set_prgname", names.append)
    desktop_identity.apply()
    assert names == ["io.github.ronb1964.TuxTalk"]
