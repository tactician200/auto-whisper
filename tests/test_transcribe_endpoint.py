"""
Tests for POST /transcribe.

All Groq calls mocked — never hits the network. WAV bytes are generated
in-memory by helper functions so tests don't depend on fixture files.
"""

import io
import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from auto_whisper_service.auth import AUTH_HEADER


# --- helpers ---

def _make_wav_bytes(duration_s: float = 0.5, sample_rate: int = 16000) -> bytes:
    """Build a valid mono 16-bit WAV in memory."""
    n_samples = int(duration_s * sample_rate)
    samples = bytes(n_samples * 2)  # silence; valid WAV header is what matters
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples)
    return buf.getvalue()


def _make_stereo_wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(bytes(8000 * 2 * 2))  # 0.5s stereo
    return buf.getvalue()


# --- fixtures ---

@pytest.fixture
def isolated_token_path(tmp_path: Path, monkeypatch) -> Path:
    token_file = tmp_path / "service-token"
    monkeypatch.setattr("auto_whisper_service.config.TOKEN_FILE", token_file)
    monkeypatch.setattr("auto_whisper_service.auth.TOKEN_FILE", token_file)
    return token_file


@pytest.fixture
def configured_groq_key(monkeypatch) -> str:
    """Force GROQ_API_KEY_DICTATION to a sentinel so the route doesn't 503."""
    sentinel = "test-key-sentinel"
    monkeypatch.setattr(
        "auto_whisper_service.routes.transcribe.GROQ_API_KEY_DICTATION",
        sentinel,
    )
    return sentinel


@pytest.fixture
def mock_groq(monkeypatch, configured_groq_key):
    """Patch the Groq client used by the transcribe route."""
    client = MagicMock()
    client.audio.transcriptions.create.return_value = "Hola mundo desde el test."
    monkeypatch.setattr(
        "auto_whisper_service.routes.transcribe.get_groq_client",
        lambda: client,
    )
    return client


@pytest.fixture
def app(isolated_token_path, mock_groq):
    from auto_whisper_service.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def valid_token(isolated_token_path) -> str:
    return isolated_token_path.read_text().strip()


@pytest.fixture
def auth_headers(valid_token) -> dict[str, str]:
    return {AUTH_HEADER: valid_token}


# --- happy path ---

