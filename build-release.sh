#!/bin/bash
# TuxTalk Release Build Script
# Builds AppImage in Ubuntu 22.04 container for maximum compatibility
# build-deb.sh, build-rpm.sh and package-extension.sh repackage its output

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🚀 Building TuxTalk AppImage (Release)"
echo "========================================"
echo ""

# Version consistency check — the AppImage filename comes from pyproject.toml
# while the running app reports src/tuxtalk/__init__.py's __version__.
# Drift between them makes the update checker offer the app an "update" to
# itself forever (happened once before). Fail the build instead.
PYPROJECT_VERSION=$(grep -E "^version = " "$PROJECT_DIR/pyproject.toml" | sed 's/version = "\(.*\)"/\1/')
INIT_VERSION=$(grep -E "^__version__ = " "$PROJECT_DIR/src/tuxtalk/__init__.py" | sed 's/__version__ = "\(.*\)"/\1/')
if [ -z "$PYPROJECT_VERSION" ] || [ -z "$INIT_VERSION" ]; then
    echo "❌ Could not read version from pyproject.toml ('$PYPROJECT_VERSION') or"
    echo "   src/tuxtalk/__init__.py ('$INIT_VERSION'). Check the 'version =' lines."
    exit 1
fi
if [ "$PYPROJECT_VERSION" != "$INIT_VERSION" ]; then
    echo "❌ VERSION MISMATCH: pyproject.toml says $PYPROJECT_VERSION but"
    echo "   src/tuxtalk/__init__.py says $INIT_VERSION"
    echo "   Bump BOTH files to the same version, then rebuild."
    exit 1
fi
echo "✅ Version check: $PYPROJECT_VERSION (pyproject.toml and __init__.py match)"
echo ""
# The Vulkan engine build this version downloads has to be on GitHub
# first. Otherwise every new Vulkan setup fails its download, and first run
# then saves the processor as the device, which stays after the engine is
# published.
# ENGINE_URL and ENGINE_SHA256 as whisper_vulkan.py works them out, its
# ENGINE_ lines run alone (importing the module needs numpy and the rest).
read -r ENGINE_URL ENGINE_SHA256 < <(python3 - "$PROJECT_DIR/src/tuxtalk/whisper_vulkan.py" <<'PY'
import ast, sys
tree = ast.parse(open(sys.argv[1]).read())
names = {}
for node in tree.body:
    if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id.startswith("ENGINE_")):
        exec(compile(ast.Module([node], []), sys.argv[1], "exec"), names)
print(names["ENGINE_URL"], names["ENGINE_SHA256"])
PY
)
if [ -z "$ENGINE_URL" ] || [ -z "$ENGINE_SHA256" ]; then
    echo "❌ Could not read ENGINE_URL and ENGINE_SHA256 from src/tuxtalk/whisper_vulkan.py"
    exit 1
