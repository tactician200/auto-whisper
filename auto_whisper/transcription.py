"""Cloud transcription routing — direct Groq vs via service.

Extracted from dictation_daemon.py in Slice 2.3. Lives in its own module
so unit tests can import it without dragging in rumps / AppKit / Quartz
(macOS-only dependencies in the daemon).

Public API:
    transcribe_cloud(audio_data, sample_rate, language, whisper_prompt)
        Routes to service or direct Groq based on USE_SERVICE_TRANSCRIPTION
        flag (set at module load from AUTO_WHISPER_USE_SERVICE env var).

    USE_SERVICE_TRANSCRIPTION: bool
        Read once at import time. Toggling at runtime is intentionally
        not supported — restart the daemon to switch paths.

The local whisper.cpp path stays in dictation_daemon.py for now (it has
tighter coupling to filesystem temp dir handling); migration is Phase 2.x.
"""

import io
import logging
import os
import time
import wave

import numpy as np

from shared.config import GROQ_API_KEY_DICTATION
from shared.groq_client import get_groq_client
from shared.transcription_cleanup import clean_transcription
from shared.vocab import VocabManager, get_default_db_path

logger = logging.getLogger(__name__)


# Read once at import. Toggle requires daemon restart — keeps semantics
# explicit and avoids inconsistencies if the flag is read mid-call.
USE_SERVICE_TRANSCRIPTION: bool = os.environ.get("AUTO_WHISPER_USE_SERVICE", "0") == "1"

# Active project for vocabulary scoping. None = only global vocab applies.
# Initial value comes from env var; can be mutated at runtime via
# set_active_project(). Daemon menu UI uses the setter to switch projects
# without restart. Tests should prefer monkeypatching the attribute directly.
ACTIVE_PROJECT: str | None = os.environ.get("AUTO_WHISPER_PROJECT") or None


def set_active_project(name: str | None) -> None:
    """Update ACTIVE_PROJECT at runtime. Effective immediately for subsequent
    transcriptions in this process. Does NOT persist across daemon restarts —
    set AUTO_WHISPER_PROJECT env var (or modify launchctl plist) for that.

    Pass None or an empty string to clear (only global vocab will apply).
    """
    global ACTIVE_PROJECT
    if name is not None and not name.strip():
        name = None
    ACTIVE_PROJECT = name

GROQ_TRANSCRIPTION_MODEL = "whisper-large-v3"


def cloud_timeout_for(duration_s: float) -> float:
    """Per-request cloud timeout, scaled to audio length.

    Groq Whisper normally returns in ~1s regardless of clip length (it's
    upload-bound, not compute-bound). A flat 30s ceiling meant that when the
    API degrades/hangs the user stares at a frozen HUD for 30s before the
    local fallback even starts. We scale instead: short clips (the common
    dictation case) fail fast and fall back to local quickly; long uploads
    still get headroom. Floor 10s protects legitimately-slow-but-working
    calls (observed up to ~9s on degraded days); cap 30s preserves the old
    ceiling for multi-minute audio.
    """
    return min(30.0, max(10.0, duration_s * 0.5))


# --- VocabManager lazy singleton ---

_vocab_manager: VocabManager | None = None


def get_vocab_manager() -> VocabManager:
    """Module-level VocabManager pointing at the default DB path."""
    global _vocab_manager
    if _vocab_manager is None:
        _vocab_manager = VocabManager(get_default_db_path())
    return _vocab_manager


# --- WAV encoding helper ---

