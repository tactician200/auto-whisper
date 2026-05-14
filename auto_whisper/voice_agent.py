#!/usr/bin/env python3
"""
Voice output module — multi-backend TTS.

Backends (fallback chain: google → edge → macos):
  - google: Google Cloud WaveNet (best quality, 1M chars/mo free)
  - edge: Edge TTS / Microsoft Neural (free unlimited, no API key)
  - macos: macOS `say` command (offline, basic quality)

Usage:
    from voice_agent import speak, speak_async
    speak("Hola, este es un resumen")
    speak("Hello world", backend="edge")
    speak_async("Background speech")
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

BACKENDS = ("google", "edge", "macos")
DEFAULT_BACKEND = "edge"

# Global playback process — allows stopping from outside
_current_playback = None
_playback_lock = threading.Lock()


# --- Google Cloud TTS (WaveNet) ---

def _speak_google(text: str, output: Path, voice: str = "es-ES-Wavenet-C"):
    from google.cloud import texttospeech
    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice_params = texttospeech.VoiceSelectionParams(
        language_code=voice[:5], name=voice,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=1.0,
    )
    response = client.synthesize_speech(
        input=synthesis_input, voice=voice_params, audio_config=audio_config,
    )
    output.write_bytes(response.audio_content)


# --- Edge TTS (Microsoft Neural, free) ---

def _speak_edge(text: str, output: Path, voice: str = "es-ES-AlvaroNeural"):
    import edge_tts
    async def _gen():
        comm = edge_tts.Communicate(text, voice, rate="+25%")
        await comm.save(str(output))
    asyncio.run(_gen())


# --- macOS native ---

def _speak_macos(text: str, output: Path, voice: str = "Mónica"):
    # say outputs AIFF by default
    aiff_path = output.with_suffix(".aiff")
    try:
        result = subprocess.run(
            ["say", "-v", voice, "-o", str(aiff_path), text],
            timeout=120, capture_output=True,
        )
        if result.returncode != 0:
            logger.error(f"say failed: {result.stderr}")
            return
        # Rename to expected path if different
        if aiff_path != output:
            aiff_path.rename(output)
    except Exception:
        # Clean up orphaned aiff file on any failure
        if aiff_path.exists():
            aiff_path.unlink(missing_ok=True)
        raise


# --- Unified interface ---

def _clean_for_speech(text: str) -> str:
    """Remove markdown, symbols, and artifacts that TTS reads literally."""
    import re
    # Headers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Bold/italic (***text***, **text**, *text*)
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    # Underscores for italic/bold
    text = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', text)
    # Strikethrough
    text = re.sub(r'~~([^~]+)~~', r'\1', text)
    # Bullet markers
    text = re.sub(r'^[\s]*[-*•]\s+', '', text, flags=re.MULTILINE)
    # Numbered lists (1. 2. etc) — keep the text
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # Checkboxes
    text = re.sub(r'\[[ x]\]\s*', '', text)
    # Inline code backticks
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)
    # HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Markdown links [text](url) — keep text (before URL removal)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # URLs — replace with "enlace"
    text = re.sub(r'https?://\S+', 'enlace', text)
    # Emojis and special symbols
    text = re.sub(r'[✅❓⚠️📌🔴◎◉⟳◠◈🔊→←↓↑▸►●○■□]', '', text)
    # Horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Pipe tables
    text = re.sub(r'^\|.*\|$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[\s|:-]+$', '', text, flags=re.MULTILINE)
    # Multiple spaces/newlines
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Stray asterisks or underscores
    text = re.sub(r'(?<!\w)[*_]+|[*_]+(?!\w)', '', text)
    return text.strip()



def synthesize(
    text: str,
    backend: str = DEFAULT_BACKEND,
    voice: str | None = None,
) -> tuple[bytes, str] | None:
    """Generate audio for text. Returns (audio_bytes, ext) or None on total failure.

    `ext` is "mp3" for google/edge, "aiff" for macos — caller writes bytes to a
    file with that extension before playing (afplay infers format from extension).

    Falls back through google → edge → macos. Returns None only if every backend
    in the fallback chain fails.

    Pure function: no playback, no global playback state. The HTTP service path
    needs this shape (returns bytes over the wire); local `speak()` wraps it with
    afplay.
    """
    if not text or not text.strip():
        return None

    text = _clean_for_speech(text.strip())
    suffix = ".aiff" if backend == "macos" else ".mp3"
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    tmp = Path(tmp_name)

    try:
        try:
            if backend == "google":
                _speak_google(text, tmp, voice or "es-ES-Wavenet-C")
            elif backend == "edge":
                _speak_edge(text, tmp, voice or "es-ES-AlvaroNeural")
            elif backend == "macos":
                _speak_macos(text, tmp, voice or "Mónica")
            else:
                raise ValueError(f"Unknown backend: {backend}")
        except Exception as e:
            logger.warning(f"TTS backend '{backend}' failed: {e}")
            fallbacks = {"google": "edge", "edge": "macos"}
            next_backend = fallbacks.get(backend)
            if next_backend:
                logger.info(f"Falling back to '{next_backend}'")
                return synthesize(text, backend=next_backend, voice=None)
            logger.error("All TTS backends failed")
            return None

        if not tmp.exists() or tmp.stat().st_size == 0:
            logger.warning("TTS produced empty audio")
            return None

        return tmp.read_bytes(), suffix.lstrip(".")
    finally:
        tmp.unlink(missing_ok=True)


def _play_bytes(audio_bytes: bytes, ext: str, block: bool = True) -> None:
    """Write audio bytes to a temp file and play via afplay.

    Shared by the local path (synth + play) and the service path (HTTP-fetched
    bytes + play). Playback state lives here so stop_speaking()/is_speaking()
    work identically regardless of where the audio came from.
    """
    fd, tmp_name = tempfile.mkstemp(suffix=f".{ext}")
    os.close(fd)
    tmp = Path(tmp_name)
    tmp.write_bytes(audio_bytes)

    global _current_playback
    cmd = ["afplay", str(tmp)]

    with _playback_lock:
        # Kill any previous playback (inline to avoid deadlock)
        if _current_playback and _current_playback.poll() is None:
            _current_playback.terminate()
        proc = subprocess.Popen(cmd)
        _current_playback = proc

    if block:
        proc.wait()
        with _playback_lock:
            _current_playback = None
        tmp.unlink(missing_ok=True)
    else:
        def _wait_and_clean():
            proc.wait()
            with _playback_lock:
                global _current_playback
                if _current_playback == proc:
                    _current_playback = None
            tmp.unlink(missing_ok=True)
        threading.Thread(target=_wait_and_clean, daemon=True).start()


def _speak_local(
    text: str,
    backend: str = DEFAULT_BACKEND,
    voice: str | None = None,
    block: bool = True,
) -> None:
    """Local TTS path: synthesize in-process, then play.

    The flag-aware public dispatcher lives in `tts_routing.speak`. This
    function is the direct branch — kept here (instead of in tts_routing)
    so synth + playback stay in one module and share `_play_bytes` /
    `_current_playback`.
    """
    result = synthesize(text, backend=backend, voice=voice)
    if result is None:
        return
    _play_bytes(*result, block=block)


def speak(text: str, backend: str = DEFAULT_BACKEND, voice: str | None = None,
          block: bool = True) -> None:
    """Public speak — flag-aware (service vs local) via tts_routing dispatcher.

    Lazy-imports tts_routing to avoid a circular import at module load time
    (tts_routing imports voice_agent for `_speak_local` / `_play_bytes`).
    """
    from auto_whisper.tts_routing import speak as _route
    _route(text, backend=backend, voice=voice, block=block)


def stop_speaking():
    """Stop any current playback immediately."""
    global _current_playback
    with _playback_lock:
        if _current_playback and _current_playback.poll() is None:
            _current_playback.terminate()
            _current_playback = None
            logger.info("Playback stopped")


def is_speaking() -> bool:
    """Check if TTS is currently playing."""
    with _playback_lock:
        return _current_playback is not None and _current_playback.poll() is None


def speak_async(text: str, backend: str = DEFAULT_BACKEND, voice: str | None = None):
    """Speak in background thread."""
    threading.Thread(
        target=speak, args=(text, backend, voice, True), daemon=True,
    ).start()
