# auto-whisper

macOS menu bar dictation app. Speak, and text appears at your cursor.

- **Cloud mode** (default): Groq whisper-large-v3 API — <1s latency
- **Local mode**: whisper.cpp — works offline, ~6s latency
- **Auto mode**: Cloud when online, local fallback

## Features

- Double-tap Right ⌘ to start/stop dictation
- Organize ideas: record via menu, AI cleans and structures before pasting
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
├── Dictate (⌘⌘)
├── Organize ideas
├── Paste Last
├── Summarize (⌘⌘←)
├── Read clipboard
├── Explain clipboard
├── Engine ▸ Cloud (Groq) / Local / Auto
├── Language ▸ Español / English / Auto-detect
├── Input ▸ System Default / micrófono específico
├── ▓░░░░░░░░░ 5/480min (1%)
├── Cloud · ES
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

## MeetingsIntel

Separate feature: drop audio files into `~/MeetingInbox/` and get structured meeting notes with AI analysis (summaries, action items, decisions) powered by Gemini.

```bash
cp recording.wav ~/MeetingInbox/
# → Note appears in ~/MeetingTranscripts/notes/
```

### Remote Meetings: Zoom / Google Meet

For remote meetings, the most practical universal setup is:

1. Route meeting audio with `BlackHole 2ch`
2. Record it in your preferred app
3. Send the resulting file to `~/MeetingInbox`

This repo includes a MeetingsIntel importer helper:

```bash
bash ~/src/meeting-transcriber/send_to_meetings_intel.sh "/path/to/recording.m4a"
```

That copies the file into `~/MeetingInbox` and nudges the watcher so transcription starts right away.

#### Send to MeetingsIntel

`AutoWhisper` stays focused on dictation/reading. `MeetingsIntel` handles imported meeting recordings.

The repo now includes two simple entry surfaces for imports:

1. [`Send to MeetingsIntel.app`](/Users/mantra/Applications/Send%20to%20MeetingsIntel.app)
   Drag one or more meeting recordings onto the app.
2. Finder Quick Action
   Use `Send to MeetingsIntel` from Finder on selected files.

```bash
bash ~/src/meeting-transcriber/send_to_meetings_intel.sh "$@"
```

That gives you near one-click import into the transcription + meeting-intelligence pipeline without building a native Share Extension.

## License

MIT
