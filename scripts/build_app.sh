#!/usr/bin/env bash
# Build the AutoWhisper.app bundle end-to-end.
#
# Steps:
#   1. Render the app icon (assets/AppIcon.icns) if missing.
#   2. Run py2app to produce dist/AutoWhisper.app.
#   3. Apply post-build patches (sounddevice + .env + re-sign).
#
# After this, install to /Applications/ via:
#     rm -rf /Applications/AutoWhisper.app
#     cp -R dist/AutoWhisper.app /Applications/
#
# And (re)load the LaunchAgent:
#     launchctl bootout "gui/$(id -u)/com.auto-whisper.v5" 2>/dev/null || true
#     launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.auto-whisper.v5.plist

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "ERROR: $VENV_PY not found. Create the venv and pip install -r requirements.txt first."
  exit 1
fi

# 1. Render icon if missing — re-render manually if you change the design.
if [ ! -f "assets/AppIcon.icns" ]; then
  echo "→ Rendering app icon (one-time)..."
  "$VENV_PY" scripts/render_app_icon.py
fi

# 2. py2app — clean build every time, this is fast and avoids stale-cache bugs.
echo
echo "→ py2app build..."
rm -rf build dist
"$VENV_PY" setup_app.py py2app 2>&1 | tail -3

# 3. Postbuild patches (sounddevice extraction, .env copy, re-codesign).
echo
echo "→ Postbuild patches..."
bash scripts/postbuild_app.sh

echo
echo "✓ dist/AutoWhisper.app ready ($(du -sh dist/AutoWhisper.app | cut -f1))"
echo
echo "Install:"
echo "  rm -rf /Applications/AutoWhisper.app && cp -R dist/AutoWhisper.app /Applications/"
echo
echo "Reload LaunchAgent:"
echo "  launchctl bootout \"gui/\$(id -u)/com.auto-whisper.v5\" 2>/dev/null || true"
echo "  launchctl bootstrap \"gui/\$(id -u)\" ~/Library/LaunchAgents/com.auto-whisper.v5.plist"
