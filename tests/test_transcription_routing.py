"""
Tests for auto_whisper.transcription — cloud routing dispatcher.

Verify:
- USE_SERVICE_TRANSCRIPTION reads AUTO_WHISPER_USE_SERVICE env var.
- transcribe_cloud dispatches to the correct path based on the flag.
- transcribe_via_service constructs WAV bytes and calls ServiceClient correctly.
- transcribe_via_groq_direct constructs WAV bytes and calls Groq correctly.
- Both paths return None on upstream failures, log without raising.

The flag is read at module import; tests that need to flip it use importlib.reload
to get a fresh module bound to the patched env.
"""

import importlib
import io
import os
import wave
from unittest.mock import MagicMock

import numpy as np
import pytest


# --- helpers ---

def _make_audio(duration_s: float = 0.5, sample_rate: int = 16000) -> np.ndarray:
    """Float32 audio array, low-level noise (NOT silence — Whisper hallucinates on silence)."""
    n = int(duration_s * sample_rate)
    return np.random.uniform(-0.01, 0.01, size=n).astype(np.float32)


def _decode_wav_header(wav_bytes: bytes) -> tuple[int, int, int]:
    """Return (channels, sampwidth, framerate) — verifies wav structure."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        return wf.getnchannels(), wf.getsampwidth(), wf.getframerate()


# --- encode_wav ---

def test_encode_wav_produces_mono_16bit_at_requested_rate():
    from auto_whisper.transcription import encode_wav

    audio = _make_audio(duration_s=0.5, sample_rate=16000)
    wav_bytes = encode_wav(audio, sample_rate=16000)

    channels, sampwidth, rate = _decode_wav_header(wav_bytes)
    assert channels == 1
    assert sampwidth == 2  # 16-bit
    assert rate == 16000


def test_encode_wav_preserves_sample_count():
    from auto_whisper.transcription import encode_wav

    audio = _make_audio(duration_s=1.0, sample_rate=16000)
    wav_bytes = encode_wav(audio, sample_rate=16000)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnframes() == 16000


# --- transcribe_via_service ---

def test_transcribe_via_service_returns_text_from_response(monkeypatch):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.transcribe.return_value = {
        "text": "transcribed via service",
        "language": "es",
        "duration_s": 0.3,
        "cleaned": False,
    }
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    audio = _make_audio()
    result = transcription.transcribe_via_service(audio, sample_rate=16000, language="es")
    assert result == "transcribed via service"


def test_transcribe_via_service_passes_wav_and_language(monkeypatch):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.transcribe.return_value = {
        "text": "x", "language": "en", "duration_s": 0.0, "cleaned": False
    }
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    audio = _make_audio()
    transcription.transcribe_via_service(audio, sample_rate=16000, language="en")

    call_args = fake_client.transcribe.call_args
    wav_arg = call_args.args[0]
    assert isinstance(wav_arg, bytes)
    # Verify it's a parseable WAV
    channels, sampwidth, rate = _decode_wav_header(wav_arg)
    assert (channels, sampwidth, rate) == (1, 2, 16000)
    # Language passed as kwarg
    assert call_args.kwargs.get("language") == "en"


def test_transcribe_via_service_returns_none_when_client_returns_none(monkeypatch):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.transcribe.return_value = None
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    audio = _make_audio()
    assert transcription.transcribe_via_service(audio, sample_rate=16000, language="es") is None


def test_transcribe_via_service_returns_none_when_response_missing_text(monkeypatch):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.transcribe.return_value = {
        "text": None, "language": "es", "duration_s": 0.0, "cleaned": True
    }
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    audio = _make_audio()
    assert transcription.transcribe_via_service(audio, sample_rate=16000, language="es") is None


def test_transcribe_via_service_swallows_exceptions(monkeypatch):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.transcribe.side_effect = RuntimeError("network down")
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    audio = _make_audio()
    # Must not raise — daemon callers expect None on failure.
    assert transcription.transcribe_via_service(audio, sample_rate=16000, language="es") is None


# --- transcribe_via_groq_direct ---

def test_transcribe_via_groq_direct_calls_groq_with_correct_params(monkeypatch):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = "groq direct text"
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_client)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")

    audio = _make_audio()
    result = transcription.transcribe_via_groq_direct(audio, sample_rate=16000, language="es")
    assert result == "groq direct text"

    call_kwargs = fake_client.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["model"] == "whisper-large-v3"
    assert call_kwargs["response_format"] == "text"
    assert call_kwargs["language"] == "es"


def test_transcribe_via_groq_direct_includes_whisper_prompt_when_provided(monkeypatch):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = "x"
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_client)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")

    audio = _make_audio()
    transcription.transcribe_via_groq_direct(
        audio, sample_rate=16000, language="es", whisper_prompt="Maisu, auto-whisper"
    )

    call_kwargs = fake_client.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["prompt"] == "Maisu, auto-whisper"


def test_transcribe_via_groq_direct_omits_prompt_when_none(monkeypatch):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = "x"
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_client)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")

    audio = _make_audio()
    transcription.transcribe_via_groq_direct(audio, sample_rate=16000, language="es")
    assert "prompt" not in fake_client.audio.transcriptions.create.call_args.kwargs


def test_transcribe_via_groq_direct_returns_none_on_no_api_key(monkeypatch):
    from auto_whisper import transcription

    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "")
    audio = _make_audio()
    assert transcription.transcribe_via_groq_direct(audio, sample_rate=16000, language="es") is None


def test_transcribe_via_groq_direct_returns_none_on_groq_exception(monkeypatch):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.side_effect = RuntimeError("groq down")
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_client)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")

    audio = _make_audio()
    assert transcription.transcribe_via_groq_direct(audio, sample_rate=16000, language="es") is None


def test_transcribe_via_groq_direct_returns_none_on_unexpected_response_type(monkeypatch):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = {"unexpected": "shape"}
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_client)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")

    audio = _make_audio()
    assert transcription.transcribe_via_groq_direct(audio, sample_rate=16000, language="es") is None


def test_transcribe_via_groq_direct_applies_artifact_cleanup(monkeypatch):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = "Subtítulos por la comunidad de Amara.org"
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_client)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")

    audio = _make_audio()
    # Cleanup drops the artifact → empty → returns None
    assert transcription.transcribe_via_groq_direct(audio, sample_rate=16000, language="es") is None


# --- transcribe_cloud dispatcher ---

def test_transcribe_cloud_uses_groq_direct_when_flag_off(monkeypatch):
    from auto_whisper import transcription

    monkeypatch.setattr(transcription, "USE_SERVICE_TRANSCRIPTION", False)

    direct_called = {"yes": False}
    service_called = {"yes": False}

    def fake_direct(*a, **kw):
        direct_called["yes"] = True
        return "from-direct"

    def fake_service(*a, **kw):
        service_called["yes"] = True
        return "from-service"

    monkeypatch.setattr(transcription, "transcribe_via_groq_direct", fake_direct)
    monkeypatch.setattr(transcription, "transcribe_via_service", fake_service)

    audio = _make_audio()
    result = transcription.transcribe_cloud(audio, sample_rate=16000, language="es")

    assert result == "from-direct"
    assert direct_called["yes"] is True
    assert service_called["yes"] is False


def test_transcribe_cloud_uses_service_when_flag_on(monkeypatch):
    from auto_whisper import transcription

    monkeypatch.setattr(transcription, "USE_SERVICE_TRANSCRIPTION", True)

    direct_called = {"yes": False}
    service_called = {"yes": False}

    monkeypatch.setattr(transcription, "transcribe_via_groq_direct",
                        lambda *a, **kw: direct_called.update(yes=True) or "from-direct")
    monkeypatch.setattr(transcription, "transcribe_via_service",
                        lambda *a, **kw: service_called.update(yes=True) or "from-service")

    audio = _make_audio()
    result = transcription.transcribe_cloud(audio, sample_rate=16000, language="es")

    assert result == "from-service"
    assert service_called["yes"] is True
    assert direct_called["yes"] is False


# --- USE_SERVICE_TRANSCRIPTION env-var driven ---

@pytest.mark.parametrize("env_value, expected", [
    ("1", True),
    ("0", False),
    ("", False),
    ("true", False),  # only "1" enables — explicit, no truthy parsing
    ("yes", False),
    ("on", False),
])
def test_use_service_transcription_only_truthy_for_value_1(monkeypatch, env_value, expected):
    monkeypatch.setenv("AUTO_WHISPER_USE_SERVICE", env_value)
    import auto_whisper.transcription
    importlib.reload(auto_whisper.transcription)
    try:
        assert auto_whisper.transcription.USE_SERVICE_TRANSCRIPTION is expected
    finally:
        # Reset to default so subsequent tests aren't affected
        monkeypatch.delenv("AUTO_WHISPER_USE_SERVICE", raising=False)
        importlib.reload(auto_whisper.transcription)


def test_use_service_transcription_default_false_when_unset(monkeypatch):
    monkeypatch.delenv("AUTO_WHISPER_USE_SERVICE", raising=False)
    import auto_whisper.transcription
    importlib.reload(auto_whisper.transcription)
    assert auto_whisper.transcription.USE_SERVICE_TRANSCRIPTION is False


# --- set_active_project ---

def test_set_active_project_updates_module_attribute(monkeypatch):
    from auto_whisper import transcription

    monkeypatch.setattr(transcription, "ACTIVE_PROJECT", None)
    transcription.set_active_project("maisu")
    assert transcription.ACTIVE_PROJECT == "maisu"


def test_set_active_project_can_clear_with_none(monkeypatch):
    from auto_whisper import transcription

    monkeypatch.setattr(transcription, "ACTIVE_PROJECT", "old_proj")
    transcription.set_active_project(None)
    assert transcription.ACTIVE_PROJECT is None


def test_set_active_project_treats_empty_string_as_clear(monkeypatch):
    from auto_whisper import transcription

    monkeypatch.setattr(transcription, "ACTIVE_PROJECT", "old_proj")
    transcription.set_active_project("")
    assert transcription.ACTIVE_PROJECT is None
    transcription.set_active_project("   ")
    assert transcription.ACTIVE_PROJECT is None
