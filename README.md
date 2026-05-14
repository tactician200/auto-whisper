# auto-whisper

macOS menu-bar dictation. **Double-tap Right ⌘ → speak → text at cursor.** Sub-1s latency on cloud, offline fallback, and a smart classifier that turns rough speech into structured prompts for Claude / ChatGPT.

> Built for non-native-English engineers and operator-builders who want raw voice → usable artifact (email, prompt, research brief, decision brief) without leaving the keyboard.

<!-- TODO: drop a screenshot of the waveform HUD here -->
<!-- ![HUD](docs/img/hud.png) -->

## Features

- **Cloud-first**, `whisper-large-v3` via Groq — typically <1s end-to-end.
- **Local fallback** via [whisper.cpp](https://github.com/ggerganov/whisper.cpp) — runs offline, ~6s latency.
- **5 output modes** the LLM rewrites your speech into:
  - `dictate` — raw transcription, paste verbatim
  - `optimize → prompt (coding)` — structured Claude Code-style prompt with `## Context / Task / Details / Constraints`
  - `optimize → writing` — drafts the actual email / DM / post, not a brief
  - `optimize → research brief` — `## Question / Context / Scope / Sources / Expected output`
  - `optimize → decision brief` — `## Decision / Options / Criteria / Risks / Open questions`
- **Smart auto-route**: Opt + double-tap classifies the dictation and picks the right mode (heuristics-first, LLM fallback for the ambiguous middle).
- **Reformat last**: if the classifier picked the wrong path, retry the same raw transcript under another mode without dictating again.
- **Privacy mode** (`AUTO_WHISPER_PRIVACY_MODE=1`): forces TTS offline (`say`), reserves LLM gating for v5.1+.
- **Audio routing**: Bluetooth devices default to built-in mic for accuracy (AirPods → MacBook mic override).
- **Vocabulary corrections** per project for proper nouns and acronyms.

## Quickstart

```bash
git clone https://github.com/tactician200/auto-whisper.git ~/auto-whisper
cd ~/auto-whisper
bash install.sh
```

The installer will:
1. Verify macOS 13+, Python 3.11+, Homebrew.
2. Install `ffmpeg`.
3. Create a Python venv and install dependencies.
4. Ask you for a free [Groq API key](https://console.groq.com/keys) (free tier covers ~8h/day).
5. Install and load a LaunchAgent so the app starts at login.
6. Walk through the one-time macOS Microphone + Accessibility prompts.

Uninstall: `cd ~/auto-whisper && bash uninstall.sh`.

## Hotkeys

| Action | Shortcut |
|---|---|
| Dictate (raw) | double-tap Right ⌘ → speak → double-tap |
| Smart route (classifier picks mode) | Opt + double-tap Right ⌘ |
| Read clipboard aloud | double-tap Left ⌘ |
| Explain clipboard | Opt + double-tap Left ⌘ |

The smart-route hotkey is the daily driver: speak naturally, the classifier figures out whether you wanted a draft email, a coding prompt, or a research/decision brief.

## Menu (◎ icon in menu bar)

```
Cloud · ES                           ← engine + language
▓░░░░ 1% Groq                        ← daily usage
─────
Hold ⌘⌘ to dictate
─────
Optimize last copy → prompt
Organize last copy
Summarize last copy → speak
Explain last copy → speak
Read last copy aloud
─────
Recent dictations ▶
Reformat last…              ▶ As coding prompt
                              As writing
                              As research brief
                              As decision brief
                              As organize
                              Raw (re-paste original)
Paste last again
─────
Settings ▶
  ├─ Voice modes ▶
  ├─ Engine ▶ (Cloud / Local / Auto)
  ├─ Language ▶ (ES / EN / Auto)
  ├─ Input ▶ (audio devices)
  ├─ Output: Speak / Paste
  ├─ Vocabulary ▶ (Project / Add term)
  └─ Stop speaking
```

## How it works (v5 architecture)

v5 splits into two processes via a strangler-fig migration off the v4.2 monolith:

```
┌─ menubar daemon (auto_whisper) ──────────────────────────┐
│  · global hotkey, recording, paste injection, HUD, menu  │
│  · routes LLM calls through dispatchers                  │
└────────────────────────────┬─────────────────────────────┘
                             │ HTTP (localhost only)
                             ▼
┌─ local service (auto_whisper_service) ───────────────────┐
│  · FastAPI on 127.0.0.1                                  │
│  · /transcribe (audio → text)                            │
│  · /process    (text → mode-specific LLM output)         │
│  · /tts        (text → audio)                            │
│  · /health · /version                                    │
└──────────────────────────────────────────────────────────┘
```

Flags (env vars) gate each route:
- `AUTO_WHISPER_USE_SERVICE=1` — transcription via local service
- `AUTO_WHISPER_USE_SERVICE_PROCESSING=1` — LLM calls via service
- `AUTO_WHISPER_USE_SERVICE_TTS=1` — TTS via service
- `AUTO_WHISPER_AUTOSTART_SERVICE=1` — daemon spawns the service subprocess

All four are pre-set in `com.auto-whisper.v5.plist` for production.

## Compared to other macOS dictation tools

| Feature | auto-whisper | Superwhisper | Wispr Flow | MacWhisper |
|---|---|---|---|---|
| Open source | ✅ MIT | ❌ Proprietary | ❌ Proprietary | ❌ Proprietary |
| Price | Free (bring-your-own Groq key, free tier 8h/day) | Paid (one-time) | Paid (subscription) [verificar] | Free + paid pro |
| Cloud + local fallback | ✅ Groq + whisper.cpp | ✅ [verificar] | Cloud-only [verificar] | ✅ |
| Smart classifier → mode auto-route | ✅ heuristics + LLM | ❌ manual mode pick | Partial [verificar] | ❌ |
| Built-in prompt-for-AI mode | ✅ coding / writing / research / decision | ❌ generic "AI mode" [verificar] | ❌ | ❌ |
| Reformat last under another mode | ✅ | ❌ | ❌ | ❌ |
| Custom vocabulary | ✅ per-project | ✅ | ✅ | ✅ |
| Privacy mode (no cloud LLM) | ✅ env flag | Partial [verificar] | ❌ | ✅ local-only |
| Hackable prompt templates | ✅ edit `shared/prompts.py` | ❌ | ❌ | Partial |
| Customizable hotkeys | Partial (code-level for now) | ✅ | ✅ | ✅ |
| Distribution | Source + ad-hoc .app | Signed + notarized | Signed + notarized | Signed + notarized |

> Items marked `[verificar]` are competitor claims I haven't personally validated — pull requests welcome.

**Why pick auto-whisper over a polished paid app:** if you frequently dictate AI prompts, write client emails in two languages, or want the prompt templates to match *your* workflow (not a vendor's), the hackability and the mode-aware classifier are the gap. If you just want one-click dictation with no editing, a paid app is faster to set up.

## Packaging the .app

```bash
.venv/bin/pip install py2app
.venv/bin/python setup_app.py py2app
codesign --force --deep --sign - dist/AutoWhisper.app
```

Result: `dist/AutoWhisper.app` (~105 MB, LSUIElement menubar app).

Ad-hoc signing works on the build machine. For distribution to other users, replace `--sign -` with your Developer ID and notarize via `xcrun notarytool`.

## Commands

```bash
# Restart the daemon (note: may leave service hijo with stale code —
# verify with `ps -ef | grep auto_whisper`)
launchctl kickstart -k gui/$(id -u)/com.auto-whisper.v5

# Tail runtime log
tail -f ~/Library/Logs/auto-whisper/dictation.log

# Edit API key / config
nano ~/auto-whisper/.env

# Run tests
make test
```

## Troubleshooting

- **No dictation happens on hotkey**: re-grant Accessibility permission for the venv's Python binary in System Settings → Privacy & Security → Accessibility.
- **Mic captures nothing with AirPods connected**: this is intentional — AirPods accuracy is poor for dictation, so we override to built-in mic.
- **Prompt edits don't take effect after restart**: kill the service process (`pkill -f auto_whisper_service`) and `launchctl kickstart -k` the daemon — the service can outlive a daemon restart and keep stale prompts in memory.
- **Groq quota hit**: switch to Local engine in the Settings menu, or wait for daily reset.

## License

MIT
