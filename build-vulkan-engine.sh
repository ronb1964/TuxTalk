#!/bin/bash
# Build TuxTalk's Vulkan engine (the "Vulkan (any GPU)" device, for AMD, Intel
# and NVIDIA): whisper.cpp's whisper-server with the Vulkan backend.
# whisper.cpp publishes no Linux Vulkan build of its own, so TuxTalk builds one
# and hosts it as a GitHub PRE-release (never "latest", so the in-app updater
# and the AppImage's update information never mistake it for a TuxTalk
# version). TuxTalk downloads it when Vulkan is chosen as the device, by hand
# or by first run's recommendation. The file names keep "talktype" because
# released versions download them by those names.
#
# Built in Ubuntu 22.04 like the AppImage, so it runs on 22.04 and newer
# (glibc 2.34). 22.04 has no glslc, so the build uses the LunarG Vulkan SDK,
# pinned in vulkan-engine-container.sh and cached in ~/.cache/talktype-vulkan-sdk.
#
# It also builds whisper.cpp's Parakeet library (libparakeet.so), which
# parakeet_gpu_worker.py loads to run Parakeet on the graphics chip.
#
# Patches in vulkan-engine-patches/ are applied to whisper.cpp first (from
# 1.9.5 build 1: Your Words for Parakeet, parakeet_set_word_boost). Check
# they still apply when moving to a new whisper.cpp version.
#
# Usage: ./build-vulkan-engine.sh <whisper.cpp version> <build number>
# The build number tells apart builds of the SAME whisper.cpp version with
# different contents. A published build must never be replaced: released
# TuxTalk versions check its SHA256 and would refuse a changed file. So the
# script refuses the build that src/tuxtalk/whisper_vulkan.py downloads now,
# or any earlier one, and a tarball that already exists here.
#
# Output: talktype-whisper-vulkan-<version>-b<build>-x64.tar.gz and its
# .sha256. The folder inside is always talktype-whisper-vulkan-<version>, so
# a later build unpacks over an earlier one. Afterwards put the version, build
# and SHA256 into src/tuxtalk/whisper_vulkan.py.
set -e
if [ $# -ne 2 ]; then
    echo "Usage: $0 <whisper.cpp version> <build number>" >&2
    exit 1
fi
WHISPER_CPP_VERSION="$1"
ENGINE_BUILD="$2"
ASSET="talktype-whisper-vulkan-${WHISPER_CPP_VERSION}-b${ENGINE_BUILD}-x64.tar.gz"
TAG="whisper-vulkan-${WHISPER_CPP_VERSION}-b${ENGINE_BUILD}"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# What the app downloads today; that build and earlier ones are published.
APP_PY="$PROJECT_DIR/src/tuxtalk/whisper_vulkan.py"
APP_VERSION=$(sed -n 's/^ENGINE_VERSION = "\([^"]*\)".*/\1/p' "$APP_PY")
APP_BUILD=$(sed -n 's/^ENGINE_BUILD = \([0-9]*\).*/\1/p' "$APP_PY")
if [ -z "$APP_VERSION" ] || [ -z "$APP_BUILD" ]; then
    echo "❌ Could not read ENGINE_VERSION / ENGINE_BUILD from $APP_PY" >&2
    exit 1
fi
if [ "$WHISPER_CPP_VERSION" = "$APP_VERSION" ] && [ "$ENGINE_BUILD" -le "$APP_BUILD" ]; then
    echo "❌ Build $ENGINE_BUILD of whisper.cpp $WHISPER_CPP_VERSION is already published" >&2
    echo "   (TuxTalk downloads build $APP_BUILD). Use build $((APP_BUILD + 1))." >&2
    exit 1
fi
if [ -e "$PROJECT_DIR/$ASSET" ]; then
    echo "❌ $ASSET already exists here. Move it away first." >&2
    exit 1
fi
# The commit each whisper.cpp release tag pointed at when it was pinned, so a
# tag moved since can't change what is built (the container checks it). For a
# new version: gh api repos/ggml-org/whisper.cpp/commits/v<version> --jq .sha
case "$WHISPER_CPP_VERSION" in
    1.9.5) WHISPER_CPP_COMMIT=d1be6fde11ac6e0407606b4e42fe72d34add8037 ;;
    *)
        echo "❌ No pinned commit for whisper.cpp $WHISPER_CPP_VERSION: add it to $0" >&2
        exit 1 ;;
esac
SDK_CACHE="$HOME/.cache/talktype-vulkan-sdk"
mkdir -p "$SDK_CACHE"
CONTAINER_CMD=$(command -v podman || command -v docker)
# Rootless podman already writes files as you. Docker writes them as root, so
# the container hands them back to your user; under podman that same chown
# would map to a sub-UID and leave files you can't delete.
OWNER_ARGS=()
case "$CONTAINER_CMD" in
    *docker) OWNER_ARGS=(-e BUILD_USER="$(id -u)" -e BUILD_GROUP="$(id -g)") ;;
esac

$CONTAINER_CMD run --rm \
    -v "$PROJECT_DIR:/build:Z" \
    -v "$SDK_CACHE:/cache:Z" \
    -e WHISPER_CPP_VERSION="$WHISPER_CPP_VERSION" -e WHISPER_CPP_COMMIT="$WHISPER_CPP_COMMIT" \
    -e ASSET="$ASSET" \
    "${OWNER_ARGS[@]}" \
    ubuntu:22.04 bash /build/vulkan-engine-container.sh

ls -lh "$PROJECT_DIR/$ASSET"*
cat "$PROJECT_DIR/$ASSET.sha256"
echo ""
echo "Next: upload both files to a GitHub pre-release tagged $TAG"
echo "(gh release create $TAG ... --prerelease), then set ENGINE_VERSION,"
echo "ENGINE_BUILD and ENGINE_SHA256 in src/tuxtalk/whisper_vulkan.py."
