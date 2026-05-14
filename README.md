# auto-whisper

macOS menu-bar dictation. Double-tap Right ⌘ → speak → text appears at your cursor.

- **Cloud mode** (default): Groq `whisper-large-v3` — sub-1s latency on most clips.
- **Local fallback**: whisper.cpp — works offline, ~6s latency.
- **Auto mode**: Cloud when online, local when not.

## Install

```bash
git clone https://github.com/tactician200/auto-whisper.git ~/auto-whisper
cd ~/auto-whisper
bash install.sh
```

The installer will:
1. Verify macOS + Python 3.11+ + Homebrew.
2. Install `ffmpeg`.
3. Create a Python venv and install dependencies.
4. Ask you for a free [Groq API key](https://console.groq.com/keys).
5. Generate and load a LaunchAgent so the app auto-starts at login.
6. Walk you through the one-time macOS permissions.

Uninstall any time:

```bash
cd ~/auto-whisper && bash uninstall.sh
```

## Requirements

- macOS 13+
- Python 3.11+
- [Homebrew](https://brew.sh)
- [Groq API key](https://console.groq.com/keys) (free tier covers ~8h of dictation/day)

## Permissions

Only two are required, both granted on first use:
- **Microphone** — to record your voice.
- **Accessibility** — to paste text where your cursor is.

If you don't see the prompt automatically, open **System Settings → Privacy & Security → Accessibility** and add the venv's Python binary (`~/auto-whisper/.venv/bin/python`).

## Hotkeys

| Action | Shortcut |
|---|---|
| Dictate | double-tap Right ⌘ → speak → double-tap |
| Optimize what I say → prompt | Opt + double-tap Right ⌘ |
| Read clipboard aloud | double-tap Left ⌘ |
| Explain clipboard | Opt + double-tap Left ⌘ |

The Optimize hotkey turns rough speech into a structured prompt for Claude / ChatGPT — useful for non-native-English speakers drafting LLM queries.

## Menu (◎ icon in menu bar)

```
Cloud · ES                           ← status
▓░░░░ 1% Groq                        ← usage tracker
─────
Hold ⌘⌘ to dictate                   ← hint
─────
Optimize last copy → prompt
Organize last copy
Summarize last copy → speak
Explain last copy → speak
Read last copy aloud
─────
Recent dictations ▶
Paste last again
─────
Settings ▶
  ├─ Voice modes ▶ (Dictate / Optimize / Organize)
  ├─ Engine ▶ (Cloud / Local / Auto)
  ├─ Language ▶ (ES / EN / Auto)
  ├─ Input ▶ (audio devices)
  ├─ Output: Speak / Paste
  ├─ Vocabulary ▶ (Project / Add term)
  └─ Stop speaking
```

The five primary items operate on your last clipboard copy. The hotkey is the fast path for dictation; the menu is for clipboard transformations.

## Commands

```bash
# Restart the daemon
launchctl kickstart -k gui/$(id -u)/com.auto-whisper

# Tail runtime log
tail -f ~/Library/Logs/auto-whisper/dictation.log

# Edit API key / config
nano ~/auto-whisper/.env
```

## License

MIT
