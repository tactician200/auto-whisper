"""TTS dispatcher — local synthesis vs via service.

Mirrors auto_whisper.processing_routing's pattern, but for TTS. Toggle:
env var AUTO_WHISPER_USE_SERVICE_TTS=1 (default off). Read once at module
import — restart daemon to flip paths.

Flag OFF: synth + playback happen in-process (voice_agent._speak_local).
Flag ON: HTTP POST /tts → audio bytes → playback in-process (still local
playback because audio device routing belongs to the user's session, not
the service process — see Phase 4 D2 in design-v5.md).

Public API matches voice_agent.speak() exactly so callers can re-route
through here transparently.
"""

import logging
import os

logger = logging.getLogger(__name__)


# Read once at import. Restart to flip.
USE_SERVICE_TTS: bool = (
    os.environ.get("AUTO_WHISPER_USE_SERVICE_TTS", "0") == "1"
)


def _via_service(
    text: str,
    backend: str | None,
    voice: str | None,
    block: bool,
) -> None:
    """Fetch audio bytes from /tts and play locally.

    Returns silently on any failure — caller (voice_agent.speak) gets None
    semantics, same as local-path with all backends down. The user sees no
    audio; the failure is logged at WARNING by ServiceClient.
    """
    # Lazy imports: get_service_client lives in auto_whisper.transcription
    # (the daemon's existing singleton helper from Phase 2). voice_agent's
    # _play_bytes is what actually drives afplay.
    from auto_whisper.transcription import get_service_client
    from auto_whisper.voice_agent import _play_bytes

    response = get_service_client().tts(text, backend=backend, voice=voice)
    if response is None:
        return
    audio_bytes, ext = response
    _play_bytes(audio_bytes, ext, block=block)


def _resolve_backend(requested: str | None) -> str | None:
    """Apply privacy-mode override on top of caller's backend choice.

    Privacy mode forces macOS native TTS (offline `say` command) regardless
    of what the caller asked for. Caller still sees no error — the audio
    just comes from a different backend.

    Returns None when caller didn't specify and privacy mode is off — lets
    downstream resolver pick its own default (voice_agent's DEFAULT_BACKEND
    locally, the service's default when going through HTTP).
    """
    from shared.user_profile import get_tts_preferences

    if get_tts_preferences().privacy_mode:
        if requested and requested != "macos":
            logger.info(f"privacy_mode: overriding TTS backend {requested!r} → 'macos'")
        return "macos"
    return requested


def speak(
    text: str,
    backend: str | None = None,
    voice: str | None = None,
    block: bool = True,
) -> None:
    """Flag-aware TTS speak.

    `backend` defaults to None (let the resolver pick — voice_agent's
    DEFAULT_BACKEND for local, service's DEFAULT_BACKEND for via-service).
    Privacy mode (env AUTO_WHISPER_PRIVACY_MODE=1 for now) forces
    backend='macos' regardless of caller request.
    """
    backend = _resolve_backend(backend)

    if USE_SERVICE_TTS:
        _via_service(text, backend, voice, block)
        return

    # Local path: voice_agent._speak_local treats `backend=None` differently
    # (it has a positional default). Pass through the resolved value.
    from auto_whisper.voice_agent import DEFAULT_BACKEND, _speak_local

    _speak_local(
        text,
        backend=backend or DEFAULT_BACKEND,
        voice=voice,
        block=block,
    )
