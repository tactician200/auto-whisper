# auto-whisper

macOS menu bar dictation app. Speak, and text appears at your cursor.

- **Cloud mode** (default): Groq whisper-large-v3 API — <1s latency
- **Local mode**: whisper.cpp — works offline, ~6s latency
- **Auto mode**: Cloud when online, local fallback

## Features

- Double-tap Right ⌘ to start/stop recording
- Audio feedback (beep on start/stop)
- Multi-language: Spanish, English, Auto-detect
- Usage tracker for Groq free tier (8hrs/day)
- Paste-last: re-paste previous transcription
- Focus restore: text always pastes in the correct window
- Meeting transcriber: drop audio files in `~/MeetingInbox/` for automatic transcription + AI analysis

## Install

```bash
bash install.sh
```

The installer will:
1. Set up Python virtual environment
2. Install dependencies
3. Ask for your [Groq API key](https://console.groq.com/keys) (free)
4. Configure LaunchAgent (auto-start at login)
5. Guide you through macOS permissions

## Requirements

- macOS 13+
- Python 3.11+
- [Homebrew](https://brew.sh)
- [Groq API key](https://console.groq.com/keys) (free tier: 8 hours/day)
- Optional: [whisper.cpp](https://github.com/ggml-org/whisper.cpp) for local fallback

## Permissions

Only 2 permissions needed:
- **Accessibility** — for pasting text via Cmd+V
- **Microphone** — for recording audio

## Menu bar

```
◎ (idle) → ◠ (starting) → ◉ (recording) → ⟳ (processing)

◎ click:
├── Toggle (⌘⌘)
├── Paste last
├── Engine ▸ Cloud (Groq) / Local / Auto
├── Language ▸ Español / English / Auto-detect
├── Usage: ░░░░░░░░░░ 0/480min (0%)
├── Status: idle
└── Quit
```

## Commands

```bash
# Start/stop
launchctl load ~/Library/LaunchAgents/com.auto-whisper.plist
launchctl unload ~/Library/LaunchAgents/com.auto-whisper.plist

# Logs
tail -f ~/MeetingTranscripts/logs/dictation.log

# Config
nano ~/auto-whisper/.env
```

## Meeting Transcriber

Separate feature: drop audio files into `~/MeetingInbox/` and get structured meeting notes with AI analysis (summaries, action items, decisions) powered by Gemini.

```bash
cp recording.wav ~/MeetingInbox/
# → Note appears in ~/MeetingTranscripts/notes/
```

## License

MIT
