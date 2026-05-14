#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  auto-whisper v5 installer
#  macOS menu-bar dictation: double-tap Right ⌘ → speak → text at cursor
#
#  Usage:
#    git clone https://github.com/tactician200/auto-whisper.git ~/auto-whisper
#    cd ~/auto-whisper && bash install.sh
#
#  Reversible — uninstall.sh undoes everything.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$INSTALL_DIR/.venv"
ENV_FILE="$INSTALL_DIR/.env"
LOG_DIR="$HOME/Library/Logs/auto-whisper"
PLIST_LABEL="com.auto-whisper"
PLIST_SRC="$INSTALL_DIR/$PLIST_LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo
echo "  ◎ auto-whisper v5 installer"
echo "  ───────────────────────────"
echo

# ─── 1. Platform ─────────────────────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
    echo "  ✗ auto-whisper is macOS-only" >&2
    exit 1
fi
echo "  ✓ macOS $(sw_vers -productVersion)"

# ─── 2. Python 3.11+ ─────────────────────────────────────────────────
PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true)
        if [[ -n "$ver" ]]; then
            major=${ver%.*}; minor=${ver#*.}
            if (( major == 3 && minor >= 11 )); then
                PYTHON="$candidate"
                break
            fi
        fi
    fi
done
if [[ -z "$PYTHON" ]]; then
    echo "  ✗ Python 3.11+ required." >&2
    echo "    Install with:  brew install python@3.12" >&2
    exit 1
fi
echo "  ✓ Python $("$PYTHON" --version 2>&1 | awk '{print $2}')"

# ─── 3. Homebrew + ffmpeg (for local fallback) ───────────────────────
if ! command -v brew &>/dev/null; then
    echo "  ✗ Homebrew required for ffmpeg." >&2
    echo "    Install: https://brew.sh" >&2
    exit 1
fi
if ! command -v ffmpeg &>/dev/null; then
    echo "  Installing ffmpeg..."
    brew install ffmpeg >/dev/null
fi
echo "  ✓ ffmpeg"

# ─── 4. Virtualenv ───────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    echo "  Creating venv at $VENV_DIR ..."
    "$PYTHON" -m venv "$VENV_DIR"
fi
echo "  Installing Python dependencies (~30s)..."
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q \
    rumps numpy sounddevice groq edge-tts httpx \
    pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices
echo "  ✓ Dependencies installed"

# ─── 5. Directories ──────────────────────────────────────────────────
mkdir -p "$LOG_DIR"
echo "  ✓ Log directory: $LOG_DIR"

# ─── 6. Groq API key ─────────────────────────────────────────────────
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

if ! grep -q "^GROQ_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    cat <<'EOF'

  ┌─────────────────────────────────────────────────┐
  │  Groq API key required (free tier is plenty)    │
  │                                                 │
  │   1. Open https://console.groq.com/keys         │
  │   2. Sign in (free, ~30s)                       │
  │   3. Create an API key, copy it                 │
  │   4. Paste below (input is hidden)              │
  └─────────────────────────────────────────────────┘
EOF
    echo
    read -rsp "  Groq API key: " GROQ_KEY
    echo
    if [[ -n "$GROQ_KEY" ]]; then
        echo "GROQ_API_KEY=$GROQ_KEY" >> "$ENV_FILE"
        echo "  ✓ Key saved (chmod 600)"
    else
        echo "  ⚠ No key entered — add one to $ENV_FILE later or cloud transcription will fail" >&2
    fi
fi

# ─── 7. Smoke-test Groq ──────────────────────────────────────────────
if grep -q "^GROQ_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    echo "  Testing Groq connection..."
    RESULT=$(INSTALL_DIR="$INSTALL_DIR" "$VENV_DIR/bin/python" - <<'PY' 2>/dev/null
import os
from pathlib import Path
for line in (Path(os.environ["INSTALL_DIR"]) / ".env").read_text().splitlines():
    if line.startswith("GROQ_API_KEY="):
        os.environ["GROQ_API_KEY"] = line.split("=", 1)[1].strip()
try:
    from groq import Groq
    Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
    print("OK")
except Exception as e:
    print(f"FAIL: {e}")
PY
)
    if [[ "$RESULT" == "OK" ]]; then
        echo "  ✓ Groq API reachable"
    else
        echo "  ⚠ Groq smoke test: $RESULT" >&2
    fi
fi

# ─── 8. Generate LaunchAgent plist ───────────────────────────────────
PYTHON_BIN="$VENV_DIR/bin/python"

cat > "$PLIST_SRC" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$INSTALL_DIR/auto_whisper/main.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/auto-whisper.out</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/auto-whisper.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>$INSTALL_DIR</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>LANG</key>
        <string>en_US.UTF-8</string>
        <key>AUTO_WHISPER_PREFER_BUILTIN_MIC</key>
        <string>1</string>
    </dict>
</dict>
</plist>
PLIST

if ! /usr/bin/plutil -lint "$PLIST_SRC" >/dev/null; then
    echo "  ✗ Generated plist failed validation (this is a bug)" >&2
    exit 1
fi
echo "  ✓ LaunchAgent plist generated"

# ─── 9. Install + load LaunchAgent ───────────────────────────────────
mkdir -p "$(dirname "$PLIST_DST")"
if launchctl list | grep -q "[[:space:]]$PLIST_LABEL\$"; then
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi
ln -sf "$PLIST_SRC" "$PLIST_DST"
launchctl load "$PLIST_DST"

started=false
for _ in 1 2 3 4; do
    sleep 2
    if launchctl list | grep -q "[[:space:]]$PLIST_LABEL\$"; then
        started=true
        break
    fi
done

if $started; then
    echo "  ✓ auto-whisper is running"
else
    echo "  ⚠ LaunchAgent loaded but process not visible. Check:" >&2
    echo "    tail $LOG_DIR/auto-whisper.err" >&2
fi

# ─── 10. Permissions + how-to ────────────────────────────────────────
cat <<EOF

  ┌─────────────────────────────────────────────────────────────────┐
  │  One-time macOS permissions                                     │
  │                                                                 │
  │  You'll be prompted on first use. Grant both:                   │
  │                                                                 │
  │   1. Microphone   — to record your voice                        │
  │   2. Accessibility — to paste text where your cursor is         │
  │                                                                 │
  │  If no prompt appears, open                                     │
  │   System Settings → Privacy & Security → Accessibility          │
  │  and add:                                                       │
  │   $PYTHON_BIN
  └─────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────┐
  │  How to use                                                     │
  │                                                                 │
  │  • Dictate          double-tap Right ⌘ → speak → double-tap     │
  │  • Optimize voice   Opt + double-tap Right ⌘ → speak            │
  │                     (turns rough speech into a clean prompt     │
  │                      for Claude / ChatGPT / etc.)               │
  │  • Read clipboard   double-tap Left ⌘                           │
  │  • Explain copy     Opt + double-tap Left ⌘                     │
  │                                                                 │
  │  Click ◎ in the menu bar for: optimize/organize/summarize       │
  │  the last clipboard copy, recent dictations, settings.          │
  └─────────────────────────────────────────────────────────────────┘

  Logs:       tail -f $LOG_DIR/dictation.log
  Restart:    launchctl kickstart -k gui/\$(id -u)/$PLIST_LABEL
  Uninstall:  cd $INSTALL_DIR && bash uninstall.sh
EOF
echo
