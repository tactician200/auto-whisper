"""
Integration tests for VocabManager wired into transcription pipeline (Slice 2.4c).

Verify:
- transcribe_via_groq_direct uses VocabManager hint as Whisper `prompt`
- transcribe_via_groq_direct applies vocab corrections AFTER cleanup
- transcribe_via_service forwards ACTIVE_PROJECT to ServiceClient
- Service /transcribe consumes `project` form field for hint+corrections
- Empty vocab → no `prompt` parameter sent to Groq, text passes through unchanged
- Explicit whisper_prompt arg overrides vocab-generated hint
"""

import io
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest


def _make_audio(duration_s: float = 0.5, sample_rate: int = 16000) -> np.ndarray:
    n = int(duration_s * sample_rate)
    return np.random.uniform(-0.01, 0.01, size=n).astype(np.float32)


def _make_wav_bytes(duration_s: float = 0.5, sample_rate: int = 16000) -> bytes:
    n = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(n * 2))
    return buf.getvalue()


# --- transcribe_via_groq_direct + vocab ---

def test_groq_direct_sends_vocab_hint_as_prompt(monkeypatch, isolated_vocab_db):
    from auto_whisper import transcription
    from shared.vocab import VocabManager

    # Pre-populate vocab so a hint is generated
    vm = VocabManager(isolated_vocab_db)
    vm.add_term("Maisu", variants=["maizu"])
    vm.add_term("Antigravity")

    fake_groq = MagicMock()
    fake_groq.audio.transcriptions.create.return_value = "transcribed text"
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_groq)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")

    transcription.transcribe_via_groq_direct(_make_audio(), sample_rate=16000, language="es")

    call_kwargs = fake_groq.audio.transcriptions.create.call_args.kwargs
    assert "prompt" in call_kwargs
    # Hint contains both canonical terms (any order)
    assert "Maisu" in call_kwargs["prompt"]
    assert "Antigravity" in call_kwargs["prompt"]


def test_groq_direct_omits_prompt_when_vocab_empty(monkeypatch, isolated_vocab_db):
    """No vocab → no `prompt` param sent (empty string is treated as none)."""
    from auto_whisper import transcription

    fake_groq = MagicMock()
    fake_groq.audio.transcriptions.create.return_value = "x"
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_groq)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")

    transcription.transcribe_via_groq_direct(_make_audio(), sample_rate=16000, language="es")

    assert "prompt" not in fake_groq.audio.transcriptions.create.call_args.kwargs


def test_groq_direct_explicit_whisper_prompt_overrides_vocab(monkeypatch, isolated_vocab_db):
    from auto_whisper import transcription
    from shared.vocab import VocabManager

    vm = VocabManager(isolated_vocab_db)
    vm.add_term("Maisu")

    fake_groq = MagicMock()
    fake_groq.audio.transcriptions.create.return_value = "x"
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_groq)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")

    transcription.transcribe_via_groq_direct(
        _make_audio(), sample_rate=16000, language="es", whisper_prompt="OVERRIDE"
    )

    assert fake_groq.audio.transcriptions.create.call_args.kwargs["prompt"] == "OVERRIDE"


def test_groq_direct_applies_vocab_corrections_to_response(monkeypatch, isolated_vocab_db):
    from auto_whisper import transcription
    from shared.vocab import VocabManager

    vm = VocabManager(isolated_vocab_db)
    vm.add_term("Maisu", variants=["maizu"])

    fake_groq = MagicMock()
    fake_groq.audio.transcriptions.create.return_value = "vamos a maizu hoy"
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_groq)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")

    result = transcription.transcribe_via_groq_direct(
        _make_audio(), sample_rate=16000, language="es"
    )
    assert result == "vamos a Maisu hoy"


def test_groq_direct_correction_runs_after_artifact_cleanup(monkeypatch, isolated_vocab_db):
    """Whisper hallucination → cleanup drops it → no further work."""
    from auto_whisper import transcription
    from shared.vocab import VocabManager

    vm = VocabManager(isolated_vocab_db)
    vm.add_term("Maisu", variants=["maizu"])

    fake_groq = MagicMock()
    fake_groq.audio.transcriptions.create.return_value = "Subtítulos por la comunidad de Amara org"
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_groq)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")

    # Cleanup drops the artifact entirely → returns None (not corrected text)
    assert transcription.transcribe_via_groq_direct(
        _make_audio(), sample_rate=16000, language="es"
    ) is None


