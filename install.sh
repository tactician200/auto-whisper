#!/bin/bash
# auto-whisper installer
# Usage: curl -sL <url>/install.sh | bash
# Or: bash install.sh

set -e

echo ""
echo "  auto-whisper installer"
echo "  ─────────────────────"
echo ""

INSTALL_DIR="$HOME/auto-whisper"
VENV_DIR="$INSTALL_DIR/.venv"
ENV_FILE="$INSTALL_DIR/.env"
PLIST_NAME="com.auto-whisper.plist"
PLIST_SRC="$INSTALL_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"
LOGS_DIR="$HOME/MeetingTranscripts/logs"

# --- Check macOS ---
if [[ "$(uname)" != "Darwin" ]]; then
    echo "  ✗ auto-whisper only works on macOS"
    exit 1
fi

# --- Check Python ---
PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "  ✗ Python 3.11+ not found. Install with: brew install python"
    exit 1
fi

PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  ✓ Python: $PY_VERSION ($PYTHON)"

# --- Check Homebrew + ffmpeg ---
if ! command -v brew &>/dev/null; then
    echo "  ✗ Homebrew not found. Install from https://brew.sh"
    exit 1
fi

if ! command -v ffmpeg &>/dev/null; then
    echo "  Installing ffmpeg..."
    brew install ffmpeg
fi
echo "  ✓ ffmpeg: $(which ffmpeg)"

# --- Clone/update repo ---
if [[ -d "$INSTALL_DIR" ]]; then
    echo "  ✓ Directory exists: $INSTALL_DIR"
else
    echo "  Cloning auto-whisper..."
    # For now, copy from local. Replace with git clone when hosted.
    mkdir -p "$INSTALL_DIR"
fi

# --- Setup venv ---
if [[ ! -d "$VENV_DIR" ]]; then
    echo "  Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

echo "  Installing dependencies..."
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q rumps numpy sounddevice groq pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices
echo "  ✓ Dependencies installed"

# --- Setup whisper.cpp (local fallback) ---
WHISPER_DIR="$HOME/src/whisper.cpp"
if [[ -f "$WHISPER_DIR/build/bin/whisper-cli" ]]; then
    echo "  ✓ whisper.cpp: installed"
else
    echo "  ⚠ whisper.cpp not found (local fallback unavailable)"
    echo "    To install: git clone https://github.com/ggml-org/whisper.cpp ~/src/whisper.cpp"
    echo "    cd ~/src/whisper.cpp && cmake -B build && cmake --build build -j"
fi

# --- API Key setup ---
mkdir -p "$LOGS_DIR"

if [[ -f "$ENV_FILE" ]] && grep -q "GROQ_API_KEY" "$ENV_FILE"; then
    echo "  ✓ Groq API key: configured"
else
    echo ""
    echo "  ┌─────────────────────────────────────────────┐"
    echo "  │  Groq API key required (free)               │"
    echo "  │  1. Go to https://console.groq.com/keys     │"
    echo "  │  2. Create a free account                   │"
    echo "  │  3. Generate an API key                     │"
    echo "  └─────────────────────────────────────────────┘"
    echo ""
    read -p "  Paste your Groq API key: " GROQ_KEY

    if [[ -z "$GROQ_KEY" ]]; then
        echo "  ⚠ No key provided. Cloud mode unavailable."
        echo "    Add later: echo 'GROQ_API_KEY=your_key' >> $ENV_FILE"
    else
        echo "GROQ_API_KEY=$GROQ_KEY" >> "$ENV_FILE"
        echo "  ✓ API key saved to $ENV_FILE"
    fi
fi

# --- Test API key ---
if grep -q "GROQ_API_KEY" "$ENV_FILE" 2>/dev/null; then
    echo "  Testing Groq API..."
    RESULT=$("$VENV_DIR/bin/python" -c "
import os
for line in open('$ENV_FILE'):
    if '=' in line and not line.startswith('#'):
        k,v = line.strip().split('=',1)
        os.environ[k] = v
from groq import Groq
try:
    c = Groq(api_key=os.environ['GROQ_API_KEY'])
    print('OK')
except Exception as e:
    print(f'FAIL: {e}')
" 2>&1)
    if [[ "$RESULT" == "OK" ]]; then
        echo "  ✓ Groq API: connected"
    else
        echo "  ⚠ Groq API test failed: $RESULT"
    fi
fi

# --- Create LaunchAgent ---
PYTHON_BIN="$VENV_DIR/bin/python"
DAEMON_SCRIPT="$INSTALL_DIR/dictation_daemon.py"

cat > "$PLIST_SRC" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.auto-whisper</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$DAEMON_SCRIPT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOGS_DIR/auto-whisper.out</string>
    <key>StandardErrorPath</key>
    <string>$LOGS_DIR/auto-whisper.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>$INSTALL_DIR</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>LANG</key>
        <string>en_US.UTF-8</string>
    </dict>
</dict>
</plist>
PLIST

# --- Permissions guidance ---
echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  macOS Permissions (one-time setup)          │"
echo "  │                                             │"
echo "  │  System Settings > Privacy & Security:      │"
echo "  │  1. Accessibility → add $PYTHON_BIN         │"
echo "  │  2. Microphone → allow when prompted        │"
echo "  └─────────────────────────────────────────────┘"

# --- Start ---
echo ""
read -p "  Start auto-whisper now? [Y/n] " START
START=${START:-Y}

if [[ "$START" =~ ^[Yy] ]]; then
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    ln -sf "$PLIST_SRC" "$PLIST_DST"
    launchctl load "$PLIST_DST"
    sleep 2

    if launchctl list | grep -q "com.auto-whisper"; then
        echo "  ✓ auto-whisper is running!"
        echo ""
        echo "  Usage: double-tap Right ⌘ to start/stop dictation"
        echo "  Menu:  click ◎ in menu bar for settings"
        echo "  Logs:  tail -f $LOGS_DIR/dictation.log"
    else
        echo "  ✗ Failed to start. Check: $LOGS_DIR/auto-whisper.err"
    fi
else
    ln -sf "$PLIST_SRC" "$PLIST_DST"
    echo "  LaunchAgent installed. Start with:"
    echo "    launchctl load $PLIST_DST"
fi

echo ""
echo "  Commands:"
echo "    Start:   launchctl load $PLIST_DST"
echo "    Stop:    launchctl unload $PLIST_DST"
echo "    Logs:    tail -f $LOGS_DIR/dictation.log"
echo "    Config:  $ENV_FILE"
echo ""
