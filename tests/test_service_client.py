"""
Tests for auto_whisper.service_client.

Strategy: use httpx.MockTransport to simulate service responses without
booting a real server or doing ASGI plumbing. This isolates client
behavior (how it interprets responses, handles errors) from service
behavior (which is covered by tests/test_service.py).
"""

from pathlib import Path

import httpx
import pytest

from auto_whisper.service_client import ServiceClient
from auto_whisper_service.auth import AUTH_HEADER


@pytest.fixture
def isolated_token_path(tmp_path: Path, monkeypatch) -> Path:
    """Redirect token storage so the client's get_or_create_token() call
    in __init__ doesn't touch the real Application Support directory."""
    token_file = tmp_path / "service-token"
    monkeypatch.setattr("auto_whisper_service.config.TOKEN_FILE", token_file)
    monkeypatch.setattr("auto_whisper_service.auth.TOKEN_FILE", token_file)
    return token_file


def _make_client(handler, isolated_token_path) -> ServiceClient:
    """Build a ServiceClient backed by a MockTransport.

    handler: callable(request) -> httpx.Response
    """
    sc = ServiceClient()
    # Swap the real localhost-targeting client for one routed through MockTransport.
    auth_headers = {AUTH_HEADER: sc._client.headers[AUTH_HEADER]}
    sc._client.close()
    sc._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=sc.base_url,
        headers=auth_headers,
        timeout=2.0,
    )
    return sc


# --- health() ---

def test_health_returns_true_on_200_with_ok_status(isolated_token_path):
    def handler(req):
        return httpx.Response(200, json={"status": "ok"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.health() is True
    finally:
        sc.close()


def test_health_returns_false_on_non_200(isolated_token_path):
    def handler(req):
        return httpx.Response(503, json={"status": "down"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.health() is False
    finally:
        sc.close()


def test_health_returns_false_on_unexpected_payload(isolated_token_path):
    def handler(req):
        return httpx.Response(200, json={"status": "weird"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.health() is False
    finally:
        sc.close()


def test_health_returns_false_on_connection_error(isolated_token_path):
    def handler(req):
        raise httpx.ConnectError("connection refused")

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.health() is False
    finally:
        sc.close()


# --- version() ---

def test_version_returns_dict_on_200(isolated_token_path):
    payload = {"service": "auto-whisper-service", "version": "0.1.0", "schema_version": 1}

    def handler(req):
        return httpx.Response(200, json=payload)

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.version() == payload
    finally:
        sc.close()


def test_version_returns_none_on_401(isolated_token_path):
    def handler(req):
        return httpx.Response(401, json={"detail": "invalid or missing auth token"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.version() is None
    finally:
        sc.close()


def test_version_returns_none_on_connection_error(isolated_token_path):
    def handler(req):
        raise httpx.ConnectError("connection refused")

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.version() is None
    finally:
        sc.close()


# --- auth header propagation ---

def test_client_sends_auth_header(isolated_token_path):
    captured = {}

    def handler(req):
        captured["token"] = req.headers.get(AUTH_HEADER)
        return httpx.Response(200, json={"status": "ok"})

    sc = _make_client(handler, isolated_token_path)
    try:
        sc.health()
    finally:
        sc.close()

    expected = isolated_token_path.read_text().strip()
    assert captured["token"] == expected
    assert len(captured["token"]) >= 40


# --- context manager ---

def test_context_manager_closes_underlying_client(isolated_token_path):
    def handler(req):
        return httpx.Response(200, json={"status": "ok"})

    sc = _make_client(handler, isolated_token_path)
    with sc as ctx:
        assert ctx is sc
        assert ctx.health() is True
    assert sc._client.is_closed


# --- transcribe() ---

def test_transcribe_returns_dict_on_200(isolated_token_path):
    payload = {"text": "hola", "language": "es", "duration_s": 0.42, "cleaned": False}

    def handler(req):
        assert req.url.path == "/transcribe"
        assert req.method == "POST"
        return httpx.Response(200, json=payload)

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.transcribe(b"fake-wav-bytes") == payload
    finally:
        sc.close()


def test_transcribe_sends_multipart_with_audio_field(isolated_token_path):
    captured = {}

    def handler(req):
        captured["content_type"] = req.headers.get("content-type", "")
        captured["body"] = req.read()
        return httpx.Response(200, json={"text": "x", "language": "es", "duration_s": 0.0, "cleaned": False})

    sc = _make_client(handler, isolated_token_path)
    try:
        sc.transcribe(b"WAVbytes_here", filename="dictation.wav")
    finally:
        sc.close()

    assert captured["content_type"].startswith("multipart/form-data")
    # The form should mention our audio field name and filename:
    assert b'name="audio"' in captured["body"]
    assert b"dictation.wav" in captured["body"]
    assert b"WAVbytes_here" in captured["body"]


def test_transcribe_includes_language_field_when_provided(isolated_token_path):
    captured = {}

    def handler(req):
        captured["body"] = req.read()
        return httpx.Response(200, json={"text": "x", "language": "en", "duration_s": 0.0, "cleaned": False})

    sc = _make_client(handler, isolated_token_path)
    try:
        sc.transcribe(b"audio", language="en")
    finally:
        sc.close()

    assert b'name="language"' in captured["body"]
    assert b"en" in captured["body"]


def test_transcribe_omits_language_field_when_none(isolated_token_path):
    captured = {}

    def handler(req):
        captured["body"] = req.read()
        return httpx.Response(200, json={"text": "x", "language": None, "duration_s": 0.0, "cleaned": False})

    sc = _make_client(handler, isolated_token_path)
    try:
        sc.transcribe(b"audio", language=None)
    finally:
        sc.close()

    assert b'name="language"' not in captured["body"]


def test_transcribe_returns_none_on_400(isolated_token_path):
    def handler(req):
        return httpx.Response(400, json={"detail": "invalid WAV"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.transcribe(b"garbage") is None
    finally:
        sc.close()


def test_transcribe_returns_none_on_401(isolated_token_path):
    def handler(req):
        return httpx.Response(401, json={"detail": "invalid token"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.transcribe(b"audio") is None
    finally:
        sc.close()


def test_transcribe_returns_none_on_502(isolated_token_path):
    def handler(req):
        return httpx.Response(502, json={"detail": "upstream failure"})

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.transcribe(b"audio") is None
    finally:
        sc.close()


def test_transcribe_returns_none_on_connection_error(isolated_token_path):
    def handler(req):
        raise httpx.ConnectError("connection refused")

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.transcribe(b"audio") is None
    finally:
        sc.close()


def test_transcribe_returns_none_on_invalid_json(isolated_token_path):
    def handler(req):
        return httpx.Response(200, content=b"<!DOCTYPE html>not json")

    sc = _make_client(handler, isolated_token_path)
    try:
        assert sc.transcribe(b"audio") is None
    finally:
        sc.close()
