# auto-whisper v5 — beta test checklist

> **Status (2026-04-30):** v5.0 GA. Phase 0 (direct Groq path) is the production
> line — that's what `com.auto-whisper` LaunchAgent has been running since
> 2026-04-28 with zero crashes/errors. The FastAPI service split documented
> below ("via auto-whisper-service" banner lines, Round 1 service checks)
> is **deferred to v5.1** — `fastapi`/`uvicorn` are intentionally not
> installed in the active venv, and the `AUTO_WHISPER_USE_SERVICE_*` flags
> are not set on the active plist. Treat this file as a) historical
> validation log of the beta, and b) reference for v5.1 when we wire the
> service back in. For new installs of v5.0, follow the installer doc
> instead (Phase 3 deliverable).

You're switching your daily-use LaunchAgent from v4.2 to v5. Everything is
reversible with one command (`make revert-to-v4`). Plan: 30-60 min over a
normal workday.

## Pre-flight (5 min)

```bash
cd /Users/stj/src/auto-whisper-v5

# Sanity: tests pass.
make test

# Sanity: v5 venv works.
.venv/bin/python -c "import auto_whisper, auto_whisper_service; print('OK')"

# Confirm v4.2 is currently your active LaunchAgent (baseline).
make v4-status
# Expected: a line like "12345  -  com.auto-whisper"
```

## Round 1 — manual run, no LaunchAgent change (10 min)

Validates the v5 binary works end-to-end before committing to a launchd swap.

```bash
make unload-v4         # stop v4.2 (file preserved, reload anytime with load-v4)
make run-v5-beta       # launches menubar; daemon auto-spawns service
```

**Checks while v5 is running:**

- [ ] Banner shows all 6 lines:
  - Cloud engine: `via auto-whisper-service`
  - LLM processing: `via auto-whisper-service`
  - TTS: `via auto-whisper-service`
  - Mic input: `prefer built-in over Bluetooth`
  - Service: `reachable`
- [ ] Right-cmd-cmd → speak → text gets pasted
- [ ] Menu bar item "Recent dictations" populates after 1-2 dictates
- [ ] Menu bar action "Optimize what I say → prompt" produces a 4-section prompt
- [ ] Menu bar action "Explain selection (speak)" actually speaks (TTS)
- [ ] Stop button while speaking → audio stops immediately

**AirPods regression test (the bug we're hunting):**

- [ ] Connect AirPods.
- [ ] Right-cmd-cmd → speak normally → check transcription quality.
- [ ] Logs (Ctrl+C menubar, scroll up) show:
  `Default input is Bluetooth (AirPods Pro); overriding to built-in (MacBook Air Microphone) for transcription quality.`
- [ ] Compare quality with `AUTO_WHISPER_PREFER_BUILTIN_MIC=0 make run-v5-beta` (the bug path) — should be noticeably worse.

When done with Round 1: `Ctrl+C` the menubar.

```bash
make load-v4    # restore v4.2 as active daemon while you decide
```

## Round 2 — install as your active LaunchAgent (5 min + days of real use)

If Round 1 looked good, swap launchd over.

```bash
make install-v5
# Output ends with the rollback command — keep that terminal open or copy it.
```

**Verify launchd has v5:**

```bash
make v5-status
# Expected: "12345  0  com.auto-whisper.v5"

make v4-status
# Expected: "v4.2 not running" (file at ~/Library/LaunchAgents/com.auto-whisper.plist preserved)
```

Now use v5 for normal work for as long as you're comfortable. The active
LaunchAgent persists across logins.

## Rollback (anytime)

```bash
cd /Users/stj/src/auto-whisper-v5
make revert-to-v4
```

This unloads v5 and re-loads v4.2. Both plist files stay on disk so you can
flip back to v5 with `make install-v5` later.

## Logs

Tailing helps diagnose anything weird:

```bash
tail -f ~/Library/Logs/auto-whisper-v5/auto-whisper-v5.err     # menubar + service stderr
ls ~/Library/Logs/auto-whisper-v5/                              # both .out and .err
```

## v5.0 GA scope (and what's deferred to v5.1)

**Shipped in v5.0:**
- Direct Groq path with AirPods built-in-mic override.
- `optimize_prompt()` 4-section restructure, max_tokens=1500.
- Existing modes: dictate, organize, optimize, summarize, explain, read.

**Deferred to v5.1:**
- FastAPI service split (the "via auto-whisper-service" path documented in
  this file). Code is in tree and tested in isolation, but never run in
  production. Watchdog with auto-respawn on dead/unhealthy subprocess is
  pre-built in `auto_whisper/service_lifecycle.py` for when this turns on.
- Privacy Mode end-to-end (today only TTS forces offline; transcription/LLM
  gating lands when service split ships).
- Onboarding wizard — TTS voice/rate hardcoded.
- DMG / signed installer. Phase 3 ships a shell installer for tech-friendly
  demo users; signed bundle is a v5.2+ concern once the prompt-converter
  hypothesis is validated.

## What to report back

If something breaks, capture:
1. The banner output from a fresh launch (`make run-v5-beta` foreground).
2. The last ~50 lines of `~/Library/Logs/auto-whisper-v5/auto-whisper-v5.err`.
3. What you did right before the failure.
