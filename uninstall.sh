#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  auto-whisper uninstaller
#
#  Removes the LaunchAgent, optionally wipes venv/logs/API key.
#  Code directory itself is preserved — delete it manually if you want.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$INSTALL_DIR/.venv"
ENV_FILE="$INSTALL_DIR/.env"
LOG_DIR="$HOME/Library/Logs/auto-whisper"
PLIST_LABEL="com.auto-whisper"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo
echo "  ◎ auto-whisper uninstaller"
echo "  ──────────────────────────"
echo

# ─── 1. Stop + remove LaunchAgent ────────────────────────────────────
if [[ -e "$PLIST_DST" ]]; then
    if launchctl list | grep -q "[[:space:]]$PLIST_LABEL\$"; then
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        echo "  ✓ LaunchAgent unloaded"
    fi
    rm -f "$PLIST_DST"
    echo "  ✓ Removed $PLIST_DST"
else
    echo "  · No LaunchAgent at $PLIST_DST (already removed)"
fi

# ─── 2. Optional: venv ───────────────────────────────────────────────
if [[ -d "$VENV_DIR" ]]; then
    read -rp "  Delete the venv ($VENV_DIR, ~200MB)? [y/N] " ans
    if [[ "$ans" =~ ^[Yy] ]]; then
        rm -rf "$VENV_DIR"
        echo "  ✓ venv deleted"
    fi
fi

# ─── 3. Optional: API key ────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
    read -rp "  Delete .env (your Groq API key)? [y/N] " ans
    if [[ "$ans" =~ ^[Yy] ]]; then
        rm -f "$ENV_FILE"
        echo "  ✓ .env deleted"
    else
        echo "  · .env kept at $ENV_FILE"
    fi
fi

# ─── 4. Optional: logs ───────────────────────────────────────────────
if [[ -d "$LOG_DIR" ]]; then
    read -rp "  Delete logs ($LOG_DIR)? [y/N] " ans
    if [[ "$ans" =~ ^[Yy] ]]; then
        rm -rf "$LOG_DIR"
        echo "  ✓ Logs deleted"
    fi
fi

cat <<EOF

  ✓ auto-whisper uninstalled.

  The code directory ($INSTALL_DIR) is left untouched.
  Delete it manually if you no longer need it:
    rm -rf "$INSTALL_DIR"

  Also consider revoking permissions in:
    System Settings → Privacy & Security → Microphone
    System Settings → Privacy & Security → Accessibility
EOF
echo
