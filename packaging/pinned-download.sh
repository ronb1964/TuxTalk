#!/bin/bash
# Shared by container-build.sh and vulkan-engine-container.sh, which download
# build tools pinned by SHA256 (appimagetool, its runtime, the Vulkan SDK) and
# keep them between builds.

# pinned_download URL SHA256 CACHE DEST
# Leaves at DEST a file with the pinned SHA256: a copy of CACHE when that
# matches, else a fresh download, which then replaces CACHE. Only DEST is
# checked and used, and the caller makes it in a folder of its own, so the
# cache can't change between the check and the use, and two builds sharing
# the cache never write the same file. Cached files are only ever whole,
# checked downloads, renamed into place.
pinned_download() {
    local url="$1" sha="$2" cache="$3" dest="$4"
    if [ -f "$cache" ]; then
        cp "$cache" "$dest"
        if echo "$sha  $dest" | sha256sum -c --quiet >/dev/null 2>&1; then
            return 0
        fi
        echo "   $cache doesn't match its pinned SHA256; downloading it again" >&2
    fi
    wget -q -O "$dest" "$url" || { echo "❌ Could not download $url" >&2; return 1; }
    if ! echo "$sha  $dest" | sha256sum -c --quiet >/dev/null 2>&1; then
        echo "❌ $url doesn't match its pinned SHA256" >&2
        return 1
    fi
    # A name of its own beside the cache, not one from the PID: builds in two
    # containers can both be PID 1.
    # The checked copy is all the build needs: a cache that can't be kept
    # (a full or read-only disk) only means downloading again next time.
    local part
    if ! { part=$(mktemp "$cache.part.XXXXXXXX") && cp "$dest" "$part" && mv "$part" "$cache"; }
    then
        rm -f "$part"
        echo "   couldn't keep $cache for the next build; carrying on" >&2
    fi
}
