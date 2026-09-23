#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Version bump ──
VERSION_CODE=$(grep -oP 'versionCode\s+\K\d+' app/build.gradle || echo 12)
VERSION_NAME=$(grep -oP 'versionName\s+"\K[^"]+' app/build.gradle || echo "7.0.5")
echo "Building Lilly Overlay v${VERSION_NAME} (code ${VERSION_CODE})"

# ── Clean previous build ──
./gradlew clean

# ── Assemble debug APK ──
./gradlew assembleDebug

# ── Locate built APK ──
# Check standard gradle output, custom buildDir, and legacy .build dir
APK_PATH=""
for candidate in \
    "app/build/outputs/apk/debug/app-debug.apk" \
    "/tmp/lilly-overlay-build/app/outputs/apk/debug/app-debug.apk" \
    "app/.build/app/outputs/apk/debug/app-debug.apk"; do
    if [ -f "$candidate" ]; then
        APK_PATH="$candidate"
        break
    fi
done

if [ -z "$APK_PATH" ]; then
    echo "ERROR: APK not found after build"
    exit 1
fi

# ── Copy to download directory ──
DEST_DIR="/home/labhrasd/Lilly_Workspace/app/api/download"
mkdir -p "$DEST_DIR"
DEST_FILE="${DEST_DIR}/lilly-overlay-v${VERSION_NAME}-overlay-debug.apk"
cp -v "$APK_PATH" "$DEST_FILE"

SHARE_DIR="/home/labhrasd/Lilly_Workspace/file_share"
mkdir -p "$SHARE_DIR"
SHARE_FILE="${SHARE_DIR}/lilly-overlay-v${VERSION_NAME}-debug.apk"
cp -v "$APK_PATH" "$SHARE_FILE"

# ── Also copy to builds directory ──
BUILDS_DIR="/home/labhrasd/Lilly_Workspace/builds"
mkdir -p "$BUILDS_DIR"
BUILDS_FILE="${BUILDS_DIR}/lilly-overlay-v${VERSION_NAME}-overlay-debug.apk"
cp -v "$APK_PATH" "$BUILDS_FILE"

echo ""
echo "✓ Build complete"
echo "  APK: $DEST_FILE"
echo "  Size: $(du -h "$DEST_FILE" | cut -f1)"
