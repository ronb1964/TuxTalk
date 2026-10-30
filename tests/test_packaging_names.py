"""Release files carry the TuxTalk name and keep pre-rename installs working.

0.14.x installs look for things by their old names: AppImageUpdate for a
TalkType-*.AppImage.zsync, the GNOME first-run for talktype-gnome-extension.zip,
apt/dnf for a package that replaces talktype, pacman for one replacing
talktype-appimage. Each must still be found.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name):
    return (ROOT / name).read_text()


def test_appimage_is_named_tuxtalk_and_updates_from_the_renamed_repo():
    s = _read("container-build.sh")
    assert 'UPDATE_INFO="gh-releases-zsync|ronb1964|TuxTalk|latest|TuxTalk-*x86_64.AppImage.zsync"' in s
    assert 'AppDir "TuxTalk-v${VERSION}-x86_64.AppImage"' in s


def test_pre_rename_appimages_get_a_talktype_named_zsync():
    s = _read("container-build.sh")
    assert 'COMPAT_ZSYNC="TalkType-v${VERSION}-x86_64.AppImage.zsync"' in s
    assert 'zsyncmake -u "TuxTalk-v${VERSION}-x86_64.AppImage" -o "$COMPAT_ZSYNC"' in s


def test_the_extension_zip_ships_under_both_names_with_both_checksums():
    s = _read("package-extension.sh")
    assert 'OUTPUT_ZIP="$SCRIPT_DIR/tuxtalk-gnome-extension.zip"' in s
    assert 'cp "$OUTPUT_ZIP" "$COMPAT_ZIP"' in s
    assert 'replace_checksums "$SCRIPT_DIR" "tuxtalk-gnome-extension.zip" "talktype-gnome-extension.zip"' in s
    # The folder inside keeps the UUID: a new one is a new extension.
    assert 'zip -r tuxtalk-gnome-extension.zip talktype@ronb1964.github.io' in s


def test_deb_and_rpm_replace_the_talktype_package():
    for name in ("build-deb.sh", "build-rpm.sh"):
        s = _read(name)
        assert "    -n tuxtalk \\" in s, name
        for flag in ('--replaces "talktype < 0.15.0"', '--conflicts "talktype < 0.15.0"',
                     '--provides "talktype"'):
            assert flag in s, (name, flag)
    # The /usr/bin/talktype command and the /opt/talktype folder they keep:
    # test_native_packages builds both and looks.


def test_docs_name_the_folder_the_packages_install_to():
    # The published license notice points readers at the license texts.
    assert "`/opt/talktype/usr/share/licenses/`" in _read("THIRD_PARTY_LICENSES.md")
    assert "Both packages install to `/opt/talktype`" in _read("README.md")


def test_aur_package_replaces_talktype_appimage():
    s = _read("aur/PKGBUILD")
    assert "pkgname=tuxtalk-appimage" in s
    assert "replaces=('talktype-appimage')" in s
    assert "provides=('tuxtalk' 'talktype')" in s
    srcinfo = _read("aur/.SRCINFO")
    assert "pkgname = tuxtalk-appimage" in srcinfo and "replaces = talktype-appimage" in srcinfo


def test_licenses_are_filed_under_tuxtalk():
    s = _read("packaging/collect-licenses.sh")
    assert 'cp "$PROJECT/LICENSE" "$PROJECT/THIRD_PARTY_LICENSES.md" "$LIC/tuxtalk/"' in s


def test_aur_keeps_the_old_appimage_path_working_for_the_first_login():
    # A 0.14.x launch-at-login entry starts /opt/talktype/TalkType.AppImage;
    # TuxTalk rewrites it on its first start, but the first login comes before.
    s = _read("aur/PKGBUILD")
    assert 'ln -s /opt/tuxtalk/TuxTalk.AppImage "${pkgdir}/opt/talktype/TalkType.AppImage"' in s


def test_the_deb_brings_pactl_for_turning_other_sounds_down():
    """Zorin OS has no pactl, so "turn other sounds down" did nothing there
    (VM test, 2026-10-20)."""
    assert '--depends "pulseaudio-utils"' in _read("build-deb.sh")


def test_the_rpm_brings_pactl_too():
    """By path, which Fedora (pulseaudio-utils) and openSUSE resolve to
    their own package: the .rpm had no pactl dependency at all."""
    assert '--depends "/usr/bin/pactl"' in _read("build-rpm.sh")
