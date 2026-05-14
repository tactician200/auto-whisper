#!/usr/bin/env bash
#
# install-v5.sh — switch the active auto-whisper LaunchAgent from v4.2 to v5.
#
# What it does (in order):
#   1. Validate the v5 repo + venv are in the expected location.
#   2. plutil-validate the v5 plist before touching launchd.
#   3. If v4.2 plist is currently loaded, unload it (file untouched on disk —
#      revert-to-v4.sh can reload it later).
#   4. Copy v5 plist to ~/Library/LaunchAgents/.
#   5. launchctl load the v5 plist.
#   6. Print a summary + the exact command to roll back if needed.
#
# Reversible: ALL state changes are launchctl unload/load + a file copy. No
# destructive ops, no deletion of the v4.2 plist file. Roll back any time
# with scripts/revert-to-v4.sh.
#
# Run with `make install-v5` or directly: bash scripts/install-v5.sh

set -euo pipefail

V5_REPO="/Users/stj/src/auto-whisper-v5"
V5_PLIST_SRC="${V5_REPO}/com.auto-whisper.v5.plist"
V5_PLIST_DEST="${HOME}/Library/LaunchAgents/com.auto-whisper.v5.plist"
V5_VENV_PYTHON="${V5_REPO}/.venv/bin/python"
V5_LOG_DIR="${HOME}/Library/Logs/auto-whisper-v5"

V4_LABEL="com.auto-whisper"
V5_LABEL="com.auto-whisper.v5"

# --- 1. Validate ---

echo "▸ Validating v5 setup..."
if [[ ! -d "${V5_REPO}" ]]; then
    echo "✗ v5 repo not found at ${V5_REPO}" >&2
    exit 1
fi
if [[ ! -x "${V5_VENV_PYTHON}" ]]; then
    echo "✗ v5 venv Python not found at ${V5_VENV_PYTHON}" >&2
    echo "  Did you run 'cd ${V5_REPO} && python3 -m venv .venv && .venv/bin/pip install -e .'?" >&2
    exit 1
fi
if [[ ! -f "${V5_PLIST_SRC}" ]]; then
    echo "✗ v5 plist source not found at ${V5_PLIST_SRC}" >&2
    exit 1
fi

if ! /usr/bin/plutil -lint "${V5_PLIST_SRC}" >/dev/null; then
    echo "✗ v5 plist failed plutil validation — fix the file before installing" >&2
    exit 1
fi

mkdir -p "${V5_LOG_DIR}"
echo "  ✓ Repo, venv, plist, log dir all OK"

# --- 2. Unload v4.2 if active ---

echo
echo "▸ Checking v4.2 LaunchAgent state..."
if launchctl list | grep -q "${V4_LABEL}\$"; then
    V4_PLIST="${HOME}/Library/LaunchAgents/com.auto-whisper.plist"
    if [[ -f "${V4_PLIST}" ]]; then
        echo "  v4.2 is loaded — unloading (file at ${V4_PLIST} preserved)..."
        launchctl unload "${V4_PLIST}"
        echo "  ✓ v4.2 unloaded"
    else
        echo "  ! v4.2 label present but plist file missing — skipping unload"
    fi
else
    echo "  v4.2 is not loaded — nothing to unload"
fi

# --- 3. Install + load v5 ---

echo
echo "▸ Installing v5 LaunchAgent..."
mkdir -p "$(dirname "${V5_PLIST_DEST}")"
cp "${V5_PLIST_SRC}" "${V5_PLIST_DEST}"
echo "  ✓ Copied plist to ${V5_PLIST_DEST}"

# If v5 was somehow already loaded, unload first to pick up updated config.
if launchctl list | grep -q "${V5_LABEL}\$"; then
    launchctl unload "${V5_PLIST_DEST}"
fi

launchctl load "${V5_PLIST_DEST}"
echo "  ✓ v5 loaded"

# --- 4. Verify ---

sleep 1  # give launchd a moment to start the process
if launchctl list | grep -q "${V5_LABEL}\$"; then
    echo "  ✓ v5 process is running"
else
    echo "  ! v5 not visible in launchctl list — check ${V5_LOG_DIR}/auto-whisper-v5.err"
fi

# --- 5. Summary ---

cat <<EOF

────────────────────────────────────────────────────────────
✓ auto-whisper v5 is now your active LaunchAgent.

Logs:  ${V5_LOG_DIR}/
Plist: ${V5_PLIST_DEST}

Roll back to v4.2 any time:
    bash ${V5_REPO}/scripts/revert-to-v4.sh

Tail v5 logs:
    tail -f ${V5_LOG_DIR}/auto-whisper-v5.err
────────────────────────────────────────────────────────────
EOF
