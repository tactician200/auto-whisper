"""
Unit tests for auto_whisper_service.

Uses FastAPI's TestClient (built on httpx) — runs the app in-process,
no real network/uvicorn. Token storage is redirected to tmp_path so
tests are isolated and never touch the user's real Application Support.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auto_whisper_service import SCHEMA_VERSION, SERVICE_NAME, __version__
from auto_whisper_service.auth import AUTH_HEADER


@pytest.fixture
def isolated_token_path(tmp_path: Path, monkeypatch) -> Path:
    """Redirect TOKEN_FILE to a tmp_path so tests don't pollute user state."""
    token_file = tmp_path / "service-token"
    monkeypatch.setattr("auto_whisper_service.config.TOKEN_FILE", token_file)
    monkeypatch.setattr("auto_whisper_service.auth.TOKEN_FILE", token_file)
    return token_file


@pytest.fixture
def app(isolated_token_path):
    """Build a fresh app instance with isolated token storage."""
    from auto_whisper_service.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def valid_token(isolated_token_path) -> str:
    return isolated_token_path.read_text().strip()


# --- /health ---

def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_does_not_require_auth(client):
    # Even without the X-Auth-Token header, /health must respond 200.
    response = client.get("/health")
    assert response.status_code == 200


# --- /version ---

def test_version_requires_auth(client):
    response = client.get("/version")
    assert response.status_code == 401
    assert "auth" in response.json()["detail"].lower()


def test_version_rejects_wrong_token(client):
    response = client.get("/version", headers={AUTH_HEADER: "obviously-wrong"})
    assert response.status_code == 401


def test_version_accepts_valid_token(client, valid_token):
    response = client.get("/version", headers={AUTH_HEADER: valid_token})
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == SERVICE_NAME
    assert body["version"] == __version__
    assert body["schema_version"] == SCHEMA_VERSION


def test_version_response_shape_is_stable(client, valid_token):
    """Schema contract — clients depend on these exact keys."""
    response = client.get("/version", headers={AUTH_HEADER: valid_token})
    body = response.json()
    assert set(body.keys()) == {"service", "version", "schema_version"}


# --- auth module behavior ---

def test_token_is_generated_on_first_app_creation(isolated_token_path):
    assert not isolated_token_path.exists()
    from auto_whisper_service.app import create_app
    create_app()
    assert isolated_token_path.exists()
    token = isolated_token_path.read_text().strip()
    assert len(token) >= 40  # token_urlsafe(32) → ~43 chars


def test_token_persists_across_app_creates(isolated_token_path):
    from auto_whisper_service.app import create_app

    create_app()
    first_token = isolated_token_path.read_text().strip()

    create_app()  # second invocation must NOT regenerate
    second_token = isolated_token_path.read_text().strip()

    assert first_token == second_token


def test_token_file_has_secure_permissions(isolated_token_path):
    from auto_whisper_service.app import create_app

    create_app()
    mode = isolated_token_path.stat().st_mode & 0o777
    assert mode == 0o600, f"token file mode is {oct(mode)}, expected 0o600"


def test_verify_token_constant_time_comparison_uses_compare_digest(monkeypatch, isolated_token_path):
    """Smoke check that compare_digest is invoked (not == comparison) —
    guards against a regression that would expose timing attacks."""
    from auto_whisper_service.app import create_app
    from auto_whisper_service import auth

    create_app()
    valid = isolated_token_path.read_text().strip()

    calls = {"count": 0}

    real_compare = auth.secrets.compare_digest

    def spy(a, b):
        calls["count"] += 1
        return real_compare(a, b)

    monkeypatch.setattr(auth.secrets, "compare_digest", spy)
    assert auth.verify_token(valid) is True
    assert calls["count"] == 1