def test_groq_direct_uses_active_project_for_hint_and_corrections(monkeypatch, isolated_vocab_db):
    from auto_whisper import transcription
    from shared.vocab import VocabManager

    vm = VocabManager(isolated_vocab_db)
    vm.add_term("AlphaTerm", variants=["alfa"], project="proj_a")
    vm.add_term("BetaTerm", variants=["beta"], project="proj_b")

    fake_groq = MagicMock()
    fake_groq.audio.transcriptions.create.return_value = "vi alfa y beta"
    monkeypatch.setattr(transcription, "get_groq_client", lambda: fake_groq)
    monkeypatch.setattr(transcription, "GROQ_API_KEY_DICTATION", "test-key")
    monkeypatch.setattr(transcription, "ACTIVE_PROJECT", "proj_a")

    result = transcription.transcribe_via_groq_direct(
        _make_audio(), sample_rate=16000, language="es"
    )
    # Only proj_a vocab applies → "alfa" → "AlphaTerm"; "beta" stays
    assert result == "vi AlphaTerm y beta"

    # Hint should reflect proj_a only
    hint = fake_groq.audio.transcriptions.create.call_args.kwargs["prompt"]
    assert "AlphaTerm" in hint
    assert "BetaTerm" not in hint


# --- transcribe_via_service forwards project ---

def test_service_path_forwards_active_project(monkeypatch, isolated_vocab_db):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.transcribe.return_value = {
        "text": "x", "language": "es", "duration_s": 0.0, "cleaned": False,
    }
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)
    monkeypatch.setattr(transcription, "ACTIVE_PROJECT", "maisu")

    transcription.transcribe_via_service(_make_audio(), sample_rate=16000, language="es")

    assert fake_client.transcribe.call_args.kwargs.get("project") == "maisu"


def test_service_path_passes_none_project_when_unset(monkeypatch, isolated_vocab_db):
    from auto_whisper import transcription

    fake_client = MagicMock()
    fake_client.transcribe.return_value = {
        "text": "x", "language": "es", "duration_s": 0.0, "cleaned": False,
    }
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)
    monkeypatch.setattr(transcription, "ACTIVE_PROJECT", None)

    transcription.transcribe_via_service(_make_audio(), sample_rate=16000, language="es")

    assert fake_client.transcribe.call_args.kwargs.get("project") is None


# --- Service endpoint /transcribe + vocab ---

@pytest.fixture
def configured_service_app(isolated_vocab_db, monkeypatch):
    """FastAPI app wired with isolated token + Groq mock + isolated vocab DB."""
    token_file = isolated_vocab_db.parent / "service-token"
    monkeypatch.setattr("auto_whisper_service.config.TOKEN_FILE", token_file)
    monkeypatch.setattr("auto_whisper_service.auth.TOKEN_FILE", token_file)
    monkeypatch.setattr(
        "auto_whisper_service.routes.transcribe.GROQ_API_KEY_DICTATION",
        "test-key-sentinel",
    )

    fake_groq = MagicMock()
    fake_groq.audio.transcriptions.create.return_value = "vamos a maizu hoy"
    monkeypatch.setattr(
        "auto_whisper_service.routes.transcribe.get_groq_client",
        lambda: fake_groq,
    )

    from auto_whisper_service.app import create_app
    return create_app(), fake_groq, token_file


