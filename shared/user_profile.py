"""User profile stub — minimal surface for v5 beta.

Holds the few preferences that already affect routing / behavior in v5:
- privacy_mode: when True, no network calls (forces local whisper.cpp,
  forces TTS backend=macos, blocks LLM modes that go to Groq).

The full schema (explanation_depth slider, prompt_structure, language
preferences, calibration signals) lands with Phase 5 onboarding wizard.
This module is the empty shell those values plug into later.

Storage is in-memory for now — Phase 5 will back this with SQLite at
~/Library/Application Support/auto-whisper/profile.db. Until then, env
vars are the only way to flip values (useful for testing).

The singleton accessor pattern (`get_tts_preferences`) mirrors the
service-client pattern so callers don't have to thread a profile object
through every layer.
"""

import os
from dataclasses import dataclass


@dataclass
class TTSPreferences:
    """TTS-relevant slice of UserProfile. Phase 4.3 scope: privacy_mode only.

    Future fields (Phase 4.4 / 5):
    - voice_per_backend: dict[str, str | None]
    - rate: float (0.5 – 2.0)
    """

    privacy_mode: bool = False


def _read_privacy_mode() -> bool:
    """AUTO_WHISPER_PRIVACY_MODE=1 forces all paths offline.

    Read at access time (not module load) so tests and the upcoming
    Settings UI can flip it without restarting. Profile DB will replace
    this in Phase 5; env stays as override.
    """
    return os.environ.get("AUTO_WHISPER_PRIVACY_MODE", "0") == "1"


def get_tts_preferences() -> TTSPreferences:
    """Resolve the live TTS preferences.

    Phase 4 stub: env-driven only. Phase 5 will read from a persisted
    profile DB and fall back to env when the field isn't set.
    """
    return TTSPreferences(privacy_mode=_read_privacy_mode())


def is_privacy_mode() -> bool:
    """Public accessor — same value as the TTS preference, exposed for UI use."""
    return _read_privacy_mode()