fi
# Each answer is the body (or nothing) and the HTTP status; 000 is no answer.
SHA256_REPLY=$(curl -sL --max-time 30 -w ' %{http_code}' "$ENGINE_URL.sha256" || true)
ASSET_STATUS=$(curl -sIL -o /dev/null --max-time 30 -w '%{http_code}' "$ENGINE_URL" || true)
SHA256_STATUS=${SHA256_REPLY##* }
PUBLISHED_SHA256=""
if [ "$SHA256_STATUS" = 200 ]; then
    PUBLISHED_SHA256=$(echo "$SHA256_REPLY" | head -1 | cut -d' ' -f1)
fi
if [ "$SHA256_STATUS" = 000 ] || [ "$ASSET_STATUS" = 000 ]; then
    echo "❌ Couldn't reach GitHub to check the Vulkan engine this version downloads:"
    echo "   $ENGINE_URL"
    echo "   Build again once you're online."
    exit 1
fi
if [ "$SHA256_STATUS" != 200 ] || [ "$ASSET_STATUS" != 200 ] ||
        [ "$PUBLISHED_SHA256" != "$ENGINE_SHA256" ]; then
    echo "❌ The Vulkan engine this version downloads isn't published as it expects:"
    echo "   $ENGINE_URL (HTTP $ASSET_STATUS, its .sha256 HTTP $SHA256_STATUS)"
    echo "   SHA256 in whisper_vulkan.py: $ENGINE_SHA256, published: ${PUBLISHED_SHA256:-none}"
    echo "   Publish its pre-release (build-vulkan-engine.sh says how) before building."
    exit 1
fi
echo "✅ Vulkan engine check: $(basename "$ENGINE_URL") is published"
echo ""

# Use podman if available, otherwise docker
if command -v podman &> /dev/null; then
    CONTAINER_CMD="podman"
else
    CONTAINER_CMD="docker"
fi

# Who owns the AppImage afterwards. Docker writes files as root, so the
# container hands them back to your user (container-build.sh chowns when
# BUILD_USER is set). Rootless podman already writes them as you, and that
# same chown maps to a sub-UID (e.g. 525287), leaving files you can't delete
# without `podman unshare`. So only Docker gets BUILD_USER.
OWNER_ARGS=()
if [ "$CONTAINER_CMD" = "docker" ]; then
    OWNER_ARGS=(-e BUILD_USER="$(id -u)" -e BUILD_GROUP="$(id -g)")
fi

echo "📦 Building in Ubuntu 22.04 container..."
echo ""

# Run the container build script
$CONTAINER_CMD run --rm \
    -v "$PROJECT_DIR:/build:Z" \
    -w /build \
    -e HOME=/tmp \
    "${OWNER_ARGS[@]}" \
    ubuntu:22.04 \
    bash /build/container-build.sh

echo ""
echo "📦 AppImage Details:"
ls -lh TuxTalk-*.AppImage 2>/dev/null || echo "❌ No AppImage found - build failed"
echo ""

# Generate release checksums. The update checker downloads SHA256SUMS.txt
# from the GitHub release and verifies the AppImage against it before
# installing — include this file in every release (gh release create ...).
APPIMAGE_FILE="TuxTalk-v${PYPROJECT_VERSION}-x86_64.AppImage"
cd "$PROJECT_DIR"
# Remove any stale checksums from a previous release so a build failure
# can't leave a wrong SHA256SUMS.txt lying around to be published.
rm -f SHA256SUMS.txt
if [ -f "$PROJECT_DIR/$APPIMAGE_FILE" ]; then
    sha256sum "$APPIMAGE_FILE" > SHA256SUMS.txt
    # Include the extension zips when they have been packaged already
    # (package-extension.sh also refreshes these entries when run afterwards).
    # talktype-gnome-extension.zip is the same zip under its pre-0.15.0 name,
    # which older installs download by exact name.
    for zip in tuxtalk-gnome-extension.zip talktype-gnome-extension.zip; do
        if [ -f "$zip" ]; then
            sha256sum "$zip" >> SHA256SUMS.txt
        fi
    done
    echo "🔐 Checksums written to SHA256SUMS.txt:"
    cat SHA256SUMS.txt
    echo ""
else
    echo "⚠️  Expected AppImage '$APPIMAGE_FILE' not found — SHA256SUMS.txt NOT generated."
    echo "   The build likely failed; do not publish a release without it."
    echo ""
fi

# The AppImage's embedded update information points at this file in the
# latest release, so it must be uploaded with every release.
if [ -f "$PROJECT_DIR/$APPIMAGE_FILE.zsync" ]; then
    echo "🔄 Update file: $APPIMAGE_FILE.zsync (upload it with the release)"
else
    echo "⚠️  $APPIMAGE_FILE.zsync is missing: AppImageUpdate can't update from this release."
fi
# Installs from before the rename look for a TalkType-named .zsync.
COMPAT_ZSYNC="TalkType-v${PYPROJECT_VERSION}-x86_64.AppImage.zsync"
if [ -f "$PROJECT_DIR/$COMPAT_ZSYNC" ]; then
    echo "🔄 Update file for pre-rename installs: $COMPAT_ZSYNC (upload it too)"
else
    echo "⚠️  $COMPAT_ZSYNC is missing: TalkType installs can't use AppImageUpdate."
fi
echo ""

echo "🚀 To test: ./TuxTalk-v${PYPROJECT_VERSION}-x86_64.AppImage"
