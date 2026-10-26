#!/bin/bash
# TuxTalk Fresh Start Script - Cleans the AppImage environment
# Dev's settings and data (~/.config/tuxtalk-dev/ and ~/.local/share/TuxTalk-dev/,
# plus their pre-rename talktype-dev / TalkType-dev links) are kept. The speech
# and AI models in ~/.cache/huggingface are shared with dev, so they go too and
# dev downloads them again.
# Since the 0.15.0 rename it clears both names: TuxTalk and the old TalkType.
#
#   ./fresh-start-for-testing.sh                # clean up before a release test
#   ./fresh-start-for-testing.sh --restore-dev  # after testing: dev starts at login again

set -e

DEV_AUTOSTART=(~/.config/autostart/tuxtalk-dev.desktop ~/.config/autostart/talktype-dev.desktop)

if [ "$1" = "--restore-dev" ]; then
    restored=no
    for f in "${DEV_AUTOSTART[@]}"; do
        if [ -f "$f" ]; then
            sed -i 's/X-GNOME-Autostart-enabled=false/X-GNOME-Autostart-enabled=true/' "$f"
            restored=yes
        fi
    done
    if [ "$restored" = yes ]; then
        echo "✓ Dev autostart turned back on: the dev version starts at the next login"
    else
        echo "ℹ️  No dev autostart entry found, nothing to restore"
    fi
    exit 0
fi

echo "🧹 TuxTalk AppImage Fresh Start Cleanup"
echo "========================================"
echo ""
echo "NOTE: This cleans the AppImage environment. Your dev settings and data stay;"
echo "the shared speech and AI models go, and dev downloads them again."
echo ""

# 1. Kill all TuxTalk (and old TalkType) processes
echo "1. Stopping all TuxTalk processes..."
# Only the app's own programs: its Python modules (dev, AppImage, .deb/.rpm),
# a running AppImage and anything inside its mount, and the speech and AI
# engine servers it downloaded. A looser match on "tuxtalk" would also kill
# an editor, a `tail -f` of the log or a shell whose command names the folder.
APP_PAT='-m (tuxtalk|talktype)\.|/tmp/\.mount_(TuxTal|TalkTy)|(TuxTalk|TalkType)[^ /]*\.AppImage|/\.local/share/(TuxTalk|TalkType)[^ ]*/(whisper|llama)-server'
pkill -f -- "$APP_PAT" 2>/dev/null || true
sleep 2
survivors=$(pgrep -f -- "$APP_PAT" || true)
if [ -n "$survivors" ]; then
    echo "   ⚠️  Some processes still running, force killing..."
    # shellcheck disable=SC2086  # one PID per word
    kill -9 $survivors 2>/dev/null || true
    sleep 1
fi
echo "   ✓ All processes stopped"

# 2. Remove AppImage config directory (NOT -dev!)
echo "2. Removing AppImage config directory..."
rm -rf ~/.config/talktype ~/.config/tuxtalk
echo "   ✓ AppImage config removed: ~/.config/tuxtalk (and the old ~/.config/talktype)"
echo "   ℹ️  Dev config preserved: ~/.config/tuxtalk-dev/"

# 3. Remove AppImage data directory (NOT -dev!)
echo "3. Removing AppImage data directory..."
rm -rf ~/.local/share/talktype
rm -rf ~/.local/share/TalkType ~/.local/share/TuxTalk
echo "   ✓ AppImage data removed: ~/.local/share/TuxTalk (and the old TalkType)"
echo "   ℹ️  Dev data preserved: ~/.local/share/TuxTalk-dev/"
echo "   ✓ AppImage CUDA libraries removed"
echo "   ℹ️  Dev CUDA libraries preserved (if you had them)"

# 4. Remove Hugging Face model cache (including xet chunk cache)
echo "4. Removing Hugging Face model cache..."
# Every model TuxTalk downloads, so first run really downloads them again:
# Whisper for the processor and CUDA (Systran), Parakeet for the processor
# (istupakov) and for Vulkan (ggml-org), Whisper for Vulkan (ggerganov), and
# the self-corrections AI (Qwen). Parakeet became the default in 0.14.0 and
# was never removed here, so release tests skipped its download. Other apps'
# models in the same cache are left alone.
TALKTYPE_MODEL_REPOS=(
    "models--Systran--faster-whisper-*"
    "models--istupakov--parakeet-tdt-0.6b-v3-onnx"
    "models--ggml-org--parakeet-GGUF"
    "models--ggerganov--whisper.cpp"
    "models--Qwen--Qwen2.5-1.5B-Instruct-GGUF"
)
if [ -d ~/.cache/huggingface/hub ]; then
    for repo in "${TALKTYPE_MODEL_REPOS[@]}"; do
        rm -rf ~/.cache/huggingface/hub/$repo ~/.cache/huggingface/hub/.locks/$repo 2>/dev/null || true
    done
    echo "   ✓ All TuxTalk models removed (Whisper, Parakeet, Vulkan and AI - will be re-downloaded)"
