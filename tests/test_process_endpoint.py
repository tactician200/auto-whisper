"""Tests for POST /process and ServiceClient.process()."""

from pathlib import Path
from unittest.mock import MagicMock

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
def mock_modes(monkeypatch):
    """Replace shared.processing.MODES with a controllable map.

    Tests can adjust the return value via the returned `responses` dict
    (mode → return value) or `raises` dict (mode → exception to raise).
    """
    responses: dict = {}
    raises: dict = {}

    def _make_fn(mode_name):
        def _fn(text):
            if mode_name in raises:
                raise raises[mode_name]
            return responses.get(mode_name, f"PROCESSED({mode_name})")
        return _fn

    fake_modes = {
        m: _make_fn(m) for m in
        ("summarize", "explain", "explain_paste", "organize_ideas", "optimize_prompt")
    }

    # Patch where the route looks up MODES — it imports it at module load so
    # we must patch the route module's bound name.
    monkeypatch.setattr("auto_whisper_service.routes.process.MODES", fake_modes)
    return {"responses": responses, "raises": raises, "modes": fake_modes}


# --- happy path ---

def test_process_summarize_returns_result(client, auth_headers, mock_modes):
    mock_modes["responses"]["summarize"] = "Texto resumido."
    response = client.post(
        "/process",
        headers=auth_headers,
        json={"mode": "summarize", "text": "Algo largo que resumir..."},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "Texto resumido."
    assert body["mode"] == "summarize"
    assert body["duration_s"] >= 0


@pytest.mark.parametrize("mode", [
    "summarize", "explain", "explain_paste", "organize_ideas", "optimize_prompt"
])
def test_process_accepts_each_mode(client, auth_headers, mock_modes, mode):
    response = client.post(
        "/process",
        headers=auth_headers,
        json={"mode": mode, "text": "input text"},
    )
    assert response.status_code == 200
    assert response.json()["mode"] == mode


def test_process_returns_none_when_underlying_returns_none(client, auth_headers, mock_modes):
    mock_modes["responses"]["summarize"] = None  # simulate "no API key" / Groq exception
    response = client.post(
        "/process",
        headers=auth_headers,
        json={"mode": "summarize", "text": "x"},
    )
    assert response.status_code == 200
    assert response.json()["result"] is None


# --- input validation ---

def test_process_unknown_mode_returns_400(client, auth_headers, mock_modes):
    response = client.post(
        "/process",
        headers=auth_headers,
        json={"mode": "bogus_mode", "text": "x"},
    )
    assert response.status_code == 400
    assert "valid:" in response.json()["detail"]


def test_process_empty_text_rejected_as_validation(client, auth_headers, mock_modes):
    response = client.post(
        "/process",
        headers=auth_headers,
        json={"mode": "summarize", "text": ""},
    )
    # Pydantic enforces min_length=1 → 422
    assert response.status_code == 422


def test_process_missing_field_returns_422(client, auth_headers, mock_modes):
    response = client.post(
        "/process",
        headers=auth_headers,
        json={"mode": "summarize"},  # missing text
    )
    assert response.status_code == 422


def test_process_oversize_text_returns_413(client, auth_headers, mock_modes, monkeypatch):
    monkeypatch.setattr(
        "auto_whisper_service.routes.process.MAX_TEXT_CHARS", 100
    )
    response = client.post(
        "/process",
        headers=auth_headers,
        json={"mode": "summarize", "text": "x" * 200},
    )
    assert response.status_code == 413


# --- auth ---

def test_process_requires_auth(client, mock_modes):
    response = client.post("/process", json={"mode": "summarize", "text": "x"})
    assert response.status_code == 401


def test_process_rejects_wrong_token(client, mock_modes):
    response = client.post(
        "/process",
        headers={AUTH_HEADER: "wrong"},
        json={"mode": "summarize", "text": "x"},
    )
    assert response.status_code == 401


# --- service-side errors ---

def test_process_500_when_mode_callable_raises(client, auth_headers, mock_modes):
    """shared.processing already swallows Groq exceptions; reaching here means
    a programming error, surface as 500 for visibility."""
    mock_modes["raises"]["summarize"] = RuntimeError("unexpected bug")
    response = client.post(
        "/process",
        headers=auth_headers,
        json={"mode": "summarize", "text": "x"},
    )
    assert response.status_code == 500


# --- ServiceClient.process() (Slice 3.2 client side) ---

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


def test_client_process_returns_result_on_200(isolated_token_path):
    payload = {"result": "summarized", "mode": "summarize", "duration_s": 0.42}

    def handler(req):
        return httpx.Response(200, json=payload)

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.process("summarize", "raw input") == payload
    finally:
        sc.close()


def test_client_process_sends_json_with_mode_and_text(isolated_token_path):
    captured = {}

    def handler(req):
        captured["body"] = req.read()
        captured["headers"] = dict(req.headers)
        return httpx.Response(
            200, json={"result": "x", "mode": "summarize", "duration_s": 0.0}
        )

    sc = _make_client(handler, isolated_token_path)
    try:
        sc.process("summarize", "raw input text")
    finally:
        sc.close()

    body = captured["body"].decode()
    assert '"mode":' in body and "summarize" in body
    assert '"text":' in body and "raw input text" in body
    assert captured["headers"]["content-type"].startswith("application/json")


def test_client_process_returns_none_on_400(isolated_token_path):
    def handler(req):
        return httpx.Response(400, json={"detail": "unknown mode"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.process("bogus", "x") is None
    finally:
        sc.close()


def test_client_process_returns_none_on_401(isolated_token_path):
    def handler(req):
        return httpx.Response(401, json={"detail": "auth"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.process("summarize", "x") is None
    finally:
        sc.close()


def test_client_process_returns_none_on_connection_error(isolated_token_path):
    def handler(req):
        raise httpx.ConnectError("refused")

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.process("summarize", "x") is None
    finally:
        sc.close()
