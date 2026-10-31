#!/bin/bash
# This script runs INSIDE the Ubuntu 22.04 container
# It's called by build-release.sh

set -e

echo "📍 Inside Ubuntu 22.04 container (Python 3.10, glibc 2.35)"
echo "🔧 Installing build dependencies..."

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    python3.10 \
    python3.10-venv \
    python3-pip \
    python3-dbus \
    git \
    build-essential \
    wget \
    xz-utils \
    rsync \
    libgirepository1.0-dev \
    gobject-introspection \
    gir1.2-glib-2.0 \
    gir1.2-ayatanaappindicator3-0.1 \
    libayatana-appindicator3-1 \
    libportaudio2 \
    patchelf \
    python3-gi \
    python3-gi-cairo \
    python3-cairo \
    libcairo2-dev \
    pkg-config \
    file \
    scdoc \
    fuse \
    libfuse2 \
    meson \
    ninja-build \
    libwayland-dev \
    wayland-protocols \
    zsync \
    > /dev/null 2>&1

echo "✅ Python version: $(python3 --version)"

# Get version from pyproject.toml
VERSION=$(grep -E "^version" pyproject.toml | sed 's/version = "\(.*\)"/\1/')
echo "✅ TuxTalk version: $VERSION"
echo ""

# Clean up old build artifacts
echo "🧹 Cleaning up old build artifacts..."
rm -rf AppDir /tmp/tuxtalk-build-venv

# Install Poetry globally in container. Pinned to the version that wrote
# poetry.lock, so every release resolves dependencies the same way.
echo "🐍 Installing Poetry..."
pip3 install -q 'poetry==2.5.1'

# CRITICAL: Configure Poetry to create venv in /tmp, NOT in /build - which is the users project dir
poetry config virtualenvs.in-project false
poetry config virtualenvs.path /tmp/tuxtalk-build-venv

export POETRY_CACHE_DIR=/tmp/poetry-cache

# The release installs exactly the versions in the committed poetry.lock. A
# lock that no longer matches pyproject.toml stops the build here instead of
# being re-resolved (and rewritten in the project folder) mid-release.
echo "🔒 Checking the lock file..."
if ! poetry check --lock; then
    echo "❌ poetry.lock is out of date: run 'poetry lock' in the project and commit it"
    exit 1
fi

# Install Python dependencies (fresh install, no cache)
echo "📚 Installing dependencies (this takes a few minutes)..."
poetry install --only main --no-root --no-cache

# Get the venv path from Poetry itself - extract first space-delimited field
# Use tr to convert spaces to tabs, then cut can use default tab delimiter
VENV_PATH=$(poetry env list --full-path 2>/dev/null | head -1 | tr " " "\t" | cut -f1)

echo ""
echo "   Poetry created venv at: $VENV_PATH"

