"""Tests for POST /tts and ServiceClient.tts() (Slice 4.1)."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from auto_whisper_service.auth import AUTH_HEADER


@pytest.fixture
def isolated_token_path(tmp_path: Path, monkeypatch) -> Path:
    token_file = tmp_path / "service-token"
    monkeypatch.setattr("auto_whisper_service.config.TOKEN_FILE", token_file)
    monkeypatch.setattr("auto_whisper_service.auth.TOKEN_FILE", token_file)
    return token_file


@pytest.fixture
def app(isolated_token_path):
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


@pytest.fixture
def mock_synthesize(monkeypatch):
    """Patch the bound `synthesize` name inside the route module.

    The route imports `synthesize` at module load time, so patching the
    source `auto_whisper.voice_agent.synthesize` is not enough — we have
    to patch the route's local binding.
    """
    state: dict = {
        "return_value": (b"FAKE_MP3_BYTES", "mp3"),
        "raises": None,
        "calls": [],
    }

    def _fake_synthesize(text, backend="edge", voice=None):
        state["calls"].append({"text": text, "backend": backend, "voice": voice})
        if state["raises"] is not None:
            raise state["raises"]
        return state["return_value"]

    monkeypatch.setattr(
        "auto_whisper_service.routes.tts.synthesize", _fake_synthesize
    )
    return state


# --- happy path ---

def test_tts_returns_audio_bytes_on_200(client, auth_headers, mock_synthesize):
    response = client.post(
        "/tts",
        headers=auth_headers,
        json={"text": "Hola mundo"},
    )
    assert response.status_code == 200, response.text
    assert response.content == b"FAKE_MP3_BYTES"
    assert response.headers["X-TTS-Backend"] == "edge"  # default
    assert response.headers["X-TTS-Format"] == "mp3"
    assert float(response.headers["X-TTS-Duration-S"]) >= 0
    assert response.headers["content-type"] == "audio/mpeg"


def test_tts_default_backend_is_edge(client, auth_headers, mock_synthesize):
    client.post("/tts", headers=auth_headers, json={"text": "x"})
    assert mock_synthesize["calls"][-1]["backend"] == "edge"


def test_tts_uses_explicit_backend(client, auth_headers, mock_synthesize):
    mock_synthesize["return_value"] = (b"AIFF_BYTES", "aiff")
    response = client.post(
        "/tts",
        headers=auth_headers,
        json={"text": "x", "backend": "macos"},
    )
    assert response.status_code == 200
    assert response.headers["X-TTS-Backend"] == "macos"
    assert response.headers["X-TTS-Format"] == "aiff"
    assert response.headers["content-type"] == "audio/aiff"
    assert mock_synthesize["calls"][-1]["backend"] == "macos"


def test_tts_passes_voice_through(client, auth_headers, mock_synthesize):
    client.post(
        "/tts",
        headers=auth_headers,
        json={"text": "x", "voice": "es-ES-AlvaroNeural"},
    )
    assert mock_synthesize["calls"][-1]["voice"] == "es-ES-AlvaroNeural"


def test_tts_voice_defaults_to_none(client, auth_headers, mock_synthesize):
    client.post("/tts", headers=auth_headers, json={"text": "x"})
    assert mock_synthesize["calls"][-1]["voice"] is None


# --- input validation ---

def test_tts_unknown_backend_returns_400(client, auth_headers, mock_synthesize):
    response = client.post(
        "/tts",
        headers=auth_headers,
        json={"text": "x", "backend": "bogus"},
    )
    assert response.status_code == 400
    assert "valid:" in response.json()["detail"]


def test_tts_empty_text_returns_422(client, auth_headers, mock_synthesize):
    response = client.post("/tts", headers=auth_headers, json={"text": ""})
    assert response.status_code == 422  # pydantic min_length=1


def test_tts_missing_text_returns_422(client, auth_headers, mock_synthesize):
    response = client.post("/tts", headers=auth_headers, json={})
    assert response.status_code == 422


def test_tts_oversize_text_returns_413(client, auth_headers, mock_synthesize, monkeypatch):
    monkeypatch.setattr("auto_whisper_service.routes.tts.MAX_TTS_CHARS", 100)
    response = client.post(
        "/tts",
        headers=auth_headers,
        json={"text": "x" * 200},
    )
    assert response.status_code == 413


# --- auth ---

def test_tts_requires_auth(client, mock_synthesize):
    response = client.post("/tts", json={"text": "x"})
    assert response.status_code == 401


def test_tts_rejects_wrong_token(client, mock_synthesize):
    response = client.post(
        "/tts",
        headers={AUTH_HEADER: "wrong"},
        json={"text": "x"},
    )
    assert response.status_code == 401


# --- service-side errors ---

def test_tts_502_when_all_backends_fail(client, auth_headers, mock_synthesize):
    """synthesize() walks the fallback chain internally; when every backend
    fails it returns None — surface as 502 (upstream stack didn't deliver)."""
    mock_synthesize["return_value"] = None
    response = client.post("/tts", headers=auth_headers, json={"text": "x"})
    assert response.status_code == 502
    assert "all TTS backends failed" in response.json()["detail"]


def test_tts_500_when_synthesize_raises(client, auth_headers, mock_synthesize):
    """synthesize() catches per-backend exceptions internally; reaching here
    means a programming error — surface as 500."""
    mock_synthesize["raises"] = RuntimeError("unexpected bug")
    response = client.post("/tts", headers=auth_headers, json={"text": "x"})
    assert response.status_code == 500


# --- ServiceClient.tts() ---

def _make_client(handler, isolated_token_path) -> "ServiceClient":  # type: ignore[name-defined]
    from auto_whisper.service_client import ServiceClient

    sc = ServiceClient()
    auth_headers = {AUTH_HEADER: sc._client.headers[AUTH_HEADER]}
    sc._client.close()
    sc._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=sc.base_url,
        headers=auth_headers,
        timeout=2.0,
    )
    return sc


