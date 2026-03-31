#!/bin/bash
# ─────────────────────────────────────────
#  auto-whisper installer
#  Voice dictation + text summarization for macOS
#
#  Usage:
#    git clone https://github.com/tactician200/auto-whisper.git ~/auto-whisper
#    cd ~/auto-whisper && bash install.sh
# ─────────────────────────────────────────

set -e

echo ""
echo "  ◎ auto-whisper installer"
echo "  ─────────────────────────"
echo ""

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$INSTALL_DIR/.venv"
ENV_FILE="$INSTALL_DIR/.env"
AW_LOGS_DIR="$HOME/Library/Logs/auto-whisper"
MI_LOGS_DIR="$HOME/MeetingTranscripts/logs"
PLIST_LABEL="com.auto-whisper"
PLIST_SRC="$INSTALL_DIR/$PLIST_LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
MEETING_PLIST_LABEL="com.meetings-intel"
MEETING_PLIST_SRC="$INSTALL_DIR/$MEETING_PLIST_LABEL.plist"
MEETING_PLIST_DST="$HOME/Library/LaunchAgents/$MEETING_PLIST_LABEL.plist"

# ─── Check macOS ───
if [[ "$(uname)" != "Darwin" ]]; then
    echo "  ✗ auto-whisper only works on macOS"
    exit 1
fi
echo "  ✓ macOS $(sw_vers -productVersion)"

# ─── Check Python ───
PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
if [[ -z "$PYTHON" ]]; then
    echo "  ✗ Python 3.11+ required. Install: brew install python"
    exit 1
fi
echo "  ✓ Python: $("$PYTHON" --version 2>&1)"

# ─── Check Homebrew ───
if ! command -v brew &>/dev/null; then
    echo "  ✗ Homebrew required. Install: https://brew.sh"
    exit 1
fi

# ─── Install ffmpeg if needed ───
if ! command -v ffmpeg &>/dev/null; then
    echo "  Installing ffmpeg..."
    brew install ffmpeg
fi
echo "  ✓ ffmpeg"

# ─── Optional: Install BlackHole for remote meetings ───
if brew list --cask blackhole-2ch &>/dev/null; then
    echo "  ✓ BlackHole 2ch"
else
    echo ""
    read -p "  Install BlackHole 2ch for Zoom/Meet routing? [y/N] " INSTALL_BLACKHOLE
    INSTALL_BLACKHOLE=${INSTALL_BLACKHOLE:-N}
    if [[ "$INSTALL_BLACKHOLE" =~ ^[Yy] ]]; then
        echo "  Installing BlackHole 2ch..."
        brew install --cask blackhole-2ch
        echo "  ✓ BlackHole 2ch installed"
    else
        echo "  ⚠ BlackHole skipped"
    fi
fi

# ─── Create venv ───
if [[ ! -d "$VENV_DIR" ]]; then
    echo "  Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

echo "  Installing dependencies..."
"$VENV_DIR/bin/pip" install -q --upgrade pip 2>/dev/null
"$VENV_DIR/bin/pip" install -q \
    rumps numpy sounddevice groq edge-tts \
    google-genai \
    pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices \
    2>/dev/null
echo "  ✓ Dependencies installed"

# ─── Create directories ───
mkdir -p "$AW_LOGS_DIR"
mkdir -p "$MI_LOGS_DIR"
mkdir -p "$HOME/MeetingInbox"
mkdir -p "$HOME/MeetingDone"
mkdir -p "$HOME/MeetingTranscripts/notes"
mkdir -p "$HOME/MeetingTranscripts/contexts"
echo "  ✓ Directories created"

# ─── API Key setup ───
touch "$ENV_FILE"