def test_transcribe_valid_wav_returns_text(client, auth_headers):
    wav = _make_wav_bytes()
    response = client.post(
        "/transcribe",
        headers=auth_headers,
        files={"audio": ("dictation.wav", wav, "audio/wav")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "Hola mundo desde el test."
    assert body["language"] == "es"
    assert body["duration_s"] >= 0
    assert body["cleaned"] is False


def test_transcribe_passes_language_to_groq(client, auth_headers, mock_groq):
    wav = _make_wav_bytes()
    client.post(
        "/transcribe",
        headers=auth_headers,
        data={"language": "en"},
        files={"audio": ("d.wav", wav, "audio/wav")},
    )
    call_kwargs = mock_groq.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["language"] == "en"
    assert call_kwargs["model"] == "whisper-large-v3"
    assert call_kwargs["response_format"] == "text"


def test_transcribe_default_language_is_spanish(client, auth_headers, mock_groq):
    wav = _make_wav_bytes()
    client.post(
        "/transcribe",
        headers=auth_headers,
        files={"audio": ("d.wav", wav, "audio/wav")},
    )
    assert mock_groq.audio.transcriptions.create.call_args.kwargs["language"] == "es"


def test_transcribe_drops_whisper_artifact_and_flags_cleaned(client, auth_headers, mock_groq):
    mock_groq.audio.transcriptions.create.return_value = "Subtítulos realizados por la comunidad de Amara org"
    wav = _make_wav_bytes()
    response = client.post(
        "/transcribe",
        headers=auth_headers,
        files={"audio": ("d.wav", wav, "audio/wav")},
    )
    body = response.json()
    assert body["text"] is None
    assert body["cleaned"] is True


def test_transcribe_does_not_flag_cleaned_when_text_unchanged(client, auth_headers, mock_groq):
    mock_groq.audio.transcriptions.create.return_value = "Texto normal sin artefactos"
    wav = _make_wav_bytes()
    response = client.post(
        "/transcribe",
        headers=auth_headers,
        files={"audio": ("d.wav", wav, "audio/wav")},
    )
    body = response.json()
    assert body["text"] == "Texto normal sin artefactos"
    assert body["cleaned"] is False


# --- auth ---

def test_transcribe_requires_auth(client):
    wav = _make_wav_bytes()
    response = client.post(
        "/transcribe",
        files={"audio": ("d.wav", wav, "audio/wav")},
    )
    assert response.status_code == 401


def test_transcribe_rejects_wrong_token(client):
    wav = _make_wav_bytes()
    response = client.post(
        "/transcribe",
        headers={AUTH_HEADER: "wrong"},
        files={"audio": ("d.wav", wav, "audio/wav")},
    )
    assert response.status_code == 401


# --- input validation ---

def test_transcribe_rejects_missing_file(client, auth_headers):
    response = client.post("/transcribe", headers=auth_headers)
    assert response.status_code == 422  # FastAPI auto-validation


def test_transcribe_rejects_empty_payload(client, auth_headers):
    response = client.post(
        "/transcribe",
        headers=auth_headers,
        files={"audio": ("d.wav", b"", "audio/wav")},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_transcribe_rejects_non_wav(client, auth_headers):
    response = client.post(
        "/transcribe",
        headers=auth_headers,
        files={"audio": ("d.mp3", b"\xff\xfb\x90\x00garbage", "audio/mpeg")},
    )
    assert response.status_code == 400
    assert "wav" in response.json()["detail"].lower()


def test_transcribe_rejects_stereo(client, auth_headers):
    wav = _make_stereo_wav_bytes()
    response = client.post(
        "/transcribe",
        headers=auth_headers,
        files={"audio": ("d.wav", wav, "audio/wav")},
    )
    assert response.status_code == 400
    assert "mono" in response.json()["detail"].lower()


def test_transcribe_rejects_oversize(client, auth_headers, monkeypatch):
    monkeypatch.setattr(
        "auto_whisper_service.routes.transcribe.MAX_AUDIO_BYTES",
        100,  # tiny limit for test
    )
    wav = _make_wav_bytes(duration_s=1.0)  # ~32KB > 100B
    response = client.post(
        "/transcribe",
        headers=auth_headers,
        files={"audio": ("d.wav", wav, "audio/wav")},
    )
    assert response.status_code == 413


# --- service-side errors ---

def test_transcribe_503_when_groq_key_missing(client, auth_headers, monkeypatch):
    monkeypatch.setattr(
        "auto_whisper_service.routes.transcribe.GROQ_API_KEY_DICTATION",
        "",
    )
    wav = _make_wav_bytes()
    response = client.post(
        "/transcribe",
        headers=auth_headers,
        files={"audio": ("d.wav", wav, "audio/wav")},
    )
    assert response.status_code == 503


def test_transcribe_502_when_groq_raises(client, auth_headers, mock_groq):
    mock_groq.audio.transcriptions.create.side_effect = RuntimeError("upstream timeout")
    wav = _make_wav_bytes()
    response = client.post(
        "/transcribe",
        headers=auth_headers,
        files={"audio": ("d.wav", wav, "audio/wav")},
    )
    assert response.status_code == 502


def test_transcribe_502_when_groq_returns_unexpected_type(client, auth_headers, mock_groq):
    mock_groq.audio.transcriptions.create.return_value = {"unexpected": "shape"}
    wav = _make_wav_bytes()
    response = client.post(
        "/transcribe",
        headers=auth_headers,
        files={"audio": ("d.wav", wav, "audio/wav")},
    )
    assert response.status_code == 502
