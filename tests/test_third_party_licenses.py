"""Bundled third-party software must ship with its license.

The AppImage (and so the .deb and .rpm, which repackage it) carries
wl-clipboard (GPL-3.0-or-later), Python packages and libraries copied from
Ubuntu. packaging/collect-licenses.sh puts all their
licenses into the AppImage and fails the build if one is missing; these tests
keep the wiring, and the versions named in THIRD_PARTY_LICENSES.md, honest.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = (ROOT / "container-build.sh").read_text()
COLLECT = (ROOT / "packaging/collect-licenses.sh").read_text()
NOTICE = (ROOT / "THIRD_PARTY_LICENSES.md").read_text()


def _var(name):
    m = re.search(rf"^{name}=(\S+)$", BUILD, re.M)
    assert m, f"container-build.sh no longer sets {name}"
    return m.group(1)


def test_ydotool_is_no_longer_built_but_its_source_offer_stands():
    """0.17.0 and earlier shipped ydotool (AGPL): the promise of its source
    outlives the last release that included it."""
    assert "ydotool" not in BUILD
    assert "708e96ff27e381a8c549418a9d34cdde12305317" in NOTICE
    assert "AGPL-3.0-or-later" in NOTICE and "https://github.com/ReimuNotMoe/ydotool" in NOTICE


def test_the_notice_names_the_wl_clipboard_version_that_is_built():
    assert f"`{_var('WL_CLIPBOARD_VERSION')}`" in NOTICE


def test_the_notice_states_each_copyleft_license_and_its_source():
    assert "GPL-3.0-or-later" in NOTICE and "https://github.com/bugaevc/wl-clipboard" in NOTICE


def test_licenses_are_collected_after_the_last_copy_into_the_appimage():
    call = BUILD.index("bash packaging/collect-licenses.sh AppDir /tmp/wl-clipboard")
    last_copy = max(m.start() for m in re.finditer(r"^cp .*AppDir", BUILD, re.M))
    assert last_copy < call < BUILD.index("appimagetool")


def test_the_collector_ships_every_license_and_fails_when_one_is_missing():
    for needle in ('"$WLCLIP_SRC/COPYING"',
                   '"$PROJECT/LICENSE" "$PROJECT/THIRD_PARTY_LICENSES.md"',
                   "licenses/ctranslate2-LICENSE", "licenses/tokenizers-LICENSE",
                   "/usr/share/doc/$pkg/copyright"):
        assert needle in COLLECT, needle
    assert "set -euo pipefail" in COLLECT
    assert COLLECT.count("exit 1") >= 2   # no package found / no copyright file


def test_the_licenses_wheels_leave_out_are_kept_in_the_repo():
    # CTranslate2's and tokenizers' wheels ship no license file of their own.
    assert (ROOT / "packaging/licenses/ctranslate2-LICENSE").read_text().startswith("MIT License")
    assert "Apache License" in (ROOT / "packaging/licenses/tokenizers-LICENSE").read_text()


def test_pyav_and_ffmpegs_libraries_ship_their_licenses():
    """PyAV's wheel bundles FFmpeg, x264, x265 and 36 more libraries with
    none of their licenses: 0.15 to 0.18 shipped them without notices."""
    pyav = ROOT / "packaging/licenses/pyav"
    for name in ("ffmpeg-COPYING.LGPLv3", "x264-COPYING", "x265-COPYING", "SOURCES.md",
                 "libdrm-NOTICES", "libgomp-COPYING.RUNTIME"):
        assert (pyav / name).is_file(), name
    assert 'cp -r "$HERE/licenses/pyav" "$LIC/pyav"' in COLLECT
    assert "av.libs" in COLLECT, "the build no longer checks av.libs against SOURCES.md"
    assert "usr/share/licenses/pyav/" in NOTICE
    # FFmpeg's libjpeg-derived files require this credit in the documentation.
    assert "based in part on\nthe work of the Independent JPEG Group" in NOTICE
    assert (pyav / "ffmpeg-libjpeg-NOTICES").is_file()
