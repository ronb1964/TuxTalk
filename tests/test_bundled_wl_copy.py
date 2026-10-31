"""The bundled wl-copy must speak ext-data-control.

KDE Plasma 6.7 offers only ext-data-control for setting the clipboard
quietly. A wl-copy built without it (Ubuntu 22.04's wayland-protocols is
1.25; wl-clipboard needs 1.39) falls back to a window of its own that can
take the keyboard focus, and TuxTalk's Ctrl+V then lands there: dictations
were lost in the AppImage, .deb and .rpm on KDE (Ron, 2026-10-18).
"""
import pathlib
import re

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent / "container-build.sh").read_text()


def test_wayland_protocols_is_new_enough_and_pinned():
    version = re.search(r"^WAYLAND_PROTOCOLS_VERSION=(\d+)\.(\d+)$", SCRIPT, re.M)
    assert version, "wayland-protocols is no longer built for wl-clipboard"
    assert (int(version.group(1)), int(version.group(2))) >= (1, 39)
    assert re.search(r"^WAYLAND_PROTOCOLS_SHA256=[0-9a-f]{64}$", SCRIPT, re.M)
    # Checked against it by pinned_download, before it's unpacked.
    assert re.search(r'pinned_download \\\n.*\n\s+"\$WAYLAND_PROTOCOLS_SHA256"', SCRIPT)


def test_wl_clipboard_is_built_against_it():
    assert 'PKG_CONFIG_PATH="/tmp/wayland-protocols-prefix/share/pkgconfig' in SCRIPT


def test_the_build_fails_without_ext_data_control():
    assert "grep -q -a ext_data_control_manager_v1 /build/AppDir/usr/bin/wl-copy" in SCRIPT