# Verify venv was created and it's NOT in /build
if [[ "$VENV_PATH" == /build/* ]]; then
    echo "❌ CRITICAL ERROR: Poetry created venv inside /build/ - this would overwrite user's dev environment!"
    echo "   Venv path: $VENV_PATH"
    exit 1
fi

if [ -z "$VENV_PATH" ] || [ ! -d "$VENV_PATH" ]; then
    echo "❌ ERROR: Could not find Poetry venv"
    echo "   Expected path: $VENV_PATH"
    ls -la /tmp/tuxtalk-build-venv/ 2>/dev/null || echo "Base directory /tmp/tuxtalk-build-venv/ does not exist"
    exit 1
fi

echo "✅ Dependencies installed at: $VENV_PATH"
echo ""

# ============================================
# Build AppImage
# ============================================

echo "🏗️  Building AppImage..."

mkdir -p AppDir/usr/{bin,lib,src,share/applications,share/icons/hicolor/scalable,share/metainfo}

# Detect Python version from venv
PYTHON_VERSION=$($VENV_PATH/bin/python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "   Python version: $PYTHON_VERSION"

# Copy Python executable
cp -L $VENV_PATH/bin/python3 AppDir/usr/bin/python3
chmod +x AppDir/usr/bin/python3

# Bundle Python shared library
echo "   Bundling libpython..."
if [ -f "/usr/lib/x86_64-linux-gnu/libpython${PYTHON_VERSION}.so.1.0" ]; then
    cp "/usr/lib/x86_64-linux-gnu/libpython${PYTHON_VERSION}.so.1.0" AppDir/usr/lib/
else
    echo "❌ ERROR: Could not find libpython${PYTHON_VERSION}.so.1.0"
    exit 1
fi

# Copy Python standard library
echo "   Copying Python stdlib..."
mkdir -p "AppDir/usr/lib/python${PYTHON_VERSION}"
rsync -a --exclude='site-packages' --exclude='__pycache__' --exclude='*.pyc' \
    "/usr/lib/python${PYTHON_VERSION}/" "AppDir/usr/lib/python${PYTHON_VERSION}/"

# Copy Python packages (excluding large unnecessary ones)
echo "   Copying Python packages..."
mkdir -p "AppDir/usr/lib/python${PYTHON_VERSION}/site-packages"

rsync -a \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='torch' \
    --exclude='torch-*' \
    --exclude='torchvision' \
    --exclude='torchvision-*' \
    --exclude='torchaudio' \
    --exclude='torchaudio-*' \
    --exclude='nvidia*' \
    --exclude='triton*' \
    --exclude='cusparselt' \
    --exclude='sympy' \
    --exclude='sympy-*' \
    --exclude='networkx' \
    --exclude='networkx-*' \
    --exclude='pip' \
    --exclude='setuptools' \
    --exclude='wheel' \
    "$VENV_PATH/lib/python${PYTHON_VERSION}/site-packages/" \
    "AppDir/usr/lib/python${PYTHON_VERSION}/site-packages/"

# PyTorch is intentionally NOT bundled (removed 2026-08-15). It was never used:
# inference is faster-whisper/CTranslate2, GPU uses cuda_helper's CUDA libs, and model
# downloads use huggingface_hub — all verified torch-free on CPU and GPU. The rsync
# excludes above still drop torch/torchvision/nvidia* defensively in case a dev venv
# happens to have them installed.

# Copy system gi, cairo, and dbus Python packages (not available via pip on Ubuntu 22.04)
echo "   Copying system gi, cairo, and dbus packages..."

# Use Python -c to resolve paths (avoids nested heredoc issues)
GI_DIR=$(python3 -c "import gi, pathlib; print(pathlib.Path(gi.__file__).parent)")
CAIRO_DIR=$(python3 -c "import cairo, pathlib; print(pathlib.Path(cairo.__file__).parent)")
DBUS_DIR=$(python3 -c "import dbus, pathlib; print(pathlib.Path(dbus.__file__).parent)")
PYTHON_VERSION_STR=$(python3 -c "import sys; print(str(sys.version_info.major) + str(sys.version_info.minor))")
PYVER="${PYTHON_VERSION_STR:0:1}.${PYTHON_VERSION_STR:1}"
DEST="AppDir/usr/lib/python${PYVER}/site-packages"

echo "     Found gi at: $GI_DIR"
echo "     Found cairo at: $CAIRO_DIR"
echo "     Found dbus at: $DBUS_DIR"
echo "     Copying to: $DEST"

# Create destination directories
mkdir -p "$DEST/gi" "$DEST/cairo" "$DEST/dbus"

# Copy gi package completely
rsync -a "$GI_DIR/" "$DEST/gi/"
echo "     ✓ Copied gi package"

# Copy Cairo Python package
rsync -a "$CAIRO_DIR/" "$DEST/cairo/"
echo "     ✓ Copied cairo package"

# Copy D-Bus Python package
rsync -a "$DBUS_DIR/" "$DEST/dbus/"
echo "     ✓ Copied dbus package"

# CRITICAL: Copy _gi_cairo bridge module (PyGObject-Cairo integration)
# This provides the foreign struct converter for cairo.Context
if ls "$GI_DIR"/_gi_cairo*.so >/dev/null 2>&1; then
    cp -v "$GI_DIR"/_gi_cairo*.so "$DEST/gi/"
    echo "     ✓ Copied _gi_cairo module (PyGObject-Cairo bridge)"
else
    echo "     ⚠️  WARNING: _gi_cairo module not found - recording indicator may not work"
fi

# Copy D-Bus C bindings
if ls /usr/lib/python3/dist-packages/_dbus*.so >/dev/null 2>&1; then
    cp -v /usr/lib/python3/dist-packages/_dbus*.so "$DEST/"
    echo "     ✓ Copied _dbus C bindings"
elif ls "$DBUS_DIR"/../_dbus*.so >/dev/null 2>&1; then
    cp -v "$DBUS_DIR"/../_dbus*.so "$DEST/"
    echo "     ✓ Copied _dbus C bindings"
else
    echo "     ⚠️  WARNING: _dbus C bindings not found - GNOME extension may not work"
fi

# Do NOT bundle the Cairo C libraries (libcairo, libcairo-gobject,
# libpangocairo). GTK needs them, so every desktop that can run TuxTalk has
# them. Bundled, Ubuntu 22.04's cairo 1.16 replaced the system's, and it can't
# draw the color emoji fonts newer distros ship (Fedora's Noto Color Emoji is
# COLRv1 only): every emoji in the app, the language flags included, was
# blank. The Python cairo package and _gi_cairo above are all that's needed.

# Bundle wl-clipboard for clipboard paste support. Built from source because
# Ubuntu 22.04 ships 2.0.0, and paste needs 2.3.0's wl-copy --sensitive: it
# keeps dictations out of clipboard managers' history (KDE's Klipper saved
# every one to disk, issue #7). Pinned to a release tag.
#
# It also needs wayland-protocols 1.39 or newer to speak ext-data-control,
# the only quiet clipboard protocol KDE Plasma 6.7 still offers; Ubuntu 22.04
# has 1.25. Without it wl-copy falls back to a window of its own, which can
# take the keyboard focus, and the Ctrl+V right after lands in that window:
# dictations lost on KDE (Ron, 2026-10-18). Pinned tarball, checked by SHA256,
# kept in the project folder like appimagetool below, installed only for
# this build.
source packaging/pinned-download.sh
WAYLAND_PROTOCOLS_VERSION=1.45
WAYLAND_PROTOCOLS_SHA256=4d2b2a9e3e099d017dc8107bf1c334d27bb87d9e4aff19a0c8d856d17cd41ef0
WAYLAND_PROTOCOLS_CACHE="wayland-protocols-${WAYLAND_PROTOCOLS_VERSION}.tar.xz"
echo "📋 Getting wayland-protocols $WAYLAND_PROTOCOLS_VERSION for wl-clipboard..."
rm -rf /tmp/wayland-protocols-* /tmp/wayland-protocols-prefix
pinned_download \
    "https://gitlab.freedesktop.org/wayland/wayland-protocols/-/releases/${WAYLAND_PROTOCOLS_VERSION}/downloads/wayland-protocols-${WAYLAND_PROTOCOLS_VERSION}.tar.xz" \
    "$WAYLAND_PROTOCOLS_SHA256" "$WAYLAND_PROTOCOLS_CACHE" /tmp/wayland-protocols.tar.xz
tar -xJf /tmp/wayland-protocols.tar.xz -C /tmp
meson setup "/tmp/wayland-protocols-${WAYLAND_PROTOCOLS_VERSION}/build" \
    "/tmp/wayland-protocols-${WAYLAND_PROTOCOLS_VERSION}" \
    --prefix=/tmp/wayland-protocols-prefix -Dtests=false > /dev/null
ninja -C "/tmp/wayland-protocols-${WAYLAND_PROTOCOLS_VERSION}/build" install > /dev/null

WL_CLIPBOARD_VERSION=v2.3.0
# The commit the tag pointed at when it was pinned: its build scripts run
# here, and a tag moved since would change what is built.
WL_CLIPBOARD_COMMIT=67a7b937895bceec1ae5ccebb10216f63f70ca1b
echo "📋 Building wl-clipboard $WL_CLIPBOARD_VERSION..."
rm -rf /tmp/wl-clipboard
git clone -q --depth 1 --branch "$WL_CLIPBOARD_VERSION" https://github.com/bugaevc/wl-clipboard /tmp/wl-clipboard
if [ "$(git -C /tmp/wl-clipboard rev-parse HEAD)" != "$WL_CLIPBOARD_COMMIT" ]; then
    echo "❌ wl-clipboard $WL_CLIPBOARD_VERSION isn't the pinned commit $WL_CLIPBOARD_COMMIT"
    exit 1
fi
PKG_CONFIG_PATH="/tmp/wayland-protocols-prefix/share/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" \
    meson setup /tmp/wl-clipboard/build /tmp/wl-clipboard --buildtype=release > /dev/null
ninja -C /tmp/wl-clipboard/build > /dev/null
cp /tmp/wl-clipboard/build/src/wl-copy /tmp/wl-clipboard/build/src/wl-paste /build/AppDir/usr/bin/
/build/AppDir/usr/bin/wl-copy --help | grep -q -- --sensitive \
    || { echo "❌ bundled wl-copy has no --sensitive"; exit 1; }
grep -q -a ext_data_control_manager_v1 /build/AppDir/usr/bin/wl-copy \
    || { echo "❌ bundled wl-copy has no ext-data-control: pastes would be lost on KDE"; exit 1; }

# Bundle GTK3 and GObject Introspection
echo "   Bundling GTK3 libraries..."
cp /usr/lib/x86_64-linux-gnu/libgirepository-1.0.so.1 AppDir/usr/lib/
cp /usr/lib/x86_64-linux-gnu/libffi.so.* AppDir/usr/lib/

mkdir -p AppDir/usr/lib/girepository-1.0

# Copy typelibs from both possible locations. The `|| true` is deliberate here:
# only one of these paths exists on any given base image, so one copy is always
# expected to fail. The outcome is asserted below instead of the attempt.
cp /usr/lib/x86_64-linux-gnu/girepository-1.0/*.typelib AppDir/usr/lib/girepository-1.0/ 2>/dev/null || true
cp /usr/lib/girepository-1.0/*.typelib AppDir/usr/lib/girepository-1.0/ 2>/dev/null || true

# Verify every typelib the app imports UNGUARDED. Only AppIndicator3 used to be
# checked, so a missing Gtk, Gdk or GdkPixbuf would sail through the build and
# crash the app on launch with a GI error the user cannot act on.
#
# Typelibs behind a try/except are deliberately NOT listed: the code degrades
# without them. Desktop notifications and password-field detection use none:
# notify.py talks to the notification service over D-Bus, because a bundled
# libnotify couldn't find Fedora's Notify typelib, and atspi_helper.py asks
# the accessibility bus over D-Bus too, as libatspi's typelib isn't bundled.
#
# tests/test_bundled_typelibs.py derives the unguarded set from the source and
# fails if this list drifts, so do not edit it by hand without running it.
REQUIRED_TYPELIBS=(
    "AyatanaAppIndicator3-0.1.typelib"
    "Gtk-3.0.typelib"
    "Gdk-3.0.typelib"
    "GdkPixbuf-2.0.typelib"
    "Gio-2.0.typelib"
    "Pango-1.0.typelib"
)
MISSING_TYPELIBS=()
for _tl in "${REQUIRED_TYPELIBS[@]}"; do
    [ -f "AppDir/usr/lib/girepository-1.0/${_tl}" ] || MISSING_TYPELIBS+=("$_tl")
done
if [ ${#MISSING_TYPELIBS[@]} -ne 0 ]; then
    echo "❌ ERROR: required typelib(s) missing after copy: ${MISSING_TYPELIBS[*]}"
    echo "   Searched in:"
    echo "     /usr/lib/x86_64-linux-gnu/girepository-1.0/"
    echo "     /usr/lib/girepository-1.0/"
    echo "   The AppImage would build fine and then fail on launch."
    exit 1
fi
echo "   ✅ All ${#REQUIRED_TYPELIBS[@]} required typelibs bundled successfully"

# Bundle the tray icon library itself, not just its typelib. A typelib only
# describes a library; GI still dlopen()s the real .so from the system. On any
# system without libayatana-appindicator3 installed the tray crashed on launch
# with "Could not locate app_indicator_new" (AppImageHub's test, 2026-09-26).
# Its Ayatana/dbusmenu dependencies come along; GTK itself stays the system's.
echo "   Bundling tray icon library (libayatana-appindicator3)..."
APPINDICATOR_LIB=/usr/lib/x86_64-linux-gnu/libayatana-appindicator3.so.1
if [ ! -f "$APPINDICATOR_LIB" ]; then
    echo "❌ ERROR: $APPINDICATOR_LIB not found - the tray would crash wherever it isn't installed"
    exit 1
fi
cp -L "$APPINDICATOR_LIB" AppDir/usr/lib/
for _dep in $(ldd "$APPINDICATOR_LIB" | awk '/libayatana|libdbusmenu/ {print $3}'); do
    cp -L "$_dep" AppDir/usr/lib/
    echo "     ✓ $(basename "$_dep")"
done
# Every Ayatana/dbusmenu library the tray needs must now resolve from AppDir.
if LD_LIBRARY_PATH=AppDir/usr/lib ldd AppDir/usr/lib/libayatana-appindicator3.so.1 \
        | grep -E 'libayatana|libdbusmenu' | grep -qv 'AppDir/usr/lib'; then
    echo "❌ ERROR: some tray icon dependencies were not bundled:"
    LD_LIBRARY_PATH=AppDir/usr/lib ldd AppDir/usr/lib/libayatana-appindicator3.so.1 | grep -E 'libayatana|libdbusmenu'
    exit 1
fi
echo "   ✅ Tray icon library bundled"

# Bundle PortAudio (the microphone library) as a fallback. sounddevice looks it
# up with find_library(), which ignores LD_LIBRARY_PATH, so without a system
# libportaudio TuxTalk had no microphone (AppImageHub's test, 2026-09-26).
# tuxtalk/__init__.py points find_library here only when the system has none.
#
# It lives in its own folder, NOT usr/lib: that folder is on LD_LIBRARY_PATH,
# and a bundled libjack there would override the system's (e.g. PipeWire's
# JACK) for a system PortAudio too. libjack and its libdb dependency are
# bundled because many desktops lack them; RUNPATH=$ORIGIN makes the bundled
# PortAudio find them. libasound stays the system's, as ALSA needs its config.
echo "   Bundling PortAudio fallback..."
PA_DIR=AppDir/usr/lib/portaudio
mkdir -p "$PA_DIR"
for _lib in libportaudio.so.2 libjack.so.0 libdb-5.3.so; do
    cp -L "/usr/lib/x86_64-linux-gnu/$_lib" "$PA_DIR/"
done
patchelf --set-rpath '$ORIGIN' "$PA_DIR/libportaudio.so.2" "$PA_DIR/libjack.so.0"
if ldd "$PA_DIR/libportaudio.so.2" | grep -E 'libjack|libdb' | grep -qv "$PA_DIR"; then
    echo "❌ ERROR: bundled PortAudio does not resolve its own libjack/libdb:"
    ldd "$PA_DIR/libportaudio.so.2"
    exit 1
fi
echo "   ✅ PortAudio fallback bundled"

# Copy TuxTalk source
echo "   Copying TuxTalk source..."
cp -r src/tuxtalk AppDir/usr/src/
# Normalize source permissions so no owner-only file (e.g. a 0600 .py) rides into the
# bundle unreadable. The AppImage's FUSE mount hides this, but a .deb/.rpm built from the
# same tree installs real files and a 0600 module then fails to import for the user.
chmod -R a+rX AppDir/usr/src/tuxtalk

# NOTE: GNOME extension is NOT bundled - it's downloaded from GitHub releases during onboarding
# This allows users to update the extension independently of the AppImage

# usr/bin/dictate-tray is on AppRun's PATH, so autostart.py's last-resort
# `dictate-tray` lookup can find it from an extracted AppImage. It hands over
# to AppRun, which owns the environment setup.
cat > AppDir/usr/bin/dictate-tray << 'EOF'
#!/bin/sh
DIR="$(dirname "$(readlink -f "$0")")"
exec "$DIR/../../AppRun" "$@"
EOF
chmod +x AppDir/usr/bin/dictate-tray

# Copy desktop files and icons
echo "   Adding desktop integration..."
cp io.github.ronb1964.TuxTalk.png AppDir/
cp io.github.ronb1964.TuxTalk.desktop AppDir/
cp io.github.ronb1964.TuxTalk.appdata.xml AppDir/usr/share/metainfo/

# Create AppRun
cat > AppDir/AppRun << 'EOF'
#!/bin/sh
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export PATH="${HERE}/usr/bin:${PATH}"
# No trailing ":" when LD_LIBRARY_PATH is unset: the loader reads an empty
# entry as the current folder, which is $HOME after the cd below.
export LD_LIBRARY_PATH="${HERE}/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="${HERE}/usr/src:${HERE}/usr/lib/python3.10/site-packages"
export GI_TYPELIB_PATH="${HERE}/usr/lib/girepository-1.0"
export PYTHONHOME="${HERE}/usr"
# GDK_BACKEND is deliberately NOT exported here. The recording indicator needs
# XWayland to position itself, but the tray sets that for the dictation service
# alone (tray.py _launch_service). Setting it for the whole app breaks every GTK3
# dropdown: under XWayland a combo popup does not latch open on a plain click, so
# the first-run model picker and all Preferences combos become unusable.
# Disable HuggingFace XET downloads - they bypass progress tracking
export HF_HUB_DISABLE_XET=1
cd "$HOME"
exec "${HERE}/usr/bin/python3" -m tuxtalk.tray "$@"
EOF
chmod +x AppDir/AppRun

# Every bundled program and library's license, after the last copy step and
# before the AppImage is made. Fails the build if any license is missing.
echo "   Collecting third-party licenses..."
bash packaging/collect-licenses.sh AppDir /tmp/wl-clipboard

# The Cairo C libraries stay out of the bundle (see "Do NOT bundle" above).
# Checked last, after every copy step. Fail the build if one got in: blank emoji is easy to miss.
for lib in libcairo.so.2 libcairo-gobject.so.2 libpangocairo-1.0.so.0; do
    if [ -e "AppDir/usr/lib/$lib" ]; then
        echo "     ❌ $lib is bundled; it hides the system's and blanks every emoji"
        exit 1
    fi
done

# Download and extract appimagetool - FUSE does not work in containers.
# This is the current tool from github.com/AppImage/appimagetool, NOT the
# retired AppImageKit one. The old tool embedded a runtime that needs libfuse2
# on the user's system; Ubuntu 26.04 no longer ships it, so the AppImage (and
# the menu/autostart entries pointing at it) silently failed to start there.
# The type2 runtime it is given below is static and needs no libfuse2.
# Both are tagged releases checked by SHA256, never "continuous": the runtime
# is the first thing a user's computer runs, and an unpinned one would change
# the AppImage between two builds of the same source. The downloads are kept
# in the project folder, but each build checks and extracts its own copy, so
# nothing left there from before (an older tool, a changed file) is run.
APPIMAGETOOL_VERSION=1.9.1
APPIMAGETOOL_SHA256=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
TYPE2_RUNTIME_VERSION=20251108
TYPE2_RUNTIME_SHA256=2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d
APPIMAGETOOL_CACHE="appimagetool-${APPIMAGETOOL_VERSION}-x86_64.AppImage"
TYPE2_RUNTIME_CACHE="type2-runtime-${TYPE2_RUNTIME_VERSION}-x86_64"
echo "   Getting appimagetool $APPIMAGETOOL_VERSION and type2 runtime $TYPE2_RUNTIME_VERSION..."
TOOLS_DIR=$(mktemp -d)
pinned_download \
    "https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage" \
    "$APPIMAGETOOL_SHA256" "$APPIMAGETOOL_CACHE" "$TOOLS_DIR/appimagetool.AppImage"
pinned_download \
    "https://github.com/AppImage/type2-runtime/releases/download/${TYPE2_RUNTIME_VERSION}/runtime-x86_64" \
    "$TYPE2_RUNTIME_SHA256" "$TYPE2_RUNTIME_CACHE" "$TOOLS_DIR/runtime"
chmod +x "$TOOLS_DIR/appimagetool.AppImage"
(cd "$TOOLS_DIR" && ./appimagetool.AppImage --appimage-extract > /dev/null 2>&1)
APPIMAGETOOL="$TOOLS_DIR/squashfs-root/AppRun"

# Update information, so AppImageUpdate and similar tools can update TuxTalk
# by downloading only the changed parts. It points at the .zsync file in the
# LATEST GitHub release, so every release must upload the .zsync that
# appimagetool writes next to the AppImage (zsyncmake, from the zsync package).
# AppImageHub's test warned about its absence (AppImage/appimage.github.io#4598).
# TuxTalk's own "Check for Updates" doesn't use this; it is for outside tools.
UPDATE_INFO="gh-releases-zsync|ronb1964|TuxTalk|latest|TuxTalk-*x86_64.AppImage.zsync"
ZSYNC_FILE="TuxTalk-v${VERSION}-x86_64.AppImage.zsync"
rm -f "$ZSYNC_FILE"

echo "   Creating AppImage..."
ARCH=x86_64 "$APPIMAGETOOL" --no-appstream --runtime-file "$TOOLS_DIR/runtime" \
    -u "$UPDATE_INFO" \
    AppDir "TuxTalk-v${VERSION}-x86_64.AppImage" > /tmp/appimagetool.log 2>&1 \
    || { echo "❌ ERROR: appimagetool failed:"; cat /tmp/appimagetool.log; exit 1; }
if [ ! -f "$ZSYNC_FILE" ]; then
    echo "❌ ERROR: $ZSYNC_FILE was not created, so the embedded update information"
    echo "   would point at a file the release doesn't have. appimagetool said:"
    cat /tmp/appimagetool.log
    exit 1
fi
echo "   ✅ Update information embedded, $ZSYNC_FILE written"

# Installs from before the 0.15.0 rename carry the update information
# "...|TalkType|latest|TalkType-*x86_64.AppImage.zsync", so AppImageUpdate
# looks for a TalkType-named .zsync in the latest release. Publish one that
# points at the TuxTalk AppImage, or those installs can never update.
COMPAT_ZSYNC="TalkType-v${VERSION}-x86_64.AppImage.zsync"
rm -f "$COMPAT_ZSYNC"
zsyncmake -u "TuxTalk-v${VERSION}-x86_64.AppImage" -o "$COMPAT_ZSYNC" \
    "TuxTalk-v${VERSION}-x86_64.AppImage" > /dev/null \
    || { echo "❌ ERROR: could not write $COMPAT_ZSYNC for pre-rename installs"; exit 1; }
echo "   ✅ $COMPAT_ZSYNC written for installs from before the rename"

# Fix ownership of AppImage output
if [ -n "$BUILD_USER" ]; then
    # Not fatal — the AppImage exists either way — but if this fails the file is
    # left owned by root and the host build script cannot copy or delete it
    # without sudo, which is worth saying out loud rather than hiding.
    chown $BUILD_USER:$BUILD_GROUP /build/TuxTalk-v${VERSION}-x86_64.AppImage \
        "/build/$ZSYNC_FILE" "/build/$COMPAT_ZSYNC" 2>/dev/null \
        || echo "   ⚠️  Warning: could not chown the AppImage to $BUILD_USER:$BUILD_GROUP (it stays root-owned)"
    # The downloads kept for the next build too; one pinned_download couldn't
    # keep isn't there.
    for cache in "$WAYLAND_PROTOCOLS_CACHE" "$APPIMAGETOOL_CACHE" "$TYPE2_RUNTIME_CACHE"; do
        if [ -f "/build/$cache" ]; then
            chown "$BUILD_USER:$BUILD_GROUP" "/build/$cache" 2>/dev/null \
                || echo "   ⚠️  Warning: could not chown $cache (it stays root-owned)"
        fi
    done
fi

# Clean up build artifacts inside container
echo "   Cleaning up build artifacts..."
rm -rf AppDir

echo ""
echo "✅ Build complete! AppImage is ready."
echo "   Build artifacts cleaned up (AppDir/ removed)"
echo "   NOTE: .venv is NOT removed - this is the user's dev environment!"
