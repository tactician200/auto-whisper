#!/usr/bin/env bash
#
# revert-to-v4.sh — switch the active LaunchAgent back from v5 to v4.2.
#
# Inverse of install-v5.sh. Unloads v5 (file stays on disk so you can
# re-install with `make install-v5` later), then loads v4.2.
#
# Run with `make revert-to-v4` or directly: bash scripts/revert-to-v4.sh

set -euo pipefail

V5_PLIST_DEST="${HOME}/Library/LaunchAgents/com.auto-whisper.v5.plist"
V4_PLIST="${HOME}/Library/LaunchAgents/com.auto-whisper.plist"

V4_LABEL="com.auto-whisper"
V5_LABEL="com.auto-whisper.v5"

# --- 1. Unload v5 ---

echo "▸ Unloading v5 LaunchAgent..."
if [[ -f "${V5_PLIST_DEST}" ]] && launchctl list | grep -q "${V5_LABEL}\$"; then
    launchctl unload "${V5_PLIST_DEST}"
    echo "  ✓ v5 unloaded (plist file preserved at ${V5_PLIST_DEST})"
else
    echo "  v5 not loaded — nothing to unload"
fi

# --- 2. Load v4.2 ---

echo
echo "▸ Reloading v4.2 LaunchAgent..."
if [[ ! -f "${V4_PLIST}" ]]; then
    echo "✗ v4.2 plist not found at ${V4_PLIST}" >&2
    echo "  Cannot auto-revert — your v4.2 install is gone." >&2
    exit 1
fi

if launchctl list | grep -q "${V4_LABEL}\$"; then
    echo "  v4.2 already loaded — skipping"
else
    launchctl load "${V4_PLIST}"
    echo "  ✓ v4.2 loaded"
fi

cat <<EOF

────────────────────────────────────────────────────────────
✓ auto-whisper v4.2 is your active LaunchAgent again.

To switch back to v5 later:
    cd /Users/stj/src/auto-whisper-v5 && make install-v5
────────────────────────────────────────────────────────────
EOF
