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
import subprocess
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

BACKENDS = ("google", "edge", "macos")
DEFAULT_BACKEND = "edge"  # start with edge until Google Cloud is configured


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

def _speak_edge(text: str, output: Path, voice: str = "es-ES-ElviraNeural"):
    import edge_tts
    async def _gen():
        comm = edge_tts.Communicate(text, voice)
        await comm.save(str(output))
    asyncio.run(_gen())


# --- macOS native ---

def _speak_macos(text: str, output: Path, voice: str = "Mónica"):
    # say outputs AIFF by default
    aiff_path = output.with_suffix(".aiff")
    subprocess.run(
        ["say", "-v", voice, "-o", str(aiff_path), text],
        timeout=120, capture_output=True,
    )
    # Rename to expected path if different
    if aiff_path != output:
        aiff_path.rename(output)


# --- Unified interface ---

def speak(text: str, backend: str = DEFAULT_BACKEND, voice: str | None = None,
          block: bool = True):
    """
    Generate speech and play it.
    Falls back through the chain: google → edge → macos.
    """
    if not text or not text.strip():
        return

    text = text.strip()

    suffix = ".aiff" if backend == "macos" else ".mp3"
    tmp = Path(tempfile.mktemp(suffix=suffix))

    try:
        if backend == "google":
            _speak_google(text, tmp, voice or "es-ES-Wavenet-C")
        elif backend == "edge":
            _speak_edge(text, tmp, voice or "es-ES-ElviraNeural")
        elif backend == "macos":
            _speak_macos(text, tmp, voice or "Mónica")
        else:
            raise ValueError(f"Unknown backend: {backend}")
    except Exception as e:
        logger.warning(f"TTS backend '{backend}' failed: {e}")
        # Fallback chain
        fallbacks = {"google": "edge", "edge": "macos"}
        next_backend = fallbacks.get(backend)
        if next_backend:
            logger.info(f"Falling back to '{next_backend}'")
            return speak(text, backend=next_backend, block=block)
        logger.error("All TTS backends failed")
        return

    # Play audio
    if not tmp.exists() or tmp.stat().st_size == 0:
        logger.warning("TTS produced empty audio")
        return

    cmd = ["afplay", str(tmp)]
    if block:
        subprocess.run(cmd, timeout=300)
        tmp.unlink(missing_ok=True)
    else:
        # Clean up after playback finishes
        def _play_and_clean():
            subprocess.run(cmd, timeout=300)
            tmp.unlink(missing_ok=True)
        threading.Thread(target=_play_and_clean, daemon=True).start()


def speak_async(text: str, backend: str = DEFAULT_BACKEND, voice: str | None = None):
    """Speak in background thread."""
    threading.Thread(
        target=speak, args=(text, backend, voice, True), daemon=True,
    ).start()