def test_service_transcribe_uses_vocab_hint_and_corrections(configured_service_app):
    from fastapi.testclient import TestClient

    from auto_whisper_service.auth import AUTH_HEADER
    from shared.vocab import VocabManager

    app, fake_groq, token_file = configured_service_app
    token = token_file.read_text().strip()

    # Populate vocab BEFORE calling — service reads from same DB
    from auto_whisper_service.routes.transcribe import _get_vocab_manager
    vm = _get_vocab_manager()
    vm.add_term("Maisu", variants=["maizu"])

    client = TestClient(app)
    response = client.post(
        "/transcribe",
        headers={AUTH_HEADER: token},
        files={"audio": ("d.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # "vamos a maizu hoy" → "vamos a Maisu hoy"
    assert body["text"] == "vamos a Maisu hoy"

    # Hint was sent to Groq
    assert "Maisu" in fake_groq.audio.transcriptions.create.call_args.kwargs["prompt"]


def test_service_transcribe_scopes_vocab_by_project(configured_service_app):
    from fastapi.testclient import TestClient

    from auto_whisper_service.auth import AUTH_HEADER
    from auto_whisper_service.routes.transcribe import _get_vocab_manager

    app, fake_groq, token_file = configured_service_app
    token = token_file.read_text().strip()

    vm = _get_vocab_manager()
    vm.add_term("AlphaTerm", variants=["maizu"], project="proj_a")
    vm.add_term("BetaTerm", variants=["maizu"], project="proj_b")

    fake_groq.audio.transcriptions.create.return_value = "vi maizu hoy"

    client = TestClient(app)
    response = client.post(
        "/transcribe",
        headers={AUTH_HEADER: token},
        data={"project": "proj_b"},
        files={"audio": ("d.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert response.json()["text"] == "vi BetaTerm hoy"

    # Hint must reflect proj_b vocabulary, not proj_a
    hint = fake_groq.audio.transcriptions.create.call_args.kwargs["prompt"]
    assert "BetaTerm" in hint
    assert "AlphaTerm" not in hint


def test_service_transcribe_no_vocab_no_corrections(configured_service_app):
    """Empty vocab → text returned unchanged + no `prompt` parameter."""
    from fastapi.testclient import TestClient

    from auto_whisper_service.auth import AUTH_HEADER

    app, fake_groq, token_file = configured_service_app
    token = token_file.read_text().strip()

    fake_groq.audio.transcriptions.create.return_value = "texto sin variantes"

    client = TestClient(app)
    response = client.post(
        "/transcribe",
        headers={AUTH_HEADER: token},
        files={"audio": ("d.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert response.json()["text"] == "texto sin variantes"
    assert "prompt" not in fake_groq.audio.transcriptions.create.call_args.kwargs


# --- ServiceClient.transcribe sends project ---

def test_service_client_sends_project_in_form(isolated_vocab_db, monkeypatch):
    """Independent of routing — verify the client wire format includes project."""
    import httpx

    from auto_whisper.service_client import ServiceClient
    from auto_whisper_service.auth import AUTH_HEADER

    # Isolate the service token too — ServiceClient.__init__ reads it.
    token_file = isolated_vocab_db.parent / "service-token"
    monkeypatch.setattr("auto_whisper_service.config.TOKEN_FILE", token_file)
    monkeypatch.setattr("auto_whisper_service.auth.TOKEN_FILE", token_file)

    captured = {}

    def handler(req):
        captured["body"] = req.read()
        return httpx.Response(
            200, json={"text": "x", "language": "es", "duration_s": 0.0, "cleaned": False}
        )

    sc = ServiceClient()
    sc._client.close()
    sc._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=sc.base_url,
        headers={AUTH_HEADER: token_file.read_text().strip()},
        timeout=2.0,
    )
    try:
        sc.transcribe(b"WAVbytes", project="maisu")
    finally:
        sc.close()

    assert b'name="project"' in captured["body"]
    assert b"maisu" in captured["body"]


def test_service_client_omits_project_when_none(isolated_vocab_db, monkeypatch):
    import httpx

    from auto_whisper.service_client import ServiceClient
    from auto_whisper_service.auth import AUTH_HEADER

    token_file = isolated_vocab_db.parent / "service-token"
    monkeypatch.setattr("auto_whisper_service.config.TOKEN_FILE", token_file)
    monkeypatch.setattr("auto_whisper_service.auth.TOKEN_FILE", token_file)

    captured = {}

    def handler(req):
        captured["body"] = req.read()
        return httpx.Response(
            200, json={"text": "x", "language": "es", "duration_s": 0.0, "cleaned": False}
        )

    sc = ServiceClient()
    sc._client.close()
    sc._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=sc.base_url,
        headers={AUTH_HEADER: token_file.read_text().strip()},
        timeout=2.0,
    )
    try:
        sc.transcribe(b"WAVbytes", project=None)
    finally:
        sc.close()

    assert b'name="project"' not in captured["body"]
