#!/bin/bash
# Shared by build-deb.sh and build-rpm.sh, which repackage the release
# AppImage instead of building TuxTalk a second way: ./build-release.sh
# already assembles a self-contained runtime (its own Python, GTK bindings,
# faster-whisper/CTranslate2, wl-clipboard) on Ubuntu 22.04 for wide glibc
# compatibility, and that finished tree is what a package needs. Each builder
# keeps what differs: where the license goes, the dependency names and fpm.
#
# The tree both packages install:
#   /opt/talktype/            <- the whole extracted AppImage tree (AppRun + usr/)
#   /usr/bin/tuxtalk          <- tiny launcher: exec /opt/talktype/AppRun "$@"
#   /usr/bin/talktype         <- the command before the 0.15.0 rename
#   /usr/share/applications/  <- desktop entry (Exec=tuxtalk tray)
#   /usr/share/icons/hicolor/scalable/apps/  <- the SVG icon
#   /usr/share/pixmaps/       <- the PNG icon

# require_fpm
# fpm is commonly installed to ~/bin or the user gem bin dir.
require_fpm() {
    export PATH="$HOME/bin:$PATH"
    if ! command -v fpm >/dev/null 2>&1; then
        echo "❌ fpm not found. Install it with:  gem install --user-install fpm" >&2
        return 1
    fi
}

# project_version PROJECT_DIR
# Prints the version from pyproject.toml, the one source build-release.sh uses.
project_version() {
    local version
    version=$(grep -E "^version = " "$1/pyproject.toml" | sed 's/version = "\(.*\)"/\1/')
    if [ -z "$version" ]; then
        echo "❌ Could not read version from pyproject.toml" >&2
        return 1
    fi
    echo "$version"
}

# release_appimage PROJECT_DIR VERSION
# Prints the path of the AppImage build-release.sh made for VERSION.
release_appimage() {
    local appimage="$1/TuxTalk-v$2-x86_64.AppImage"
    if [ ! -f "$appimage" ]; then
        echo "❌ AppImage not found: $appimage" >&2
        echo "   Run ./build-release.sh first to produce it." >&2
        return 1
    fi
    echo "$appimage"
}

# stage_tree APPIMAGE WORK PROJECT_DIR
# Extracts APPIMAGE into the empty folder WORK and stages the tree above in
# WORK/stage. Refuses an AppImage built without its third-party licenses
# (packaging/collect-licenses.sh puts them in its tree).
stage_tree() {
    local appimage="$1" work="$2" project="$3"
    local tree="$work/squashfs-root" stage="$work/stage" licenses f
    echo "📂 Extracting the AppImage..."
    ( cd "$work" && "$appimage" --appimage-extract >/dev/null 2>&1 )
    [ -d "$tree" ] || { echo "❌ extraction failed" >&2; return 1; }

    echo "🏗️  Staging the package's files..."
    mkdir -p "$stage/opt" "$stage/usr/bin" "$stage/usr/share/applications" \
        "$stage/usr/share/pixmaps"
    # AppRun derives its paths from where it sits (${HERE}/usr/...), so it
    # works unchanged here. The folder keeps its name from before the 0.15.0
    # rename (nobody sees it): upgrades then replace files in place, like
    # every earlier one, instead of deleting the folder a running 0.14.x
    # tray still imports from.
    mv "$tree" "$stage/opt/talktype"

    licenses="$stage/opt/talktype/usr/share/licenses"
    for f in tuxtalk/LICENSE tuxtalk/THIRD_PARTY_LICENSES.md wl-clipboard/COPYING; do
        [ -f "$licenses/$f" ] || {
            echo "❌ The AppImage has no $f: rebuild it with ./build-release.sh" >&2
            return 1
        }
    done

    # Reuses the tested AppRun rather than repeating how it sets things up.
    cat > "$stage/usr/bin/tuxtalk" <<'LAUNCHER'
#!/bin/sh
# TuxTalk system launcher — hands off to the bundled AppRun, which sets
# PYTHONHOME/PYTHONPATH/LD_LIBRARY_PATH/GI_TYPELIB_PATH relative to itself.
exec /opt/talktype/AppRun "$@"
LAUNCHER
    chmod +x "$stage/usr/bin/tuxtalk"
    # Old shortcuts, scripts and a 0.14.x updater (which restarts
    # /usr/bin/talktype after installing this) still use the old command.
    ln -s tuxtalk "$stage/usr/bin/talktype"

    # The AppImage's desktop entry, every Exec pointing at the launcher; the
    # shortcuts' lines keep their own arguments (--open preferences, --quit).
    sed 's/^Exec=AppRun/Exec=tuxtalk/' "$project/io.github.ronb1964.TuxTalk.desktop" \
        > "$stage/usr/share/applications/io.github.ronb1964.TuxTalk.desktop"
    cp "$project/io.github.ronb1964.TuxTalk.png" "$stage/usr/share/pixmaps/"
    # The app menu and the first-run splash screen (which prefers SVG) look
    # here: without it the splash shows plain "TuxTalk" text, not the logo.
    install -Dm644 "$project/icons/OFFICIAL_ICON_DO_NOT_CHANGE.svg" \
        "$stage/usr/share/icons/hicolor/scalable/apps/io.github.ronb1964.TuxTalk.svg"
}

# finish_stage STAGE POSTINST
# Makes every staged file readable and writes the after-install script to
# POSTINST. Called once the builder has added its own files to STAGE.
finish_stage() {
    local stage="$1" postinst="$2"
    # A stray owner-only file (a 0600 .py) would land as root:root 0600 and
    # break imports for the user: update_checker.py once did, and took
    # Preferences, About and Check for Updates with it. Execute stays only
    # where it was set (the launchers, Python).
    chmod -R a+rX "$stage"
    # Refreshes the icon and desktop caches; never fails the install.
    cat > "$postinst" <<'POST'
#!/bin/sh
set -e
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -f /usr/share/icons/hicolor 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications 2>/dev/null || true
exit 0
POST
}
