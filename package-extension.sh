#!/bin/bash
# Package GNOME Extension for GitHub Release
#
# Creates tuxtalk-gnome-extension.zip from the source extension directory
# This zip is downloaded by the welcome dialog during first-run setup.
# It also writes talktype-gnome-extension.zip, the same zip under its name
# before the 0.15.0 rename: TalkType installs download it by that exact name.
# The folder inside keeps the extension's UUID, talktype@ronb1964.github.io
# (a new UUID would be a new extension: a logout and enabling it again).

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTENSION_DIR="$SCRIPT_DIR/gnome-extension/talktype@ronb1964.github.io"
OUTPUT_ZIP="$SCRIPT_DIR/tuxtalk-gnome-extension.zip"
COMPAT_ZIP="$SCRIPT_DIR/talktype-gnome-extension.zip"

echo "📦 Packaging GNOME Extension..."
echo "================================"

# Verify source directory exists
if [ ! -d "$EXTENSION_DIR" ]; then
    echo "❌ Error: Extension source directory not found: $EXTENSION_DIR"
    exit 1
fi

# Verify required files exist
REQUIRED_FILES=("extension.js" "metadata.json" "stylesheet.css" "shellIndicator.js")
for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$EXTENSION_DIR/$file" ]; then
        echo "❌ Error: Required file not found: $file"
        exit 1
    fi
done

echo "✅ Source directory verified: $EXTENSION_DIR"
echo ""
echo "📄 Files to package:"
ls -lh "$EXTENSION_DIR"
echo ""

# Remove old zip if it exists
if [ -f "$OUTPUT_ZIP" ]; then
    echo "🗑️  Removing old zip file..."
    rm "$OUTPUT_ZIP"
fi

# Create the zip file
# The zip must contain the UUID directory as the root
echo "📦 Creating zip file..."
cd "$SCRIPT_DIR/gnome-extension"

# Remove any existing zip in this directory
rm -f tuxtalk-gnome-extension.zip talktype@ronb1964.github.io.zip

# Create fresh zip
# NOTE: the exclude patterns must not match the extension's own UUID directory.
# "talktype@ronb1964.github.io" contains the substring ".git" (from "github.io"),
# so a pattern of "*.git*" matches EVERY path in the archive and zip exits with
# "Nothing to do!" — after the old zip has already been deleted above. Anchor the
# git patterns on a leading slash so they only match real git metadata.
zip -r tuxtalk-gnome-extension.zip talktype@ronb1964.github.io \
    -x "*/.git/*" \
    -x "*/.gitignore" \
    -x "*~" \
    -x "*.swp"

# A missing imported module installs as a broken extension.
for file in "${REQUIRED_FILES[@]}"; do
    if ! unzip -Z1 tuxtalk-gnome-extension.zip | grep -Fxq "talktype@ronb1964.github.io/$file"; then
        echo "❌ Error: $file missing from extension zip. Check the -x exclude patterns."
        exit 1
    fi
done

# Move to project root
mv tuxtalk-gnome-extension.zip "$OUTPUT_ZIP"
cp "$OUTPUT_ZIP" "$COMPAT_ZIP"

# Verify the zip was created
if [ ! -f "$OUTPUT_ZIP" ]; then
    echo "❌ Error: Failed to create zip file"
    exit 1
fi

# Refresh the extension's entry in SHA256SUMS.txt so the checksums file
# published with the release always matches the freshly-packaged zip.
cd "$SCRIPT_DIR"
# shellcheck source=packaging/checksums.sh
source "$SCRIPT_DIR/packaging/checksums.sh"
replace_checksums "$SCRIPT_DIR" "tuxtalk-gnome-extension.zip" "talktype-gnome-extension.zip"
echo "🔐 Updated SHA256SUMS.txt entries for the extension zips"

# Show zip contents
echo ""
echo "✅ Extension packaged successfully!"
echo "📦 Output: $OUTPUT_ZIP"
echo "📊 Size: $(du -h "$OUTPUT_ZIP" | cut -f1)"
echo ""
echo "📋 Zip contents:"
unzip -l "$OUTPUT_ZIP"
echo ""
echo "✅ Done!"
echo ""
echo "📤 Next steps:"
echo "   1. Test the extension locally:"
echo "      unzip -l $OUTPUT_ZIP"
echo ""
echo "   2. Upload to GitHub release:"
echo "      gh release upload vX.X.X $OUTPUT_ZIP $COMPAT_ZIP --clobber"
echo ""
echo "   3. Verify download URL:"
echo "      https://github.com/ronb1964/TuxTalk/releases/latest/download/tuxtalk-gnome-extension.zip"