if ! grep -q "GROQ_API_KEY" "$ENV_FILE" 2>/dev/null; then
    echo ""
    echo "  ┌─────────────────────────────────────────────┐"
    echo "  │  Groq API key required (free)               │"
    echo "  │                                             │"
    echo "  │  1. Go to https://console.groq.com/keys     │"
    echo "  │  2. Create a free account                   │"
    echo "  │  3. Generate an API key                     │"
    echo "  │  4. Paste it below                          │"
    echo "  └─────────────────────────────────────────────┘"
    echo ""
    read -p "  Groq API key: " GROQ_KEY
    if [[ -n "$GROQ_KEY" ]]; then
        echo "GROQ_API_KEY=$GROQ_KEY" >> "$ENV_FILE"
        echo "  ✓ Groq API key saved"
    else
        echo "  ⚠ No key — cloud mode unavailable (local whisper.cpp only)"
    fi
fi

# Optional: Gemini key for meeting analysis
if ! grep -q "GEMINI_API_KEY" "$ENV_FILE" 2>/dev/null; then
    echo ""
    echo "  Optional: Gemini API key (for meeting analysis)"
    echo "  Get one at: https://aistudio.google.com/apikey"
    read -p "  Gemini API key (Enter to skip): " GEMINI_KEY
    if [[ -n "$GEMINI_KEY" ]]; then
        echo "GEMINI_API_KEY=$GEMINI_KEY" >> "$ENV_FILE"
        echo "  ✓ Gemini API key saved"
    fi
fi