else
    echo "   ✓ No model cache found"
fi
# Hugging Face's xet chunk cache (~/.cache/huggingface/xet) is left alone:
# TuxTalk downloads with HF_HUB_DISABLE_XET=1 and never fills it, so only
# other apps' downloads are in it.

# 5. Uninstall GNOME extension (both via gnome-extensions and direct folder removal)
echo "5. Uninstalling GNOME extension..."
gnome-extensions uninstall talktype@ronb1964.github.io 2>/dev/null || true
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/talktype@ronb1964.github.io"
if [ -d "$EXT_DIR" ]; then
    rm -rf "$EXT_DIR"
    echo "   ✓ Extension directory removed"
else
    echo "   ✓ Extension not installed"
fi

# 6. Remove the AppImage's menu launcher, icon and autostart; pause dev autostart
echo "6. Removing the AppImage's launcher, icon and autostart..."
rm -f ~/.config/autostart/talktype.desktop ~/.config/autostart/tuxtalk.desktop
echo "   ✓ AppImage autostart removed"
# First run creates the menu launcher and installs the icon only when the
# launcher is missing, so both go, or that step is never tested. The dev
# launcher (tuxtalk-dev.desktop) points at an icon in the project folder and
# is left alone.
rm -f ~/.local/share/applications/tuxtalk.desktop ~/.local/share/applications/talktype.desktop
for size in 256x256 128x128 64x64 48x48 32x32; do
    rm -f ~/.local/share/icons/hicolor/$size/apps/tuxtalk.png ~/.local/share/icons/hicolor/$size/apps/talktype.png
done
if command -v update-desktop-database > /dev/null; then
    update-desktop-database -q ~/.local/share/applications 2>/dev/null || true
fi
if command -v gtk-update-icon-cache > /dev/null; then
    gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor 2>/dev/null || true
fi
echo "   ✓ AppImage menu launcher and icon removed"
# Pause dev autostart so only the AppImage starts at the next login during
# testing. --restore-dev turns it back on.
dev_found=no
for f in "${DEV_AUTOSTART[@]}"; do
    if [ -f "$f" ]; then
        sed -i 's/X-GNOME-Autostart-enabled=true/X-GNOME-Autostart-enabled=false/' "$f"
        dev_found=yes
    fi
done
if [ "$dev_found" = yes ]; then
    echo "   ✓ Dev autostart turned off for testing"
else
    echo "   ℹ️  Dev autostart not found, skipping"
fi

# 7. Verify cleanup
echo ""
echo "🔍 Verification:"
[ ! -e ~/.config/tuxtalk ] && [ ! -e ~/.config/talktype ] && echo "   ✓ Config dir removed" || echo "   ❌ Config dir still exists!"
[ ! -e ~/.local/share/TuxTalk ] && [ ! -e ~/.local/share/TalkType ] && echo "   ✓ Data dir removed" || echo "   ❌ Data dir still exists!"
left=$(for repo in "${TALKTYPE_MODEL_REPOS[@]}"; do ls -d ~/.cache/huggingface/hub/$repo 2>/dev/null || true; done)
[ -z "$left" ] && echo "   ✓ TuxTalk models removed" || echo "   ❌ Models still there: $left"
[ ! -e ~/.local/share/applications/tuxtalk.desktop ] && echo "   ✓ Menu launcher removed" || echo "   ❌ Menu launcher still exists!"
! pgrep -f -- "$APP_PAT" > /dev/null && echo "   ✓ No processes running" || echo "   ❌ Processes still running!"

echo ""
echo "✅ Fresh start complete! Ready for first-run testing."
echo ""
echo "To test: run the latest ./TuxTalk-v*-x86_64.AppImage build"
echo "Left in place on purpose: ~/AppImages/TuxTalk.AppImage and ~/AppImages/TalkType.AppImage, if present."
if [ "$dev_found" = yes ]; then
    echo "After testing, turn dev autostart back on: ./fresh-start-for-testing.sh --restore-dev"
fi