def test_client_tts_returns_bytes_and_ext_on_200(isolated_token_path):
    def handler(req):
        return httpx.Response(
            200,
            content=b"BYTES",
            headers={
                "X-TTS-Backend": "edge",
                "X-TTS-Format": "mp3",
                "X-TTS-Duration-S": "0.42",
            },
        )

    sc = _make_client(handler, isolated_token_path)
    try:
        result = sc.tts("hola")
        assert result == (b"BYTES", "mp3")
    finally:
        sc.close()


def test_client_tts_sends_text_only_when_backend_voice_omitted(isolated_token_path):
    captured = {}

    def handler(req):
        captured["body"] = req.read()
        return httpx.Response(
            200,
            content=b"x",
            headers={"X-TTS-Format": "mp3"},
        )

    sc = _make_client(handler, isolated_token_path)
    try:
        sc.tts("hola")
    finally:
        sc.close()

    body = captured["body"].decode()
    assert '"text":' in body and "hola" in body
    assert "backend" not in body  # omitted
    assert "voice" not in body  # omitted


def test_client_tts_includes_backend_and_voice_when_passed(isolated_token_path):
    captured = {}

    def handler(req):
        captured["body"] = req.read()
        return httpx.Response(
            200,
            content=b"x",
            headers={"X-TTS-Format": "aiff"},
        )

    sc = _make_client(handler, isolated_token_path)
    try:
        sc.tts("hola", backend="macos", voice="Mónica")
    finally:
        sc.close()

    body = captured["body"].decode()
    assert "macos" in body
    assert "Mónica" in body


def test_client_tts_returns_none_on_400(isolated_token_path):
    def handler(req):
        return httpx.Response(400, json={"detail": "unknown backend"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.tts("x", backend="bogus") is None
    finally:
        sc.close()


def test_client_tts_returns_none_on_401(isolated_token_path):
    def handler(req):
        return httpx.Response(401, json={"detail": "auth"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.tts("x") is None
    finally:
        sc.close()


def test_client_tts_returns_none_on_502(isolated_token_path):
    def handler(req):
        return httpx.Response(502, json={"detail": "all backends failed"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.tts("x") is None
    finally:
        sc.close()


def test_client_tts_returns_none_on_connection_error(isolated_token_path):
    def handler(req):
        raise httpx.ConnectError("refused")

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.tts("x") is None
    finally:
        sc.close()


def test_client_tts_returns_none_when_format_header_missing(isolated_token_path):
    """Service should always set X-TTS-Format on 200; if it's missing we
    can't know how to write the file, so treat as failure."""
    def handler(req):
        return httpx.Response(200, content=b"bytes", headers={})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.tts("x") is None
    finally:
        sc.close()