def encode_wav(audio_data: np.ndarray, sample_rate: int) -> bytes:
    """Encode mono float32 audio (range -1.0 to 1.0) to 16-bit PCM WAV bytes."""
    pcm = (audio_data * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


# --- Service client lazy singleton ---

_service_client = None


def get_service_client():
    """Return the module-level ServiceClient, lazy-initialized."""
    global _service_client
    if _service_client is None:
        # Import locally to avoid a hard dependency at module load —
        # the service client itself imports auto_whisper_service.auth
        # which touches filesystem on first use.
        from auto_whisper.service_client import ServiceClient
        _service_client = ServiceClient()
    return _service_client


# --- Routing implementations ---

def transcribe_via_service(audio_data: np.ndarray, sample_rate: int, language: str | None) -> str | None:
    """Route audio through auto-whisper-service.

    The service is responsible for vocabulary hint + correction on its side.
    The active project is forwarded so service applies the right scope.
    """
    try:
        wav_bytes = encode_wav(audio_data, sample_rate)
        timeout = cloud_timeout_for(len(audio_data) / sample_rate)
        t0 = time.time()
        result = get_service_client().transcribe(
            wav_bytes, language=language, project=ACTIVE_PROJECT, timeout=timeout
        )
        elapsed = time.time() - t0
    except Exception as e:
        logger.error(f"Service transcription error: {e}")
        return None

    if result is None:
        logger.error("Service transcription failed (returned None)")
        return None

    text = result.get("text")
    logger.info(
        f"Service transcription: {elapsed:.1f}s "
        f"({len(text) if text else 0} chars{', cleaned' if result.get('cleaned') else ''})"
    )
    return text


def transcribe_via_groq_direct(
    audio_data: np.ndarray,
    sample_rate: int,
    language: str | None,
    whisper_prompt: str | None = None,
) -> str | None:
    """Route audio directly to Groq (preserves v4.2 cloud path exactly).

    Vocabulary integration:
    - When `whisper_prompt` is None, build a hint from VocabManager
      (active project + language). Empty hint → no prompt sent to Groq.
    - When `whisper_prompt` is a string, use it verbatim (test/manual override).
    - Apply VocabManager.apply_corrections AFTER cleanup, before returning.
    """
    if not GROQ_API_KEY_DICTATION:
        logger.error("No Groq API key configured")
        return None
    try:
        # Build hint from vocab if no override.
        if whisper_prompt is None:
            hint = get_vocab_manager().get_hint(project=ACTIVE_PROJECT, language=language)
            whisper_prompt = hint or None

        wav_bytes = encode_wav(audio_data, sample_rate)
        # Per-request timeout scaled to audio length — see cloud_timeout_for.
        # with_options returns a shallow copy; the singleton's 30s default
        # (used by the LLM processing path) is left untouched.
        client = get_groq_client().with_options(timeout=cloud_timeout_for(len(audio_data) / sample_rate))
        t0 = time.time()
        params: dict = {
            "model": GROQ_TRANSCRIPTION_MODEL,
            "file": ("audio.wav", io.BytesIO(wav_bytes)),
            "response_format": "text",
        }
        if whisper_prompt:
            params["prompt"] = whisper_prompt
        if language:
            params["language"] = language
        result = client.audio.transcriptions.create(**params)
        elapsed = time.time() - t0
        logger.info(f"Groq transcription: {elapsed:.1f}s")

        if not isinstance(result, str):
            logger.error(f"Unexpected Groq response type: {type(result)}")
            return None
        text = result.strip()
        if not text:
            return None
        cleaned = clean_transcription(text) or None
        if cleaned is None:
            return None
        # Post-transcription vocab corrections (variant → canonical).
        return get_vocab_manager().apply_corrections(
            cleaned, project=ACTIVE_PROJECT, language=language
        )
    except Exception as e:
        logger.error(f"Groq API failed: {e}")
        return None


# --- Public dispatcher ---

def transcribe_cloud(
    audio_data: np.ndarray,
    sample_rate: int,
    language: str | None = "es",
    whisper_prompt: str | None = None,
) -> str | None:
    """Cloud transcription — picks service or direct based on USE_SERVICE_TRANSCRIPTION.

    Flag read once at module import; restart the daemon to switch paths.
    """
    if USE_SERVICE_TRANSCRIPTION:
        return transcribe_via_service(audio_data, sample_rate, language)
    return transcribe_via_groq_direct(audio_data, sample_rate, language, whisper_prompt)
