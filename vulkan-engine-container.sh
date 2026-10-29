#!/bin/bash
# Runs INSIDE the Ubuntu 22.04 container; called by build-vulkan-engine.sh.
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq build-essential cmake git wget xz-utils libvulkan-dev ca-certificates >/dev/null

# Vulkan SDK for glslc and current Vulkan headers (22.04 has neither).
# A pinned version, checked against the SHA256 LunarG publishes at
# https://sdk.lunarg.com/sdk/sha/<version>/linux/vulkansdk-linux-x86_64-<version>.tar.xz.txt,
# so two builds of one engine compile its shaders with the same glslc.
VULKAN_SDK_VERSION=1.4.363.0
VULKAN_SDK_SHA256=197962f5cbf80baf2775a03336a01cee7c8745686c65aaa70d3f751ade4d7e43
SDK_DIR="/cache/sdk-$VULKAN_SDK_VERSION"
cd /cache
# Unpacked beside its folder and moved in only when complete, so a failed
# unpack is not cached and reused by the next build.
if [ ! -d "$SDK_DIR" ]; then
    wget -q -O sdk.tar.xz \
        "https://sdk.lunarg.com/sdk/download/${VULKAN_SDK_VERSION}/linux/vulkansdk-linux-x86_64-${VULKAN_SDK_VERSION}.tar.xz"
    echo "${VULKAN_SDK_SHA256}  sdk.tar.xz" | sha256sum -c --quiet \
        || { echo "❌ Vulkan SDK download doesn't match its pinned SHA256"; rm -f sdk.tar.xz; exit 1; }
    rm -rf "$SDK_DIR.partial" && mkdir "$SDK_DIR.partial"
    tar xf sdk.tar.xz -C "$SDK_DIR.partial" && rm sdk.tar.xz && mv "$SDK_DIR.partial" "$SDK_DIR"
fi
export VULKAN_SDK="$SDK_DIR/$VULKAN_SDK_VERSION/x86_64"
export PATH="$VULKAN_SDK/bin:$PATH"

WORK=/tmp/whisper-build
rm -rf "$WORK" && mkdir -p "$WORK" && cd "$WORK"
git clone -q --depth 1 --branch "v${WHISPER_CPP_VERSION}" https://github.com/ggml-org/whisper.cpp
cd whisper.cpp
# TuxTalk's own changes (vulkan-engine-patches/): Your Words for Parakeet's
# greedy decoder (parakeet_set_word_boost). A patch that doesn't apply
# stops the build rather than ship an engine without it.
for patch in /build/vulkan-engine-patches/*.patch; do
    [ -e "$patch" ] || continue
    git apply "$patch" || { echo "❌ $(basename "$patch") does not apply to whisper.cpp v${WHISPER_CPP_VERSION}"; exit 1; }
    echo "Applied $(basename "$patch")"
done
# GGML_BACKEND_DL + GGML_CPU_ALL_VARIANTS: the CPU part is chosen at run time
# for the user's processor, like the official builds, so one download fits all.
cmake -B build -DGGML_VULKAN=ON -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON \
      -DGGML_CPU_ALL_VARIANTS=ON -DWHISPER_BUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build build -j"$(nproc)" --target whisper-server whisper-cli parakeet-cli >/dev/null

NAME="talktype-whisper-vulkan-${WHISPER_CPP_VERSION}"
OUT="$WORK/$NAME"
mkdir -p "$OUT"
cp -a build/bin/whisper-server build/bin/whisper-cli build/bin/parakeet-cli "$OUT/"
cp -a build/bin/*.so* "$OUT/" 2>/dev/null || true
cp -a build/src/*.so* build/ggml/src/*.so* "$OUT/" 2>/dev/null || true
strip --strip-unneeded "$OUT"/whisper-* "$OUT"/parakeet-cli "$OUT"/*.so* 2>/dev/null || true
cp LICENSE "$OUT/LICENSE-whisper.cpp"
# A short public-domain sample (JFK, 1961) for TalkType's speed check.
cp samples/jfk.wav "$OUT/speed-check.wav"

# Refuse to publish a build that can't do what it's for.
test -f "$OUT/libggml-vulkan.so" || { echo "❌ no Vulkan backend in the build"; exit 1; }
test -f "$OUT/libparakeet.so" || { echo "❌ no Parakeet library in the build"; exit 1; }
nm -D "$OUT/libparakeet.so" | grep -q parakeet_set_word_boost || { echo "❌ the Parakeet library has no word boost"; exit 1; }
MAXGLIBC=$(objdump -T "$OUT"/whisper-server "$OUT"/*.so* 2>/dev/null | grep -oE "GLIBC_[0-9.]+" | sort -Vu | tail -1)
echo "Newest glibc needed: $MAXGLIBC"

cd "$WORK"
TARBALL="${ASSET:-$NAME-x64.tar.gz}"
tar czf "/build/$TARBALL" "$NAME"
cd /build && sha256sum "$TARBALL" > "$TARBALL.sha256"
if [ -n "$BUILD_USER" ]; then
    chown "$BUILD_USER:$BUILD_GROUP" "/build/$TARBALL" "/build/$TARBALL.sha256" 2>/dev/null || true
fi
echo "✅ Built $TARBALL"
