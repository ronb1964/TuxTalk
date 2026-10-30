#!/bin/bash
# TuxTalk .rpm Build Script
# ==========================
# Repackages the release AppImage into a Fedora/RHEL/openSUSE .rpm.
#
# It reuses the finished AppImage, as build-deb.sh does
# (packaging/stage-from-appimage.sh says why and what it installs, the same
# tree as the .deb's), so run ./build-release.sh FIRST.
#
# System deps are DECLARED (the bundle relies on the host's GTK stack). Fedora names:
#   gtk3, libayatana-appindicator-gtk3 (tray), portaudio (mic), and /usr/bin/pactl
#   by path (for turning other sounds down; Fedora's pulseaudio-utils has it).
#   wl-clipboard IS bundled inside /opt/talktype, so it is not declared.
#
# Requires: fpm (gem install --user-install fpm) and rpmbuild (rpm-build package).

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# shellcheck source=packaging/stage-from-appimage.sh
source "$PROJECT_DIR/packaging/stage-from-appimage.sh"
require_fpm
if ! command -v rpmbuild >/dev/null 2>&1; then
    echo "❌ rpmbuild not found. Install it with:  sudo dnf install rpm-build"
    exit 1
fi
VERSION=$(project_version "$PROJECT_DIR")
APPIMAGE=$(release_appimage "$PROJECT_DIR" "$VERSION")

echo "📦 Building TuxTalk .rpm from $(basename "$APPIMAGE")"
echo "========================================"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
stage_tree "$APPIMAGE" "$WORK" "$PROJECT_DIR"
STAGE="$WORK/stage"

# Fedora keeps a package's license in /usr/share/licenses/<package>/.
LIC_SRC="$STAGE/opt/talktype/usr/share/licenses"
mkdir -p "$STAGE/usr/share/licenses/tuxtalk"
cp "$LIC_SRC/tuxtalk/LICENSE" "$LIC_SRC/tuxtalk/THIRD_PARTY_LICENSES.md" \
    "$STAGE/usr/share/licenses/tuxtalk/"

POSTINST="$WORK/postinst.sh"
finish_stage "$STAGE" "$POSTINST"

echo "🔧 Packaging with fpm..."
rm -f "$PROJECT_DIR"/tuxtalk-"${VERSION}"-*.rpm "$PROJECT_DIR"/tuxtalk-"${VERSION}".*.rpm
fpm -s dir -t rpm \
    -n tuxtalk \
    -v "$VERSION" \
    --architecture x86_64 \
    --maintainer "Ron Brand <ronb1964@gmail.com>" \
    --url "https://tuxtalk.app/" \
    --description "TuxTalk (formerly TalkType) — voice dictation for Linux.
Hold a hotkey, speak, and your words are typed into any application. Runs fully
offline, on the processor or an NVIDIA, AMD or Intel graphics card." \
    --license "MIT" \
    --category "Applications/Multimedia" \
    --depends "gtk3" \
    --depends "libayatana-appindicator-gtk3" \
    --depends "portaudio" \
    --depends "/usr/bin/pactl" \
    --after-install "$POSTINST" \
    --before-remove "$PROJECT_DIR/packaging/before-remove.sh" \
    --replaces "talktype < 0.15.0" \
    --conflicts "talktype < 0.15.0" \
    --provides "talktype" \
    -C "$STAGE" \
    -p "$PROJECT_DIR/tuxtalk-${VERSION}-1.x86_64.rpm" \
    opt usr

echo ""
echo "📦 .rpm Details:"
ls -lh "$PROJECT_DIR"/tuxtalk-"${VERSION}"-1.x86_64.rpm

# Add this .rpm to SHA256SUMS.txt so the in-app updater can verify the download
# before installing it with pkexec. build-release.sh created the file first, so
# we append here (dropping any stale entry from a previous build). fail-closed:
# the updater aborts a package download whose hash is missing from this file.
RPM_BASENAME="tuxtalk-${VERSION}-1.x86_64.rpm"
# shellcheck source=packaging/checksums.sh
source "$PROJECT_DIR/packaging/checksums.sh"
replace_checksums "$PROJECT_DIR" "$RPM_BASENAME"
echo "🔐 Added ${RPM_BASENAME} to SHA256SUMS.txt"

echo ""
echo "🚀 To test on Fedora:  sudo dnf install ./tuxtalk-${VERSION}-1.x86_64.rpm"