# ─── Test API connection ───
if grep -q "GROQ_API_KEY" "$ENV_FILE" 2>/dev/null; then
    echo "  Testing Groq API..."
    RESULT=$("$VENV_DIR/bin/python" -c "
import sys; sys.path.insert(0, '$INSTALL_DIR')
from shared.config import GROQ_API_KEY
if not GROQ_API_KEY: print('NO_KEY'); sys.exit()
from groq import Groq
try:
    Groq(api_key=GROQ_API_KEY)
    print('OK')
except Exception as e:
    print(f'FAIL: {e}')
" 2>/dev/null)
    if [[ "$RESULT" == "OK" ]]; then
        echo "  ✓ Groq API: connected"
    else
        echo "  ⚠ Groq API: $RESULT"
    fi
fi

# ─── Generate LaunchAgent plist ───
PYTHON_BIN="$VENV_DIR/bin/python"

cat > "$PLIST_SRC" << PLIST
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
    <string>$AW_LOGS_DIR/auto-whisper.out</string>
    <key>StandardErrorPath</key>
    <string>$AW_LOGS_DIR/auto-whisper.err</string>
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

echo "  ✓ LaunchAgent configured"

# ─── Generate Meeting Transcriber LaunchAgent plist ───
cat > "$MEETING_PLIST_SRC" << MEETING_PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$MEETING_PLIST_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$INSTALL_DIR/meetings_intel/main.py</string>
    </array>
    <key>WatchPaths</key>
    <array>
        <string>$HOME/MeetingInbox</string>
    </array>
    <key>StandardOutPath</key>
    <string>$MI_LOGS_DIR/meetings-intel.out</string>
    <key>StandardErrorPath</key>
    <string>$MI_LOGS_DIR/meetings-intel.err</string>
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
MEETING_PLIST

echo "  ✓ MeetingsIntel LaunchAgent configured"

# ─── Install .app to Applications ───
APP_DIR="/Applications/auto-whisper.app"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# Info.plist
cat > "$APP_DIR/Contents/Info.plist" << 'INFOPLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>auto-whisper</string>
    <key>CFBundleDisplayName</key>
    <string>auto-whisper</string>
    <key>CFBundleIdentifier</key>
    <string>com.auto-whisper</string>
    <key>CFBundleVersion</key>
    <string>5.0.0</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>auto-whisper needs microphone access for dictation.</string>
</dict>
</plist>
INFOPLIST

# Launcher script
cat > "$APP_DIR/Contents/MacOS/launcher" << LAUNCHER
#!/bin/bash
APP_DIR="$INSTALL_DIR"
PYTHON="$PYTHON_BIN"

if [[ ! -f "\$PYTHON" ]]; then
    osascript -e 'display dialog "auto-whisper not installed correctly.\\nRun: cd ~/auto-whisper && bash install.sh" with title "auto-whisper" buttons {"OK"} with icon stop'
    exit 1
fi

if pgrep -f "auto_whisper/main.py" > /dev/null 2>&1; then
    osascript -e 'display dialog "auto-whisper is already running.\\nLook for the ◎ icon in the menu bar." with title "auto-whisper" buttons {"OK"} with icon note'
    exit 0
fi

export PYTHONPATH="\$APP_DIR"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:\$PATH"
export LANG="en_US.UTF-8"
exec "\$PYTHON" "\$APP_DIR/auto_whisper/main.py"
LAUNCHER

chmod +x "$APP_DIR/Contents/MacOS/launcher"

# Copy icon
if [[ -f "$INSTALL_DIR/auto-whisper.icns" ]]; then
    cp "$INSTALL_DIR/auto-whisper.icns" "$APP_DIR/Contents/Resources/AppIcon.icns"
fi

# Sign
codesign --force --deep --sign - "$APP_DIR" 2>/dev/null

echo "  ✓ App installed: /Applications/auto-whisper.app"

# ─── Permissions ───
echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  macOS Permissions (one-time)               │"
echo "  │                                             │"
echo "  │  When prompted, grant:                      │"
echo "  │  1. Microphone access                       │"
echo "  │  2. Accessibility (System Settings →        │"
echo "  │     Privacy & Security → Accessibility)     │"
echo "  │                                             │"
echo "  │  Add this binary to Accessibility:          │"
echo "  │  $PYTHON_BIN"
echo "  └─────────────────────────────────────────────┘"

# ─── Start ───
echo ""
read -p "  Start auto-whisper now? [Y/n] " START
START=${START:-Y}

if [[ "$START" =~ ^[Yy] ]]; then
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    ln -sf "$PLIST_SRC" "$PLIST_DST"
    launchctl load "$PLIST_DST"
    launchctl unload "$MEETING_PLIST_DST" 2>/dev/null || true
    ln -sf "$MEETING_PLIST_SRC" "$MEETING_PLIST_DST"
    launchctl load "$MEETING_PLIST_DST"
    sleep 2

    if launchctl list | grep -q "$PLIST_LABEL"; then
        echo ""
        echo "  ✓ auto-whisper is running!"
        echo ""
        echo "  ┌─────────────────────────────────────────────┐"
        echo "  │  How to use:                                │"
        echo "  │                                             │"
        echo "  │  Dictate:   double-tap Right ⌘              │"
        echo "  │  Organize:  click ◎ → Organize ideas        │"
        echo "  │  Summarize: double-tap Left ⌘               │"
        echo "  │  Stop:      double-tap Left ⌘ while speaking│"
        echo "  │  Menu:      click ◎ in menu bar             │"
        echo "  │                                             │"
        echo "  │  MeetingsIntel: drop audio in ~/MeetingInbox│"
        echo "  └─────────────────────────────────────────────┘"
    else
        echo "  ✗ Failed to start. Check: $AW_LOGS_DIR/auto-whisper.err"
    fi
else
    ln -sf "$PLIST_SRC" "$PLIST_DST"
    ln -sf "$MEETING_PLIST_SRC" "$MEETING_PLIST_DST"
    echo "  LaunchAgent installed. Start with:"
    echo "    launchctl load $PLIST_DST"
    echo "    launchctl load $MEETING_PLIST_DST"
    echo "  Or open auto-whisper from Applications/Spotlight."
fi

echo ""
echo "  Commands:"
echo "    Start:     launchctl load $PLIST_DST"
echo "    Stop:      launchctl unload $PLIST_DST"
echo "    Logs:      tail -f $AW_LOGS_DIR/dictation.log"
echo "    Meetings:  launchctl kickstart -k gui/$(id -u)/$MEETING_PLIST_LABEL"
echo "    Config:    nano $ENV_FILE"
echo "    Reinstall: cd $INSTALL_DIR && bash install.sh"
echo ""
