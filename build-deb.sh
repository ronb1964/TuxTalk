#!/bin/bash
# TuxTalk .deb Build Script
# ==========================
# Repackages the release AppImage into a Debian/Ubuntu .deb.
#
# It reuses the finished AppImage (packaging/stage-from-appimage.sh says why
# and what it installs), so run ./build-release.sh FIRST.
#
# System deps are DECLARED (not bundled): the bundle relies on the host's GTK stack.
#   libgtk-3-0, libayatana-appindicator3-1 (tray), libportaudio2 (mic),
#   pulseaudio-utils (pactl, for turning other sounds down while dictating:
#   Zorin OS ships without it, 2026-10-20; it works on PipeWire too).
#   wl-clipboard IS bundled inside /opt/talktype, so it is not declared.
#
# Requires: fpm (gem install --user-install fpm) and ar/tar (binutils/coreutils).
# Builds fine on a non-Debian host (Fedora/Nobara) — no dpkg-deb needed.

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# shellcheck source=packaging/stage-from-appimage.sh
source "$PROJECT_DIR/packaging/stage-from-appimage.sh"
require_fpm
VERSION=$(project_version "$PROJECT_DIR")
APPIMAGE=$(release_appimage "$PROJECT_DIR" "$VERSION")

echo "📦 Building TuxTalk .deb from $(basename "$APPIMAGE")"
echo "========================================"

# Work in a temp dir so nothing litters the project tree.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
stage_tree "$APPIMAGE" "$WORK" "$PROJECT_DIR"
STAGE="$WORK/stage"

# Debian keeps a package's license in /usr/share/doc/<package>/copyright.
LIC_SRC="$STAGE/opt/talktype/usr/share/licenses"
mkdir -p "$STAGE/usr/share/doc/tuxtalk"
{ cat "$LIC_SRC/tuxtalk/LICENSE"; echo
  echo "TuxTalk bundles third-party software under /opt/talktype. Its licenses are in"
  echo "/opt/talktype/usr/share/licenses/; THIRD_PARTY_LICENSES.md here lists them."
} > "$STAGE/usr/share/doc/tuxtalk/copyright"
cp "$LIC_SRC/tuxtalk/THIRD_PARTY_LICENSES.md" "$STAGE/usr/share/doc/tuxtalk/"

POSTINST="$WORK/postinst.sh"
finish_stage "$STAGE" "$POSTINST"

echo "🔧 Packaging with fpm..."
rm -f "$PROJECT_DIR/tuxtalk_${VERSION}_amd64.deb"
fpm -s dir -t deb \
    -n tuxtalk \
    -v "$VERSION" \
    --architecture amd64 \
    --maintainer "Ron Brand <ronb1964@gmail.com>" \
    --url "https://tuxtalk.app/" \
    --description "TuxTalk (formerly TalkType) — voice dictation for Linux.
 Hold a hotkey, speak, and your words are typed into any application. Runs fully
 offline, on the processor or an NVIDIA, AMD or Intel graphics card." \
    --license "MIT" \
    --category "sound" \
    --depends "libgtk-3-0" \
    --depends "libayatana-appindicator3-1" \
    --depends "libportaudio2" \
    --depends "pulseaudio-utils" \
    --after-install "$POSTINST" \
    --before-remove "$PROJECT_DIR/packaging/before-remove.sh" \
    --replaces "talktype < 0.15.0" \
    --conflicts "talktype < 0.15.0" \
    --provides "talktype" \
    --deb-no-default-config-files \
    -C "$STAGE" \
    -p "$PROJECT_DIR/tuxtalk_${VERSION}_amd64.deb" \
    opt usr

echo ""
echo "📦 .deb Details:"
ls -lh "$PROJECT_DIR/tuxtalk_${VERSION}_amd64.deb"

# Add this .deb to SHA256SUMS.txt so the in-app updater can verify the download
# before installing it with pkexec. build-release.sh created the file first, so
# we append here (dropping any stale entry from a previous build). fail-closed:
# the updater aborts a package download whose hash is missing from this file.
DEB_BASENAME="tuxtalk_${VERSION}_amd64.deb"
# shellcheck source=packaging/checksums.sh
source "$PROJECT_DIR/packaging/checksums.sh"
replace_checksums "$PROJECT_DIR" "$DEB_BASENAME"
echo "🔐 Added ${DEB_BASENAME} to SHA256SUMS.txt"

echo ""
echo "🚀 To test on Debian/Ubuntu:  sudo apt install ./tuxtalk_${VERSION}_amd64.deb"
